"""SQLite storage with atomic dispatch operations and database-enforced invariants."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Iterator, Optional, Union

from app.errors import DispatchError

SCHEMA = """
CREATE TABLE IF NOT EXISTS drivers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    vehicle_type TEXT NOT NULL CHECK (vehicle_type IN ('bike', 'auto')),
    latitude REAL NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude REAL NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    status TEXT NOT NULL CHECK (status IN ('available', 'busy', 'offline')),
    heading REAL NOT NULL DEFAULT 0 CHECK (heading >= 0 AND heading < 360),
    simulation_phase REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS bookings (
    id TEXT PRIMARY KEY,
    rider_name TEXT NOT NULL,
    pickup_label TEXT NOT NULL,
    pickup_lat REAL NOT NULL CHECK (pickup_lat BETWEEN -90 AND 90),
    pickup_lng REAL NOT NULL CHECK (pickup_lng BETWEEN -180 AND 180),
    vehicle_type TEXT NOT NULL CHECK (vehicle_type IN ('bike', 'auto')),
    radius_km REAL NOT NULL CHECK (radius_km BETWEEN 0.25 AND 10),
    status TEXT NOT NULL CHECK (status IN ('pending', 'assigned', 'cancelled', 'completed', 'expired')),
    driver_id TEXT REFERENCES drivers(id),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    CHECK (status != 'assigned' OR driver_id IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_booking_per_driver
    ON bookings(driver_id) WHERE status = 'assigned';
CREATE INDEX IF NOT EXISTS bookings_status_expiry ON bookings(status, expires_at);
CREATE INDEX IF NOT EXISTS bookings_created ON bookings(created_at DESC);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    message TEXT NOT NULL,
    booking_id TEXT,
    driver_id TEXT,
    created_at REAL NOT NULL
);
"""


class Database:
    """A connection per application, protected across FastAPI worker threads.

    BEGIN IMMEDIATE also coordinates independently running app instances against
    the same file. SQLite reserves its write lock before any eligibility check,
    so another process cannot accept a stale booking or driver in between.
    """

    def __init__(self, path: Union[str, Path]):
        self.path = str(path)
        self._connection: Optional[sqlite3.Connection] = None
        self._lock = RLock()

    def open(self) -> None:
        with self._lock:
            if self._connection is not None:
                return
            if self.path != ":memory:":
                Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                str(Path(self.path).expanduser()) if self.path != ":memory:" else self.path,
                timeout=10,
                isolation_level=None,
                check_same_thread=False,
            )
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA busy_timeout = 10000")
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(SCHEMA)
            except BaseException:
                connection.close()
                raise
            self._connection = connection

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            if self._connection is None:
                raise RuntimeError("Database is not open. Start the app lifespan first.")
            connection = self._connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except DispatchError:
                # Rejected commands make no domain writes. Preserve the expiry
                # sweep performed before validation, even when an ID/transition
                # is invalid, so every API operation advances expired offers.
                connection.commit()
                raise
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
