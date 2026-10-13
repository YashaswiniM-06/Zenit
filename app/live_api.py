"""Session-authenticated API for the real pilot, with fixed-origin map proxies."""

import httpx
from fastapi import APIRouter, BackgroundTasks, Body, Depends, Query, Request, Response
from sqlalchemy import text

from app.errors import DispatchError
from app.live_auth import (
    COOKIE_NAME, clear_session_cookie, guard_unsafe_request, send_password_reset,
    set_session_cookie,
)
from app.live_models import (
    ForgotPassword, LiveAvailability, LiveDriverProfile, LiveLocation, LiveLogin,
    LiveRideCreate, LiveSignup, ResetPassword,
)
from app.live_service import LIVE_OFFER_TTL_SECONDS, LOCATION_STALE_SECONDS
from app.models import EmptyRequest, Latitude, Longitude


def live_router(service, settings, limiter):
    router = APIRouter(prefix="/api/live", tags=["Live pilot"])

    def authenticated(request: Request):
        return service.authenticate(request.cookies.get(COOKIE_NAME))

    def unsafe(request: Request):
        guard_unsafe_request(request, settings)

    def deliver_reset(email, token):
        try:
            send_password_reset(settings, email, token)
        except Exception:
            # Email success/failure must not identify which accounts exist or
            # leak provider messages, tokens, passwords, or transport URLs.
            pass

    @router.get("/config")
    def config():
        return {
            "enabled": settings.enabled, "maps_enabled": settings.maps_enabled,
            "email_enabled": settings.email_enabled, "geoapify_key": settings.geoapify_key,
            "default_radius_km": 2, "offer_ttl_seconds": LIVE_OFFER_TTL_SECONDS,
            "deployment_mode": "pilot", "location_stale_seconds": LOCATION_STALE_SECONDS,
        }

    @router.get("/health")
    def health():
        ready = False
        if settings.enabled and service.database.ready:
            try:
                with service.database.engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                ready = True
            except Exception:
                pass
        if settings.enabled and not ready:
            raise DispatchError(503, "Live database is unavailable.")
        return {"status": "ok" if ready else "disabled", "enabled": settings.enabled, "database_ready": ready}

    @router.post("/auth/signup", status_code=201, dependencies=[Depends(unsafe)])
    def signup(body: LiveSignup, request: Request, response: Response):
        limiter.check(request, body.email, action="signup")
        user, token = service.signup(body)
        set_session_cookie(response, token, settings)
        return {"user": user}

    @router.post("/auth/login", dependencies=[Depends(unsafe)])
    def login(body: LiveLogin, request: Request, response: Response):
        limiter.check(request, body.email, action="login")
        user, token = service.login(body)
        set_session_cookie(response, token, settings)
        return {"user": user}

    @router.get("/auth/me")
    def me(user=Depends(authenticated)):
        return {"user": user}

    @router.post("/auth/logout", status_code=204, dependencies=[Depends(unsafe)])
    def logout(request: Request, user=Depends(authenticated)):
        service.logout(request.cookies.get(COOKIE_NAME), user)
        response = Response(status_code=204)
        clear_session_cookie(response, settings)
        return response

    @router.post("/auth/forgot-password", dependencies=[Depends(unsafe)])
    def forgot_password(body: ForgotPassword, request: Request, tasks: BackgroundTasks):
        limiter.check(request, body.email, action="forgot-password")
        if not settings.email_enabled:
            raise DispatchError(503, "Password recovery is not configured yet. An email delivery provider is required.")
        token = service.forgot_password(body.email)
        if token:
            tasks.add_task(deliver_reset, body.email, token)
        return {"detail": "If an account exists for this email, a password reset link will be sent."}

    @router.post("/auth/reset-password", dependencies=[Depends(unsafe)])
    def reset_password(body: ResetPassword, request: Request, response: Response):
        limiter.check(request, "password-reset", action="reset-password")
        service.reset_password(body.token, body.password)
        clear_session_cookie(response, settings)
        return {"detail": "Password changed. Sign in with your new password."}

    @router.get("/state")
    def state(user=Depends(authenticated)):
        return service.state(user)

    @router.put("/driver/profile", dependencies=[Depends(unsafe)])
    def driver_profile(body: LiveDriverProfile, user=Depends(authenticated)):
        return service.driver_profile(user, body)

    @router.post("/driver/location", dependencies=[Depends(unsafe)])
    def driver_location(body: LiveLocation, user=Depends(authenticated)):
        return service.driver_location(user, body)

    @router.post("/driver/availability", dependencies=[Depends(unsafe)])
    def driver_availability(body: LiveAvailability, user=Depends(authenticated)):
        return service.driver_availability(user, body)

    @router.post("/rides", status_code=201, dependencies=[Depends(unsafe)])
    def create_ride(body: LiveRideCreate, user=Depends(authenticated)):
        return service.create_ride(user, body)

    @router.get("/rides/{ride_id}")
    def get_ride(ride_id: str, user=Depends(authenticated)):
        return service.get_ride(user, ride_id)

    @router.post("/rides/{ride_id}/accept", dependencies=[Depends(unsafe)])
    def accept_ride(ride_id: str, body: EmptyRequest = Body(default=EmptyRequest()), user=Depends(authenticated)):
        return service.accept_ride(user, ride_id)

    @router.post("/rides/{ride_id}/start", dependencies=[Depends(unsafe)])
    def start_ride(ride_id: str, body: EmptyRequest = Body(default=EmptyRequest()), user=Depends(authenticated)):
        return service.transition_ride(user, ride_id, "start")

    @router.post("/rides/{ride_id}/complete", dependencies=[Depends(unsafe)])
    def complete_ride(ride_id: str, body: EmptyRequest = Body(default=EmptyRequest()), user=Depends(authenticated)):
        return service.transition_ride(user, ride_id, "complete")

    @router.post("/rides/{ride_id}/cancel", dependencies=[Depends(unsafe)])
    def cancel_ride(ride_id: str, body: EmptyRequest = Body(default=EmptyRequest()), user=Depends(authenticated)):
        return service.transition_ride(user, ride_id, "cancel")

    def map_request(path, parameters):
        if not settings.geoapify_key:
            raise DispatchError(503, "Map search and route estimates are not configured. You can still enter coordinates.")
        try:
            with httpx.Client(timeout=8, follow_redirects=False) as client:
                result = client.get("https://api.geoapify.com" + path, params={**parameters, "apiKey": settings.geoapify_key})
                result.raise_for_status()
                return result.json()
        except (httpx.HTTPError, ValueError, TypeError):
            raise DispatchError(502, "The map provider is unavailable. Try again or use coordinate inputs.") from None

    @router.get("/geocode")
    def geocode(q: str = Query(min_length=3, max_length=200), user=Depends(authenticated)):
        data = map_request("/v1/geocode/search", {"text": q.strip(), "limit": 5, "format": "geojson"})
        results = []
        for feature in data.get("features", [])[:5]:
            properties = feature.get("properties", {})
            latitude, longitude = properties.get("lat"), properties.get("lon")
            if isinstance(latitude, (int, float)) and isinstance(longitude, (int, float)) and -90 <= latitude <= 90 and -180 <= longitude <= 180:
                results.append({"label": str(properties.get("formatted", "Search result"))[:300], "latitude": latitude, "longitude": longitude})
        return {"results": results}

    @router.get("/route")
    def route(
        pickup_lat: Latitude, pickup_lng: Longitude, dropoff_lat: Latitude, dropoff_lng: Longitude,
        user=Depends(authenticated),
    ):
        data = map_request("/v1/routing", {
            "waypoints": f"{pickup_lat},{pickup_lng}|{dropoff_lat},{dropoff_lng}", "mode": "drive",
        })
        features = data.get("features", [])
        if not features:
            raise DispatchError(404, "No driving route was found for these coordinates.")
        feature = features[0]
        geometry = feature.get("geometry", {})
        coordinates = geometry.get("coordinates", [])
        if geometry.get("type") == "MultiLineString":
            coordinates = [point for line in coordinates for point in line]
        properties = feature.get("properties", {})
        return {
            "coordinates": coordinates[:5000], "distance_meters": properties.get("distance", 0),
            "duration_seconds": properties.get("time", 0),
        }

    return router
