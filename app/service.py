"""Dispatch rules: matching, reservations, cancellation, expiry, and simulation."""

from datetime import datetime, timezone
from math import cos, sin
from typing import Callable, Optional
from uuid import uuid4

from app.database import Database
from app.errors import DispatchError
from app.geo import bearing_degrees, haversine_km, wrap_longitude
from app.models import BookingCreate, DriverUpdate
from app.seed import DEMO_DRIVERS

DEFAULT_RADIUS_KM = 2.0
OFFER_TTL_SECONDS = 90
DRIVER_COLUMNS = ("id", "name", "vehicle_type", "latitude", "longitude", "status", "heading")
BOOKING_COLUMNS = (
    "id", "rider_name", "pickup_label", "pickup_lat", "pickup_lng", "vehicle_type",
    "radius_km", "status", "driver_id", "created_at", "updated_at", "expires_at",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class DispatchService:
    def __init__(self, database: Database, clock: Optional[Callable] = None):
        self.database = database
        self.clock = clock or utc_now

    def _now(self) -> float:
        value = self.clock()
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.timestamp()
        return float(value)

    @staticmethod
    def _event(connection, event_type, message, now, booking_id=None, driver_id=None):
        connection.execute(
            "INSERT INTO events (type, message, booking_id, driver_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (event_type, message, booking_id, driver_id, now),
        )

    def _seed(self, connection, now, reset=False):
        connection.executemany(
            "INSERT INTO drivers (id, name, vehicle_type, latitude, longitude, status, heading) VALUES (?, ?, ?, ?, ?, ?, ?)",
            DEMO_DRIVERS,
        )
        self._event(
            connection,
            "demo.reset" if reset else "demo.ready",
            "Demo reset: 12 drivers are ready around central Bengaluru." if reset
            else "Dispatch ready: 6 bikes and 6 autos are seeded around central Bengaluru.",
            now,
        )

    def initialize(self):
        self.database.open()
        with self.database.transaction() as connection:
            if connection.execute("SELECT COUNT(*) FROM drivers").fetchone()[0] == 0:
                self._seed(connection, self._now())

    def _expire(self, connection, now):
        pending = connection.execute(
            "SELECT id FROM bookings WHERE status = 'pending' AND expires_at <= ? ORDER BY created_at, rowid",
            (now,),
        ).fetchall()
        for booking in pending:
            connection.execute(
                "UPDATE bookings SET status = 'expired', updated_at = ? WHERE id = ? AND status = 'pending'",
                (now, booking["id"]),
            )
            self._event(
                connection, "booking.expired", "Offer expired after 90 seconds without a driver acceptance.",
                now, booking_id=booking["id"],
            )

    def expire_pending(self):
        """Also used before request validation, so malformed requests sweep expiry."""
        with self.database.transaction() as connection:
            self._expire(connection, self._now())

    @staticmethod
    def _find_booking(connection, booking_id):
        booking = connection.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        if booking is None:
            raise DispatchError(404, "Booking not found.")
        return booking

    @staticmethod
    def _find_driver(connection, driver_id):
        driver = connection.execute("SELECT * FROM drivers WHERE id = ?", (driver_id,)).fetchone()
        if driver is None:
            raise DispatchError(404, "Driver not found.")
        return driver

    @staticmethod
    def _driver(driver):
        return {key: driver[key] for key in DRIVER_COLUMNS}

    @staticmethod
    def _eligible(connection, booking):
        if booking["status"] != "pending":
            return []
        drivers = connection.execute(
            "SELECT id, latitude, longitude FROM drivers WHERE vehicle_type = ? AND status = 'available'",
            (booking["vehicle_type"],),
        ).fetchall()
        nearby = []
        for driver in drivers:
            distance = haversine_km(
                booking["pickup_lat"], booking["pickup_lng"], driver["latitude"], driver["longitude"],
            )
            # Compare full precision before rounding the value for display.
            if distance <= booking["radius_km"] + 1e-9:
                nearby.append((distance, driver["id"]))
        nearby.sort()
        return [{"driver_id": driver_id, "distance_km": round(distance, 6)} for distance, driver_id in nearby]

    def _booking(self, connection, booking):
        result = {key: booking[key] for key in BOOKING_COLUMNS}
        for key in ("created_at", "updated_at", "expires_at"):
            result[key] = iso_utc(result[key])
        result["eligible_drivers"] = self._eligible(connection, booking)
        result["assigned_driver"] = None
        if booking["driver_id"]:
            result["assigned_driver"] = self._driver(self._find_driver(connection, booking["driver_id"]))
        return result

    @staticmethod
    def _events(connection):
        rows = connection.execute("SELECT * FROM events ORDER BY id DESC LIMIT 100").fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["created_at"] = iso_utc(event["created_at"])
            events.append(event)
        return events

    def _state(self, connection):
        driver_rows = connection.execute("SELECT * FROM drivers ORDER BY vehicle_type DESC, id").fetchall()
        booking_rows = connection.execute("SELECT * FROM bookings ORDER BY created_at DESC, rowid DESC").fetchall()
        return {
            "drivers": [self._driver(driver) for driver in driver_rows],
            "bookings": [self._booking(connection, booking) for booking in booking_rows],
            "events": self._events(connection),
            "stats": {
                "available_drivers": sum(driver["status"] == "available" for driver in driver_rows),
                "active_bookings": sum(booking["status"] in ("pending", "assigned") for booking in booking_rows),
                "completed_rides": sum(booking["status"] == "completed" for booking in booking_rows),
                "total_bookings": len(booking_rows),
            },
            "settings": {"default_radius_km": DEFAULT_RADIUS_KM, "offer_ttl_seconds": OFFER_TTL_SECONDS},
        }

    def state(self):
        with self.database.transaction() as connection:
            self._expire(connection, self._now())
            return self._state(connection)

    def list_drivers(self, vehicle_type=None, status=None):
        with self.database.transaction() as connection:
            self._expire(connection, self._now())
            rows = connection.execute("SELECT * FROM drivers ORDER BY vehicle_type DESC, id").fetchall()
            return [self._driver(row) for row in rows
                    if (vehicle_type is None or row["vehicle_type"] == vehicle_type)
                    and (status is None or row["status"] == status)]

    def list_bookings(self):
        with self.database.transaction() as connection:
            self._expire(connection, self._now())
            rows = connection.execute("SELECT * FROM bookings ORDER BY created_at DESC, rowid DESC").fetchall()
            return [self._booking(connection, row) for row in rows]

    def get_booking(self, booking_id):
        with self.database.transaction() as connection:
            self._expire(connection, self._now())
            return self._booking(connection, self._find_booking(connection, booking_id))

    def list_events(self):
        with self.database.transaction() as connection:
            self._expire(connection, self._now())
            return self._events(connection)

    def create_booking(self, request: BookingCreate):
        booking_id = "ride-" + uuid4().hex
        with self.database.transaction() as connection:
            now = self._now()
            self._expire(connection, now)
            connection.execute(
                """INSERT INTO bookings
                (id, rider_name, pickup_label, pickup_lat, pickup_lng, vehicle_type, radius_km,
                 status, driver_id, created_at, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ?, ?)""",
                (booking_id, request.rider_name, request.pickup_label, request.pickup_lat, request.pickup_lng,
                 request.vehicle_type, request.radius_km, now, now, now + OFFER_TTL_SECONDS),
            )
            result = self._booking(connection, self._find_booking(connection, booking_id))
            count = len(result["eligible_drivers"])
            self._event(
                connection, "booking.created",
                f"{request.rider_name} requested a {request.vehicle_type} at {request.pickup_label}; "
                f"{count} available driver{'s' if count != 1 else ''} within {request.radius_km:g} km.",
                now, booking_id=booking_id,
            )
            return result

    def accept_booking(self, booking_id, driver_id):
        with self.database.transaction() as connection:
            now = self._now()
            self._expire(connection, now)
            booking = self._find_booking(connection, booking_id)
            driver = self._find_driver(connection, driver_id)
            if booking["status"] != "pending":
                raise DispatchError(409, f"Only pending bookings can be accepted; this booking is {booking['status']}.")
            if driver["vehicle_type"] != booking["vehicle_type"]:
                raise DispatchError(409, "Driver vehicle category does not match this booking.")
            if driver["status"] != "available":
                raise DispatchError(409, "Driver is unavailable or already assigned to another booking.")
            distance = haversine_km(
                booking["pickup_lat"], booking["pickup_lng"], driver["latitude"], driver["longitude"],
            )
            if distance > booking["radius_km"] + 1e-9:
                raise DispatchError(409, "Driver is outside this booking's search radius.")
            # BEGIN IMMEDIATE holds the SQLite write lock throughout the checks
            # and both updates. The partial unique index is an additional guard.
            connection.execute(
                "UPDATE bookings SET status = 'assigned', driver_id = ?, updated_at = ? WHERE id = ? AND status = 'pending'",
                (driver_id, now, booking_id),
            )
            connection.execute("UPDATE drivers SET status = 'busy' WHERE id = ?", (driver_id,))
            self._event(
                connection, "booking.assigned", f"{driver['name']} accepted the ride ({distance:.2f} km from pickup).",
                now, booking_id=booking_id, driver_id=driver_id,
            )
            return self._booking(connection, self._find_booking(connection, booking_id))

    def cancel_booking(self, booking_id, reason="Cancelled by rider"):
        with self.database.transaction() as connection:
            now = self._now()
            self._expire(connection, now)
            booking = self._find_booking(connection, booking_id)
            if booking["status"] not in ("pending", "assigned"):
                raise DispatchError(409, f"A {booking['status']} booking cannot be cancelled.")
            connection.execute(
                "UPDATE bookings SET status = 'cancelled', updated_at = ? WHERE id = ?", (now, booking_id),
            )
            if booking["status"] == "assigned":
                connection.execute("UPDATE drivers SET status = 'available' WHERE id = ?", (booking["driver_id"],))
            self._event(
                connection, "booking.cancelled", f"Ride cancelled: {reason}", now,
                booking_id=booking_id, driver_id=booking["driver_id"],
            )
            return self._booking(connection, self._find_booking(connection, booking_id))

    def complete_booking(self, booking_id):
        with self.database.transaction() as connection:
            now = self._now()
            self._expire(connection, now)
            booking = self._find_booking(connection, booking_id)
            if booking["status"] != "assigned":
                raise DispatchError(409, "Only an assigned ride can be completed.")
            connection.execute(
                "UPDATE bookings SET status = 'completed', updated_at = ? WHERE id = ?", (now, booking_id),
            )
            connection.execute("UPDATE drivers SET status = 'available' WHERE id = ?", (booking["driver_id"],))
            self._event(
                connection, "booking.completed", "Ride completed; the driver is available for another booking.",
                now, booking_id=booking_id, driver_id=booking["driver_id"],
            )
            return self._booking(connection, self._find_booking(connection, booking_id))

    def update_driver(self, driver_id, request: DriverUpdate):
        with self.database.transaction() as connection:
            now = self._now()
            self._expire(connection, now)
            driver = self._find_driver(connection, driver_id)
            if driver["status"] == "busy":
                raise DispatchError(409, "Busy drivers can only be released by cancelling or completing their ride.")
            latitude = driver["latitude"] if request.latitude is None else request.latitude
            longitude = driver["longitude"] if request.longitude is None else request.longitude
            status = driver["status"]
            if request.available is not None:
                status = "available" if request.available else "offline"
            connection.execute(
                "UPDATE drivers SET latitude = ?, longitude = ?, status = ? WHERE id = ?",
                (latitude, longitude, status, driver_id),
            )
            self._event(
                connection, "driver.updated", f"{driver['name']} updated: {status} at {latitude:.4f}, {longitude:.4f}.",
                now, driver_id=driver_id,
            )
            return self._driver(self._find_driver(connection, driver_id))

    def simulation_tick(self, seconds=5):
        with self.database.transaction() as connection:
            now = self._now()
            self._expire(connection, now)
            rows = connection.execute("SELECT * FROM drivers ORDER BY id").fetchall()
            assignments = {
                row["driver_id"]: row for row in connection.execute("SELECT * FROM bookings WHERE status = 'assigned'")
            }
            moved = 0
            for driver in rows:
                if driver["status"] == "offline":
                    continue
                old_lat, old_lng = driver["latitude"], driver["longitude"]
                phase = driver["simulation_phase"]
                if driver["status"] == "busy":
                    booking = assignments.get(driver["id"])
                    if booking is None:
                        continue
                    distance = haversine_km(old_lat, old_lng, booking["pickup_lat"], booking["pickup_lng"])
                    speed_km_per_second = 0.009 if driver["vehicle_type"] == "bike" else 0.007
                    fraction = min(1.0, seconds * speed_km_per_second / distance) if distance > 0 else 0
                    latitude = old_lat + (booking["pickup_lat"] - old_lat) * fraction
                    lng_delta = wrap_longitude(booking["pickup_lng"] - old_lng)
                    longitude = wrap_longitude(old_lng + lng_delta * fraction)
                else:
                    new_phase = phase + seconds * (0.026 if driver["vehicle_type"] == "bike" else 0.018)
                    latitude = max(-90, min(90, old_lat + (sin(new_phase) - sin(phase)) * 0.0006))
                    longitude = wrap_longitude(old_lng + (cos(new_phase) - cos(phase)) * 0.0008)
                    phase = new_phase
                heading = bearing_degrees(old_lat, old_lng, latitude, longitude)
                if abs(old_lat - latitude) + abs(old_lng - longitude) > 1e-12:
                    moved += 1
                else:
                    heading = driver["heading"]
                connection.execute(
                    "UPDATE drivers SET latitude = ?, longitude = ?, heading = ?, simulation_phase = ? WHERE id = ?",
                    (latitude, longitude, heading, phase, driver["id"]),
                )
            self._event(
                connection, "simulation.tick", f"Simulation advanced {seconds} seconds; {moved} drivers moved.", now,
            )
            return self._state(connection)

    def reset_demo(self):
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM events")
            connection.execute("DELETE FROM bookings")
            connection.execute("DELETE FROM drivers")
            self._seed(connection, self._now(), reset=True)
            return self._state(connection)
