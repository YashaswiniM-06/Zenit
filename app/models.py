"""Validated request bodies and documented API response shapes."""

from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

VehicleType = Literal["bike", "auto"]
DriverStatus = Literal["available", "busy", "offline"]
BookingStatus = Literal["pending", "assigned", "cancelled", "completed", "expired"]
Latitude = Annotated[float, Field(strict=True, ge=-90, le=90, allow_inf_nan=False)]
Longitude = Annotated[float, Field(strict=True, ge=-180, le=180, allow_inf_nan=False)]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BookingCreate(RequestModel):
    pickup_lat: Latitude
    pickup_lng: Longitude
    vehicle_type: VehicleType
    radius_km: float = Field(default=2, strict=True, ge=0.25, le=10, allow_inf_nan=False)
    rider_name: str = Field(default="Guest rider", min_length=1, max_length=80)
    pickup_label: str = Field(default="Custom pickup", min_length=1, max_length=120)


class BookingAccept(RequestModel):
    driver_id: str = Field(min_length=1, max_length=80)


class BookingCancel(RequestModel):
    reason: str = Field(default="Cancelled by rider", min_length=1, max_length=240)


class DriverUpdate(RequestModel):
    available: Optional[bool] = Field(default=None, strict=True)
    latitude: Optional[Latitude] = None
    longitude: Optional[Longitude] = None

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("Provide availability or coordinates to update.")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Driver updates cannot contain null values.")
        return self


class SimulationTick(RequestModel):
    seconds: int = Field(default=5, strict=True, ge=1, le=60)


class EmptyRequest(RequestModel):
    pass


class Driver(BaseModel):
    id: str
    name: str
    vehicle_type: VehicleType
    latitude: float
    longitude: float
    status: DriverStatus
    heading: float


class EligibleDriver(BaseModel):
    driver_id: str
    distance_km: float


class Booking(BaseModel):
    id: str
    rider_name: str
    pickup_label: str
    pickup_lat: float
    pickup_lng: float
    vehicle_type: VehicleType
    radius_km: float
    status: BookingStatus
    driver_id: Optional[str]
    created_at: str
    updated_at: str
    expires_at: str
    eligible_drivers: List[EligibleDriver]
    assigned_driver: Optional[Driver]


class Event(BaseModel):
    id: int
    type: str
    message: str
    booking_id: Optional[str]
    driver_id: Optional[str]
    created_at: str


class Stats(BaseModel):
    available_drivers: int
    active_bookings: int
    completed_rides: int
    total_bookings: int


class Settings(BaseModel):
    default_radius_km: float
    offer_ttl_seconds: int


class State(BaseModel):
    drivers: List[Driver]
    bookings: List[Booking]
    events: List[Event]
    stats: Stats
    settings: Settings
