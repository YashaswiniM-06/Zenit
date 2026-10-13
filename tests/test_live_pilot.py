"""End-to-end contracts for the isolated real rider/driver pilot."""

from fastapi.testclient import TestClient

from app.main import create_app


def make_client(monkeypatch):
    monkeypatch.setenv("LIVE_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("APP_BASE_URL", "http://testserver")
    return TestClient(create_app(db_path=":memory:"))


def signup(client, name, email, role):
    response = client.post("/api/live/auth/signup", json={
        "name": name, "email": email, "password": "safe pilot password", "role": role,
    })
    assert response.status_code == 201, response.text
    return response.json()["user"]


def test_rider_driver_accept_and_complete(monkeypatch):
    with make_client(monkeypatch) as rider:
        # Each test client needs the same app/database, so create two cookie jars
        # against the app owned by this client instead of separate test apps.
        app = rider.app
        driver = TestClient(app)
        signup(rider, "Rider", "rider@example.com", "rider")
        signup(driver, "Driver", "driver@example.com", "driver")
        assert driver.put("/api/live/driver/profile", json={"vehicle_type": "bike", "vehicle_number": "KA 01 AB 1234"}).status_code == 200
        assert driver.post("/api/live/driver/location", json={"latitude": 12.9716, "longitude": 77.5946, "accuracy": 8}).status_code == 200
        assert driver.post("/api/live/driver/availability", json={"available": True}).status_code == 200
        created = rider.post("/api/live/rides", json={
            "pickup_lat": 12.9716, "pickup_lng": 77.5946, "pickup_label": "Cubbon Park",
            "dropoff_lat": 12.9816, "dropoff_lng": 77.6046, "dropoff_label": "MG Road",
            "vehicle_type": "bike", "radius_km": 2,
        })
        assert created.status_code == 201, created.text
        ride_id = created.json()["id"]
        assert driver.get("/api/live/state").json()["offers"][0]["id"] == ride_id
        assert driver.post(f"/api/live/rides/{ride_id}/accept", json={}).json()["status"] == "assigned"
        assert driver.post(f"/api/live/rides/{ride_id}/start", json={}).json()["status"] == "in_progress"
        assert driver.post(f"/api/live/rides/{ride_id}/complete", json={}).json()["status"] == "completed"


def test_live_privacy_and_gps_guard(monkeypatch):
    with make_client(monkeypatch) as first:
        app = first.app
        other = TestClient(app)
        driver = TestClient(app)
        signup(first, "First rider", "first@example.com", "rider")
        signup(other, "Other rider", "other@example.com", "rider")
        signup(driver, "Driver", "driver2@example.com", "driver")
        assert driver.put("/api/live/driver/profile", json={"vehicle_type": "auto", "vehicle_number": "KA 02 CD 9876"}).status_code == 200
        assert driver.post("/api/live/driver/availability", json={"available": True}).status_code == 409
        made = first.post("/api/live/rides", json={
            "pickup_lat": 12.97, "pickup_lng": 77.59, "pickup_label": "A",
            "dropoff_lat": 12.98, "dropoff_lng": 77.60, "dropoff_label": "B", "vehicle_type": "auto",
        }).json()
        assert other.get(f"/api/live/rides/{made['id']}").status_code == 404
        assert other.get("/api/live/state").json()["rides"] == []


def test_cross_origin_mutations_are_rejected(monkeypatch):
    with make_client(monkeypatch) as client:
        response = client.post("/api/live/auth/signup", headers={"Origin": "https://evil.example"}, json={
            "name": "Blocked", "email": "blocked@example.com", "password": "safe pilot password", "role": "rider",
        })
        assert response.status_code == 403
