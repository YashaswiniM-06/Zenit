"""Geographic helpers independent of the API and persistence layer."""

from math import asin, atan2, cos, degrees, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return great-circle distance in kilometers, including across the dateline."""
    phi1, phi2 = radians(lat1), radians(lat2)
    delta_phi = phi2 - phi1
    delta_lambda = radians(lng2 - lng1)
    value = sin(delta_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(delta_lambda / 2) ** 2
    # Floating point arithmetic may put antipodal coordinates just above 1.
    return 2 * EARTH_RADIUS_KM * asin(sqrt(min(1.0, max(0.0, value))))


def bearing_degrees(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return the initial compass bearing from one coordinate to another."""
    phi1, phi2 = radians(lat1), radians(lat2)
    delta_lambda = radians(lng2 - lng1)
    y = sin(delta_lambda) * cos(phi2)
    x = cos(phi1) * sin(phi2) - sin(phi1) * cos(phi2) * cos(delta_lambda)
    return (degrees(atan2(y, x)) + 360) % 360


def wrap_longitude(value: float) -> float:
    return (value + 180) % 360 - 180
