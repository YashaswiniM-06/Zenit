"""Participant-scoped live dispatch and account/session lifecycle."""

from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, select

from app.errors import DispatchError
from app.geo import haversine_km
from app.live_auth import (
    RESET_TTL_SECONDS, SESSION_TTL_SECONDS, hash_password, hash_token, new_token,
    verify_password,
)
from app.live_db import LiveDriver, LiveEvent, LiveRide, LiveSession, LiveUser, PasswordReset
from app.service import iso_utc

LIVE_OFFER_TTL_SECONDS = 180
LOCATION_STALE_SECONDS = 90
ACTIVE_RIDE_STATUSES = ("pending", "assigned", "in_progress")


class LiveService:
    def __init__(self, database, settings, clock=None):
        self.database = database
        self.settings = settings
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def now(self):
        value = self.clock()
        if isinstance(value, datetime):
            return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()
        return float(value)

    @staticmethod
    def _id():
        return uuid4().hex

    @staticmethod
    def user_dict(user):
        return {key: getattr(user, key) for key in ("id", "name", "email", "role")}

    @staticmethod
    def _driver_for_user(session, user_id, lock=False):
        query = select(LiveDriver).where(LiveDriver.user_id == user_id)
        if lock:
            query = query.with_for_update()
        return session.execute(query).scalar_one_or_none()

    def driver_dict(self, session, driver):
        if driver is None:
            return None
        user = session.get(LiveUser, driver.user_id)
        return {
            "id": driver.id, "user_id": driver.user_id, "name": user.name,
            "vehicle_type": driver.vehicle_type, "vehicle_number": driver.vehicle_number,
            "latitude": driver.latitude, "longitude": driver.longitude, "status": driver.status,
            "last_seen_at": iso_utc(driver.last_seen_at) if driver.last_seen_at is not None else None,
        }

    def ride_dict(self, session, ride):
        rider = session.get(LiveUser, ride.rider_id)
        driver = session.get(LiveDriver, ride.driver_id) if ride.driver_id else None
        result = {key: getattr(ride, key) for key in (
            "id", "rider_id", "driver_id", "pickup_lat", "pickup_lng", "pickup_label",
            "dropoff_lat", "dropoff_lng", "dropoff_label", "vehicle_type", "radius_km", "status",
        )}
        result.update(
            rider_name=rider.name, driver=self.driver_dict(session, driver),
            created_at=iso_utc(ride.created_at), updated_at=iso_utc(ride.updated_at), expires_at=iso_utc(ride.expires_at),
        )
        return result

    def _event(self, session, user_id, event_type, message, now, ride_id=None):
        session.add(LiveEvent(user_id=user_id, type=event_type, message=message, created_at=now, ride_id=ride_id))

    def _ride_event(self, session, ride, event_type, message, now):
        self._event(session, ride.rider_id, event_type, message, now, ride.id)
        if ride.driver_id:
            driver = session.get(LiveDriver, ride.driver_id)
            self._event(session, driver.user_id, event_type, message, now, ride.id)

    def _expire(self, session, now):
        rides = session.execute(
            select(LiveRide).where(LiveRide.status == "pending", LiveRide.expires_at <= now)
            .with_for_update(skip_locked=True)
        ).scalars().all()
        for ride in rides:
            ride.status = "expired"
            ride.updated_at = now
            self._ride_event(session, ride, "ride.expired", "Ride request expired without a driver acceptance.", now)
        stale = session.execute(
            select(LiveDriver).where(
                LiveDriver.status == "available",
                (LiveDriver.last_seen_at.is_(None)) | (LiveDriver.last_seen_at < now - LOCATION_STALE_SECONDS),
            ).with_for_update(skip_locked=True)
        ).scalars().all()
        for driver in stale:
            driver.status = "offline"
            self._event(session, driver.user_id, "driver.location_stale", "Driver went offline because GPS updates stopped.", now)
        session.execute(delete(LiveSession).where(LiveSession.expires_at <= now))

    @contextmanager
    def operation(self):
        if not self.settings.enabled:
            raise DispatchError(503, "Live pilot is not configured on this server. The simulator is available at /demo.")
        failure = None
        with self.database.transaction() as session:
            now = self.now()
            self._expire(session, now)
            try:
                yield session, now
            except DispatchError as error:
                # Only expiry changes precede a rejected command. Preserve the
                # sweep, while unexpected failures still roll back everything.
                failure = error
        if failure is not None:
            raise failure

    def sweep(self):
        if self.settings.enabled and self.database.ready:
            with self.database.transaction() as session:
                self._expire(session, self.now())

    def authenticate(self, token):
        if not token or len(token) > 200:
            raise DispatchError(401, "Sign in to continue.")
        with self.operation() as (session, now):
            login_session = session.get(LiveSession, hash_token(token))
            if login_session is None or login_session.expires_at <= now:
                raise DispatchError(401, "Your session has expired. Sign in again.")
            return self.user_dict(session.get(LiveUser, login_session.user_id))

    def _new_session(self, session, user_id, now):
        token = new_token()
        session.add(LiveSession(token_hash=hash_token(token), user_id=user_id, created_at=now, expires_at=now + SESSION_TTL_SECONDS))
        return token

    def signup(self, body):
        password_hash = hash_password(body.password)
        with self.operation() as (session, now):
            if session.execute(select(LiveUser.id).where(LiveUser.email == body.email)).first():
                raise DispatchError(409, "An account with this email already exists. Sign in or recover your password.")
            user = LiveUser(id=self._id(), name=body.name, email=body.email, password_hash=password_hash, role=body.role, created_at=now)
            session.add(user)
            session.flush()
            token = self._new_session(session, user.id, now)
            self._event(session, user.id, "account.created", "Your pilot account is ready.", now)
            return self.user_dict(user), token

    def login(self, body):
        with self.operation() as (session, now):
            user = session.execute(select(LiveUser).where(LiveUser.email == body.email)).scalar_one_or_none()
            valid = verify_password(body.password, user.password_hash if user else None)
            if not valid or user is None:
                raise DispatchError(401, "Email or password is incorrect.")
            return self.user_dict(user), self._new_session(session, user.id, now)

    def logout(self, token, user):
        with self.operation() as (session, now):
            session.execute(delete(LiveSession).where(LiveSession.token_hash == hash_token(token)))
            if user["role"] == "driver":
                driver = self._driver_for_user(session, user["id"], lock=True)
                if driver and driver.status == "available":
                    driver.status = "offline"

    def forgot_password(self, email):
        with self.operation() as (session, now):
            user = session.execute(select(LiveUser).where(LiveUser.email == email)).scalar_one_or_none()
            if user is None:
                return None
            token = new_token()
            session.execute(delete(PasswordReset).where(PasswordReset.user_id == user.id))
            session.add(PasswordReset(token_hash=hash_token(token), user_id=user.id, created_at=now, expires_at=now + RESET_TTL_SECONDS))
            return token

    def reset_password(self, token, password):
        password_hash = hash_password(password)
        with self.operation() as (session, now):
            reset = session.execute(select(PasswordReset).where(PasswordReset.token_hash == hash_token(token)).with_for_update()).scalar_one_or_none()
            if reset is None or reset.consumed_at is not None or reset.expires_at <= now:
                raise DispatchError(400, "This password reset link is invalid or expired. Request a new link.")
            user = session.execute(select(LiveUser).where(LiveUser.id == reset.user_id).with_for_update()).scalar_one()
            user.password_hash = password_hash
            reset.consumed_at = now
            session.execute(delete(LiveSession).where(LiveSession.user_id == user.id))
            driver = self._driver_for_user(session, user.id, lock=True)
            if driver and driver.status == "available":
                driver.status = "offline"
            self._event(session, user.id, "account.password_reset", "Password changed; all previous sessions were signed out.", now)

    @staticmethod
    def _require_role(user, role):
        if user["role"] != role:
            raise DispatchError(403, f"This action requires a {role} account.")

    @staticmethod
    def _fresh(driver, now):
        return (driver.latitude is not None and driver.longitude is not None and driver.last_seen_at is not None
                and driver.last_seen_at >= now - LOCATION_STALE_SECONDS)

    def _eligible(self, driver, ride, now):
        return (driver is not None and driver.status == "available" and self._fresh(driver, now)
                and ride.status == "pending" and ride.expires_at > now and driver.vehicle_type == ride.vehicle_type
                and haversine_km(driver.latitude, driver.longitude, ride.pickup_lat, ride.pickup_lng) <= ride.radius_km + 1e-9)

    def state(self, user):
        with self.operation() as (session, now):
            driver = self._driver_for_user(session, user["id"]) if user["role"] == "driver" else None
            if user["role"] == "rider":
                query = select(LiveRide).where(LiveRide.rider_id == user["id"])
            else:
                query = select(LiveRide).where(LiveRide.driver_id == driver.id) if driver else select(LiveRide).where(False)
            rides = session.execute(query.order_by(LiveRide.created_at.desc(), LiveRide.id.desc()).limit(100)).scalars().all()
            offers = []
            if driver and driver.status == "available" and self._fresh(driver, now):
                pending = session.execute(select(LiveRide).where(LiveRide.status == "pending", LiveRide.vehicle_type == driver.vehicle_type).order_by(LiveRide.created_at)).scalars().all()
                offers = [self.ride_dict(session, ride) for ride in pending if self._eligible(driver, ride, now)][:50]
            events = session.execute(select(LiveEvent).where(LiveEvent.user_id == user["id"]).order_by(LiveEvent.id.desc()).limit(100)).scalars().all()
            return {
                "user": user, "driver": self.driver_dict(session, driver),
                "rides": [self.ride_dict(session, ride) for ride in rides], "offers": offers,
                "events": [{"id": event.id, "type": event.type, "message": event.message, "ride_id": event.ride_id,
                            "created_at": iso_utc(event.created_at)} for event in events],
            }

    def driver_profile(self, user, body):
        self._require_role(user, "driver")
        with self.operation() as (session, now):
            # Lock the account too, so concurrent first-time profile creations
            # cannot race the unique user_id constraint on PostgreSQL.
            session.execute(select(LiveUser).where(LiveUser.id == user["id"]).with_for_update()).scalar_one()
            driver = self._driver_for_user(session, user["id"], lock=True)
            if driver and driver.status == "busy":
                raise DispatchError(409, "Vehicle details cannot change during an active ride.")
            if driver is None:
                driver = LiveDriver(id=self._id(), user_id=user["id"], vehicle_type=body.vehicle_type, vehicle_number=body.vehicle_number, status="offline")
                session.add(driver)
            else:
                driver.vehicle_type = body.vehicle_type
                driver.vehicle_number = body.vehicle_number
            session.flush()
            self._event(session, user["id"], "driver.profile_updated", "Vehicle profile saved.", now)
            return self.driver_dict(session, driver)

    def driver_location(self, user, body):
        self._require_role(user, "driver")
        with self.operation() as (session, now):
            driver = self._driver_for_user(session, user["id"], lock=True)
            if driver is None:
                raise DispatchError(409, "Save your vehicle profile before sharing location.")
            driver.latitude, driver.longitude = body.latitude, body.longitude
            driver.accuracy, driver.last_seen_at = body.accuracy, now
            return self.driver_dict(session, driver)

    def driver_availability(self, user, body):
        self._require_role(user, "driver")
        with self.operation() as (session, now):
            driver = self._driver_for_user(session, user["id"], lock=True)
            if driver is None:
                raise DispatchError(409, "Save your vehicle profile first.")
            if driver.status == "busy":
                raise DispatchError(409, "Complete or cancel your assigned ride before changing availability.")
            if body.available and not self._fresh(driver, now):
                raise DispatchError(409, "Share a fresh GPS location before going online.")
            driver.status = "available" if body.available else "offline"
            self._event(session, user["id"], "driver.availability", f"You are now {'online' if body.available else 'offline'}.", now)
            return self.driver_dict(session, driver)

    def create_ride(self, user, body):
        self._require_role(user, "rider")
        with self.operation() as (session, now):
            session.execute(select(LiveUser).where(LiveUser.id == user["id"]).with_for_update()).scalar_one()
            if session.execute(select(LiveRide.id).where(LiveRide.rider_id == user["id"], LiveRide.status.in_(ACTIVE_RIDE_STATUSES))).first():
                raise DispatchError(409, "You already have an active ride. Finish or cancel it before requesting another.")
            ride = LiveRide(id=self._id(), rider_id=user["id"], status="pending", created_at=now, updated_at=now,
                            expires_at=now + LIVE_OFFER_TTL_SECONDS, **body.model_dump())
            session.add(ride)
            session.flush()
            self._ride_event(session, ride, "ride.requested", f"Requested a {ride.vehicle_type} from {ride.pickup_label} to {ride.dropoff_label}.", now)
            return self.ride_dict(session, ride)

    @staticmethod
    def _get_ride(session, ride_id, lock=False):
        query = select(LiveRide).where(LiveRide.id == ride_id)
        if lock:
            query = query.with_for_update()
        ride = session.execute(query).scalar_one_or_none()
        if ride is None:
            raise DispatchError(404, "Ride not found.")
        return ride

    def _participant(self, session, user, ride):
        if ride.rider_id == user["id"]:
            return True
        driver = session.get(LiveDriver, ride.driver_id) if ride.driver_id else None
        return driver is not None and driver.user_id == user["id"]

    def get_ride(self, user, ride_id):
        with self.operation() as (session, now):
            ride = self._get_ride(session, ride_id)
            if not self._participant(session, user, ride):
                raise DispatchError(404, "Ride not found.")
            return self.ride_dict(session, ride)

    def accept_ride(self, user, ride_id):
        self._require_role(user, "driver")
        with self.operation() as (session, now):
            ride = self._get_ride(session, ride_id, lock=True)
            driver = self._driver_for_user(session, user["id"], lock=True)
            if not self._eligible(driver, ride, now):
                raise DispatchError(409, "Offer is unavailable. Check vehicle category, GPS freshness, availability, and pickup radius.")
            if session.execute(select(LiveRide.id).where(LiveRide.driver_id == driver.id, LiveRide.status.in_(("assigned", "in_progress")))).first():
                raise DispatchError(409, "You already have an active ride.")
            ride.status, ride.driver_id, ride.updated_at = "assigned", driver.id, now
            driver.status = "busy"
            session.flush()
            self._ride_event(session, ride, "ride.accepted", f"{user['name']} accepted the ride.", now)
            return self.ride_dict(session, ride)

    def transition_ride(self, user, ride_id, action):
        with self.operation() as (session, now):
            ride = self._get_ride(session, ride_id, lock=True)
            driver = session.execute(select(LiveDriver).where(LiveDriver.id == ride.driver_id).with_for_update()).scalar_one_or_none() if ride.driver_id else None
            assigned_driver = driver is not None and driver.user_id == user["id"]
            if action == "cancel":
                if ride.rider_id != user["id"] and not assigned_driver:
                    raise DispatchError(404, "Ride not found.")
                if ride.status not in ("pending", "assigned"):
                    raise DispatchError(409, "Only a pending or assigned ride can be cancelled. A started trip must be completed.")
                ride.status = "cancelled"
                message = "Ride cancelled; the reserved driver has been released."
            else:
                if not assigned_driver:
                    raise DispatchError(404, "Ride not found.")
                expected = "assigned" if action == "start" else "in_progress"
                if ride.status != expected:
                    raise DispatchError(409, f"This action requires a ride with status {expected}.")
                ride.status = "in_progress" if action == "start" else "completed"
                message = "Trip started." if action == "start" else "Trip completed; thank you for riding with ZENIT."
            ride.updated_at = now
            if driver and action in ("cancel", "complete"):
                driver.status = "available" if self._fresh(driver, now) else "offline"
            self._ride_event(session, ride, "ride." + ("cancelled" if action == "cancel" else "started" if action == "start" else "completed"), message, now)
            return self.ride_dict(session, ride)
