"""FastAPI application factory and the complete ZENIT REST interface."""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, List, Optional, Union

from fastapi import Body, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.database import Database
from app.errors import DispatchError
from app.models import (
    Booking, BookingAccept, BookingCancel, BookingCreate, Driver, DriverStatus,
    DriverUpdate, EmptyRequest, Event, SimulationTick, State, VehicleType,
)
from app.service import DispatchService

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_ROOT = PROJECT_ROOT / "static"


def create_app(
    db_path: Optional[Union[str, Path]] = None,
    clock: Optional[Callable] = None,
) -> FastAPI:
    """Create an isolated application; persistence opens only during lifespan.

    Tests can pass a temporary database path (or ':memory:') and a callable clock
    returning a datetime or Unix timestamp. No database is touched on import.
    """
    path = db_path if db_path is not None else os.getenv("ZENIT_DB_PATH", str(PROJECT_ROOT / "data" / "zenit.sqlite3"))
    database = Database(path)
    service = DispatchService(database, clock)

    @asynccontextmanager
    async def lifespan(application):
        try:
            await run_in_threadpool(service.initialize)
            yield
        finally:
            await run_in_threadpool(database.close)

    application = FastAPI(
        title="ZENIT — Smart Driver Dispatch",
        description=(
            "A local dispatch mini-simulator with category matching, configurable radius, atomic driver "
            "acceptance, ride cancellation, offer expiry, and deterministic movement. Coordinates use "
            "great-circle distance. This demo needs no API keys."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )
    application.state.database = database
    application.state.service = service

    @application.exception_handler(DispatchError)
    async def dispatch_error_handler(request: Request, error: DispatchError):
        return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, error: RequestValidationError):
        # Python's JSON parser accepts NaN/Infinity and overflowing exponents.
        # Pydantic rejects these values, but echoing the raw input in the default
        # error response would itself fail JSON serialization and turn 422 into
        # 500. Return the documented location/type/message without raw input.
        detail = [
            {"type": item["type"], "loc": list(item["loc"]), "msg": item["msg"]}
            for item in error.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": detail})

    @application.middleware("http")
    async def expire_on_api_operation(request: Request, call_next):
        # Run even before body/query validation and unknown /api routes. Each
        # operation repeats the sweep under its own write lock before acting.
        if request.url.path == "/api" or request.url.path.startswith("/api/"):
            await run_in_threadpool(service.expire_pending)
        return await call_next(request)

    @application.get("/health", tags=["Health"])
    def health():
        return {"status": "ok"}

    @application.get("/api/state", response_model=State, tags=["Dispatch"])
    def get_state():
        return service.state()

    @application.get("/api/drivers", response_model=List[Driver], tags=["Drivers"])
    def get_drivers(
        vehicle_type: Optional[VehicleType] = Query(default=None),
        status: Optional[DriverStatus] = Query(default=None),
    ):
        return service.list_drivers(vehicle_type, status)

    @application.patch("/api/drivers/{driver_id}", response_model=Driver, tags=["Drivers"])
    def patch_driver(driver_id: str, request: DriverUpdate):
        return service.update_driver(driver_id, request)

    @application.get("/api/bookings", response_model=List[Booking], tags=["Bookings"])
    def get_bookings():
        return service.list_bookings()

    @application.get("/api/bookings/{booking_id}", response_model=Booking, tags=["Bookings"])
    def get_booking(booking_id: str):
        return service.get_booking(booking_id)

    @application.post("/api/bookings", response_model=Booking, status_code=201, tags=["Bookings"])
    def post_booking(request: BookingCreate):
        return service.create_booking(request)

    @application.post("/api/bookings/{booking_id}/accept", response_model=Booking, tags=["Bookings"])
    def accept_booking(booking_id: str, request: BookingAccept):
        return service.accept_booking(booking_id, request.driver_id)

    @application.post("/api/bookings/{booking_id}/cancel", response_model=Booking, tags=["Bookings"])
    def cancel_booking(booking_id: str, request: BookingCancel = Body(default=BookingCancel())):
        return service.cancel_booking(booking_id, request.reason)

    @application.post("/api/bookings/{booking_id}/complete", response_model=Booking, tags=["Bookings"])
    def complete_booking(booking_id: str, request: EmptyRequest = Body(default=EmptyRequest())):
        return service.complete_booking(booking_id)

    @application.get("/api/events", response_model=List[Event], tags=["Events"])
    def get_events():
        return service.list_events()

    @application.post("/api/simulation/tick", response_model=State, tags=["Simulation"])
    def simulation_tick(request: SimulationTick = Body(default=SimulationTick())):
        return service.simulation_tick(request.seconds)

    @application.post("/api/demo/reset", response_model=State, tags=["Simulation"])
    def reset_demo(request: EmptyRequest = Body(default=EmptyRequest())):
        """Clear local demo history and restore the initial fleet."""
        return service.reset_demo()

    @application.get("/", response_class=FileResponse, include_in_schema=False)
    def dashboard():
        return FileResponse(STATIC_ROOT / "index.html")

    application.mount("/static", StaticFiles(directory=str(STATIC_ROOT), check_dir=False), name="static")
    return application


app = create_app()
