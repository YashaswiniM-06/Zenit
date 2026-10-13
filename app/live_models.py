"""Strict bodies for real user operations; never persisted in the demo store."""

from typing import Literal, Optional

from pydantic import ConfigDict, Field, field_validator

from app.models import Latitude, Longitude, RequestModel, VehicleType


def validated_email(value):
    value = value.strip().casefold()
    if len(value) > 254 or value.count("@") != 1:
        raise ValueError("Enter a valid email address.")
    local, domain = value.split("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("Enter a valid email address.")
    if any(char.isspace() or ord(char) < 33 for char in value):
        raise ValueError("Enter a valid email address.")
    return value


class LiveSignup(RequestModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=128)
    role: Literal["rider", "driver"]

    _email = field_validator("email")(validated_email)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class LiveLogin(RequestModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    _email = field_validator("email")(validated_email)


class ForgotPassword(RequestModel):
    email: str = Field(min_length=3, max_length=254)

    _email = field_validator("email")(validated_email)


class ResetPassword(RequestModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=10, max_length=128)


class LiveDriverProfile(RequestModel):
    vehicle_type: VehicleType
    vehicle_number: str = Field(min_length=3, max_length=20)

    @field_validator("vehicle_number")
    @classmethod
    def vehicle_number_normalize(cls, value):
        value = value.upper()
        if not all(char.isalnum() or char in " -" for char in value):
            raise ValueError("Use letters, digits, spaces, or hyphens for the vehicle number.")
        return value


class LiveLocation(RequestModel):
    latitude: Latitude
    longitude: Longitude
    accuracy: Optional[float] = Field(default=None, ge=0, le=10000, strict=True, allow_inf_nan=False)


class LiveAvailability(RequestModel):
    available: bool = Field(strict=True)


class LiveRideCreate(RequestModel):
    pickup_lat: Latitude
    pickup_lng: Longitude
    pickup_label: str = Field(min_length=1, max_length=160)
    dropoff_lat: Latitude
    dropoff_lng: Longitude
    dropoff_label: str = Field(min_length=1, max_length=160)
    vehicle_type: VehicleType
    radius_km: float = Field(default=2, strict=True, ge=0.25, le=10, allow_inf_nan=False)
