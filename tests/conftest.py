from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


class MutableClock:
    """Keep time-sensitive tests deterministic and free of sleeps."""

    def __init__(self):
        self.now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock():
    return MutableClock()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "zenit-test.sqlite3"


@pytest.fixture
def client(db_path, clock):
    with TestClient(create_app(db_path=db_path, clock=clock)) as test_client:
        yield test_client


@pytest.fixture
def pickup():
    return {"pickup_lat": 12.9716, "pickup_lng": 77.5946, "vehicle_type": "bike"}
