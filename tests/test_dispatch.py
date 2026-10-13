"""Behavior tests for dispatch, persistence, validation, and atomic acceptance."""

from concurrent.futures import ThreadPoolExecutor
from math import pi
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def create_booking(client, pickup, **overrides):
    response = client.post("/api/bookings", json={**pickup, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def accept(client, booking_id, driver_id):
    return client.post(f"/api/bookings/{booking_id}/accept", json={"driver_id": driver_id})


def position_driver(client, driver_id, latitude, longitude, available=True):
    response = client.patch(
        f"/api/drivers/{driver_id}",
        json={"latitude": latitude, "longitude": longitude, "available": available},
    )
    assert response.status_code == 200, response.text
    return response.json()


def isolate_fleet(client):
    """Make matching tests independent of the seeded demo geography."""
    for driver in client.get("/api/drivers").json():
        response = client.patch(f"/api/drivers/{driver['id']}", json={"available": False})
        assert response.status_code == 200, response.text


def candidate(booking):
    assert booking["eligible_drivers"]
    return booking["eligible_drivers"][0]["driver_id"]


def test_health_and_seeded_state(client):
    assert client.get("/health").json() == {"status": "ok"}
    state = client.get("/api/state").json()
    assert len(state["drivers"]) == 12
    assert sum(d["vehicle_type"] == "bike" for d in state["drivers"]) == 6
    assert sum(d["vehicle_type"] == "auto" for d in state["drivers"]) == 6
    assert sum(d["status"] == "offline" for d in state["drivers"]) == 2
    assert state["bookings"] == []
    assert state["settings"] == {"default_radius_km": 2, "offer_ttl_seconds": 90}
    assert state["stats"]["total_bookings"] == 0


def test_booking_defaults_and_nearest_first_matching(client, pickup):
    booking = create_booking(client, pickup)
    assert booking["status"] == "pending"
    assert booking["radius_km"] == 2
    assert booking["rider_name"] == "Guest rider"
    assert booking["pickup_label"] == "Custom pickup"
    assert booking["driver_id"] is None
    assert booking["assigned_driver"] is None
    distances = [d["distance_km"] for d in booking["eligible_drivers"]]
    assert distances == sorted(distances)
    assert all(0 <= distance <= 2 for distance in distances)
    fleet = {d["id"]: d for d in client.get("/api/drivers").json()}
    assert all(fleet[d["driver_id"]]["vehicle_type"] == "bike" for d in booking["eligible_drivers"])
    assert all(fleet[d["driver_id"]]["status"] == "available" for d in booking["eligible_drivers"])


def test_matching_excludes_wrong_category_offline_and_outside_radius(client, pickup):
    isolate_fleet(client)
    position_driver(client, "bike-01", 12.9726, 77.5946)
    position_driver(client, "bike-02", 13.0016, 77.5946)
    position_driver(client, "bike-03", 12.9716, 77.5946, available=False)
    position_driver(client, "auto-01", 12.9716, 77.5946)
    booking = create_booking(client, pickup)
    assert [d["driver_id"] for d in booking["eligible_drivers"]] == ["bike-01"]


def test_search_radius_is_configurable(client, pickup):
    isolate_fleet(client)
    position_driver(client, "bike-01", 12.9916, 77.5946)
    narrow = create_booking(client, pickup, radius_km=2)
    wide = create_booking(client, pickup, radius_km=3)
    assert narrow["eligible_drivers"] == []
    assert [d["driver_id"] for d in wide["eligible_drivers"]] == ["bike-01"]


def test_radius_boundary_just_inside_and_outside(client, pickup):
    isolate_fleet(client)
    # One degree of latitude is approximately 111.2 km. The +/- 1 metre
    # separation makes this a real boundary check without floating-point ties.
    delta_inside = (1.999 / 6371.0088) * (180 / pi)
    delta_boundary = (2.0 / 6371.0088) * (180 / pi)
    delta_outside = (2.001 / 6371.0088) * (180 / pi)
    position_driver(client, "bike-01", pickup["pickup_lat"] + delta_inside, pickup["pickup_lng"])
    position_driver(client, "bike-02", pickup["pickup_lat"] + delta_outside, pickup["pickup_lng"])
    position_driver(client, "bike-03", pickup["pickup_lat"] + delta_boundary, pickup["pickup_lng"])
    booking = create_booking(client, pickup)
    assert [d["driver_id"] for d in booking["eligible_drivers"]] == ["bike-01", "bike-03"]


def test_haversine_matches_across_antimeridian(client, pickup):
    isolate_fleet(client)
    position_driver(client, "bike-01", 0, -179.995)
    booking = create_booking(client, pickup, pickup_lat=0, pickup_lng=179.995)
    assert [d["driver_id"] for d in booking["eligible_drivers"]] == ["bike-01"]
    assert 1.10 < booking["eligible_drivers"][0]["distance_km"] < 1.12


def test_driver_at_pickup_has_zero_distance(client, pickup):
    isolate_fleet(client)
    position_driver(client, "bike-01", pickup["pickup_lat"], pickup["pickup_lng"])
    booking = create_booking(client, pickup)
    assert booking["eligible_drivers"] == [{"driver_id": "bike-01", "distance_km": 0}]


def test_pending_booking_can_gain_new_candidates(client, pickup):
    isolate_fleet(client)
    booking = create_booking(client, pickup)
    assert booking["status"] == "pending"
    assert booking["eligible_drivers"] == []
    position_driver(client, "bike-01", pickup["pickup_lat"], pickup["pickup_lng"])
    current = client.get(f"/api/bookings/{booking['id']}").json()
    assert [d["driver_id"] for d in current["eligible_drivers"]] == ["bike-01"]


def test_accept_assigns_booking_and_reserves_driver(client, pickup):
    booking = create_booking(client, pickup)
    driver_id = candidate(booking)
    response = accept(client, booking["id"], driver_id)
    assert response.status_code == 200, response.text
    assigned = response.json()
    assert assigned["status"] == "assigned"
    assert assigned["driver_id"] == driver_id
    assert assigned["assigned_driver"]["id"] == driver_id
    assert assigned["assigned_driver"]["status"] == "busy"
    assert assigned["eligible_drivers"] == []
    next_booking = create_booking(client, pickup)
    assert driver_id not in [d["driver_id"] for d in next_booking["eligible_drivers"]]


def test_booking_acceptance_can_succeed_only_once(client, pickup):
    booking = create_booking(client, pickup)
    first, second = [d["driver_id"] for d in booking["eligible_drivers"][:2]]
    assert accept(client, booking["id"], first).status_code == 200
    assert accept(client, booking["id"], second).status_code == 409
    assert accept(client, booking["id"], first).status_code == 409
    current = client.get(f"/api/bookings/{booking['id']}").json()
    assert current["driver_id"] == first


def test_one_driver_cannot_accept_two_bookings(client, pickup):
    first = create_booking(client, pickup)
    second = create_booking(client, pickup)
    driver_id = candidate(first)
    assert accept(client, first["id"], driver_id).status_code == 200
    assert accept(client, second["id"], driver_id).status_code == 409
    assert client.get(f"/api/bookings/{second['id']}").json()["status"] == "pending"


@pytest.mark.parametrize("vehicle_type", ["bike", "auto"])
def test_each_vehicle_category_can_be_dispatched(client, pickup, vehicle_type):
    booking = create_booking(client, pickup, vehicle_type=vehicle_type)
    assigned = accept(client, booking["id"], candidate(booking))
    assert assigned.status_code == 200
    assert assigned.json()["assigned_driver"]["vehicle_type"] == vehicle_type


def test_wrong_vehicle_offline_and_distant_acceptance_rejected(client, pickup):
    isolate_fleet(client)
    position_driver(client, "auto-01", pickup["pickup_lat"], pickup["pickup_lng"])
    position_driver(client, "bike-01", 13.0716, 77.5946)
    position_driver(client, "bike-02", pickup["pickup_lat"], pickup["pickup_lng"], available=False)
    booking = create_booking(client, pickup)
    for driver_id in ("auto-01", "bike-01", "bike-02"):
        assert accept(client, booking["id"], driver_id).status_code == 409


def test_eligibility_is_rechecked_when_accepting(client, pickup):
    booking = create_booking(client, pickup)
    driver_id = candidate(booking)
    position_driver(client, driver_id, 13.5, 77.5946)
    assert accept(client, booking["id"], driver_id).status_code == 409


def test_cancel_pending_booking(client, pickup):
    booking = create_booking(client, pickup)
    response = client.post(f"/api/bookings/{booking['id']}/cancel", json={"reason": "Plans changed"})
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["driver_id"] is None
    assert accept(client, booking["id"], candidate(booking)).status_code == 409
    assert client.post(f"/api/bookings/{booking['id']}/cancel", json={}).status_code == 409


def test_cancel_assigned_booking_releases_driver_and_preserves_history(client, pickup):
    booking = create_booking(client, pickup)
    driver_id = candidate(booking)
    assert accept(client, booking["id"], driver_id).status_code == 200
    cancelled = client.post(f"/api/bookings/{booking['id']}/cancel", json={}).json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["driver_id"] == driver_id
    assert cancelled["assigned_driver"]["id"] == driver_id
    next_booking = create_booking(client, pickup)
    assert accept(client, next_booking["id"], driver_id).status_code == 200


def test_complete_releases_driver_and_increments_stats(client, pickup):
    booking = create_booking(client, pickup)
    driver_id = candidate(booking)
    assert accept(client, booking["id"], driver_id).status_code == 200
    response = client.post(f"/api/bookings/{booking['id']}/complete")
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["driver_id"] == driver_id
    state = client.get("/api/state").json()
    assert next(d for d in state["drivers"] if d["id"] == driver_id)["status"] == "available"
    assert state["stats"]["completed_rides"] == 1
    assert state["stats"]["active_bookings"] == 0
    assert client.post(f"/api/bookings/{booking['id']}/complete").status_code == 409
    assert client.post(f"/api/bookings/{booking['id']}/cancel", json={}).status_code == 409


def test_pending_booking_cannot_complete(client, pickup):
    booking = create_booking(client, pickup)
    assert client.post(f"/api/bookings/{booking['id']}/complete").status_code == 409


def test_offer_expires_at_90_seconds(client, pickup, clock):
    booking = create_booking(client, pickup)
    clock.advance(89)
    assert client.get(f"/api/bookings/{booking['id']}").json()["status"] == "pending"
    clock.advance(1)
    expired = client.get(f"/api/bookings/{booking['id']}").json()
    assert expired["status"] == "expired"
    assert expired["eligible_drivers"] == []
    assert accept(client, booking["id"], candidate(booking)).status_code == 409
    assert client.post(f"/api/bookings/{booking['id']}/cancel", json={}).status_code == 409


def test_accept_operation_itself_expires_stale_offer(client, pickup, clock):
    booking = create_booking(client, pickup)
    clock.advance(91)
    assert accept(client, booking["id"], candidate(booking)).status_code == 409
    assert client.get(f"/api/bookings/{booking['id']}").json()["status"] == "expired"


def test_assigned_booking_does_not_expire(client, pickup, clock):
    booking = create_booking(client, pickup)
    driver_id = candidate(booking)
    assert accept(client, booking["id"], driver_id).status_code == 200
    clock.advance(3600)
    assert client.get(f"/api/bookings/{booking['id']}").json()["status"] == "assigned"


def test_busy_driver_cannot_be_moved_or_taken_offline(client, pickup):
    booking = create_booking(client, pickup)
    driver_id = candidate(booking)
    assert accept(client, booking["id"], driver_id).status_code == 200
    for payload in ({"available": False}, {"latitude": 13.0}, {"longitude": 77.6}):
        response = client.patch(f"/api/drivers/{driver_id}", json=payload)
        assert response.status_code == 409, response.text


def test_driver_filters(client):
    response = client.get("/api/drivers", params={"vehicle_type": "auto", "status": "available"})
    assert response.status_code == 200
    assert response.json()
    assert all(d["vehicle_type"] == "auto" and d["status"] == "available" for d in response.json())


@pytest.mark.parametrize(
    "invalid",
    [
        {"pickup_lat": 91},
        {"pickup_lat": -91},
        {"pickup_lng": 181},
        {"pickup_lng": -181},
        {"pickup_lat": "NaN"},
        {"pickup_lng": "Infinity"},
        {"vehicle_type": "car"},
        {"radius_km": 0.24},
        {"radius_km": 10.01},
        {"rider_name": "a" * 1000},
        {"pickup_label": "a" * 1000},
    ],
)
def test_invalid_booking_inputs_rejected(client, pickup, invalid):
    response = client.post("/api/bookings", json={**pickup, **invalid})
    assert response.status_code == 422, response.text
    assert client.get("/api/bookings").json() == []


@pytest.mark.parametrize("nonfinite", ["NaN", "Infinity", "1e999"])
@pytest.mark.parametrize("target", ["pickup", "radius", "driver"])
def test_raw_nonfinite_numbers_return_json_validation_errors(client, nonfinite, target):
    # httpx's json= encoding rejects NaN itself, so send the raw JSON that an
    # external client could send. Even the error response must be valid JSON.
    if target == "driver":
        response = client.patch(
            "/api/drivers/bike-01",
            content='{"latitude":' + nonfinite + "}",
            headers={"Content-Type": "application/json"},
        )
    else:
        field = "pickup_lat" if target == "pickup" else "radius_km"
        body = '{"pickup_lat":12.9716,"pickup_lng":77.5946,"vehicle_type":"bike"'
        if field == "pickup_lat":
            body = '{"pickup_lng":77.5946,"vehicle_type":"bike"'
        body += ',"' + field + '":' + nonfinite + "}"
        response = client.post(
            "/api/bookings", content=body, headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 422, response.text
    assert isinstance(response.json()["detail"], list)
    assert client.get("/api/bookings").json() == []


def test_missing_required_fields_rejected(client):
    assert client.post("/api/bookings", json={}).status_code == 422


@pytest.mark.parametrize("radius", [0.25, 10])
def test_radius_limits_are_inclusive(client, pickup, radius):
    assert create_booking(client, pickup, radius_km=radius)["radius_km"] == radius


def test_unknown_ids_return_404(client, pickup):
    assert client.get("/api/bookings/does-not-exist").status_code == 404
    assert client.post("/api/bookings/does-not-exist/accept", json={"driver_id": "bike-01"}).status_code == 404
    assert client.post("/api/bookings/does-not-exist/cancel", json={}).status_code == 404
    assert client.post("/api/bookings/does-not-exist/complete").status_code == 404
    assert client.patch("/api/drivers/does-not-exist", json={"available": True}).status_code == 404
    booking = create_booking(client, pickup)
    assert accept(client, booking["id"], "does-not-exist").status_code == 404


def test_events_record_dispatch_lifecycle(client, pickup):
    booking = create_booking(client, pickup)
    driver_id = candidate(booking)
    assert accept(client, booking["id"], driver_id).status_code == 200
    assert client.post(f"/api/bookings/{booking['id']}/complete").status_code == 200
    events = client.get("/api/events").json()
    booking_events = [event for event in events if event["booking_id"] == booking["id"]]
    assert len(booking_events) >= 3
    assert [event["id"] for event in events] == sorted([event["id"] for event in events], reverse=True)
    assert any(event["driver_id"] == driver_id for event in booking_events)


def test_simulation_moves_drivers_and_keeps_valid_coordinates(client):
    before = {driver["id"]: driver for driver in client.get("/api/drivers").json()}
    response = client.post("/api/simulation/tick", json={"seconds": 5})
    assert response.status_code == 200, response.text
    after = response.json()["drivers"]
    assert any(
        (d["latitude"], d["longitude"]) != (before[d["id"]]["latitude"], before[d["id"]]["longitude"])
        for d in after if d["status"] == "available"
    )
    for driver in after:
        assert -90 <= driver["latitude"] <= 90
        assert -180 <= driver["longitude"] <= 180
        if driver["status"] == "offline":
            assert driver["latitude"] == before[driver["id"]]["latitude"]
            assert driver["longitude"] == before[driver["id"]]["longitude"]


def test_assigned_driver_moves_toward_pickup_without_completing_ride(client, pickup):
    booking = create_booking(client, pickup)
    driver_id = candidate(booking)
    assigned = accept(client, booking["id"], driver_id).json()
    before = assigned["assigned_driver"]
    distance_squared_before = (before["latitude"] - pickup["pickup_lat"]) ** 2 + (
        before["longitude"] - pickup["pickup_lng"]
    ) ** 2
    state = client.post("/api/simulation/tick", json={"seconds": 5}).json()
    after = next(d for d in state["drivers"] if d["id"] == driver_id)
    distance_squared_after = (after["latitude"] - pickup["pickup_lat"]) ** 2 + (
        after["longitude"] - pickup["pickup_lng"]
    ) ** 2
    assert distance_squared_after < distance_squared_before
    assert after["status"] == "busy"
    assert next(b for b in state["bookings"] if b["id"] == booking["id"])["status"] == "assigned"


@pytest.mark.parametrize("seconds", [0, 61])
def test_simulation_rejects_invalid_tick_lengths(client, seconds):
    assert client.post("/api/simulation/tick", json={"seconds": seconds}).status_code == 422


def test_reset_restores_seed_and_clears_bookings(client, pickup):
    initial = client.get("/api/drivers").json()
    booking = create_booking(client, pickup)
    assert accept(client, booking["id"], candidate(booking)).status_code == 200
    assert client.post("/api/simulation/tick", json={"seconds": 5}).status_code == 200
    response = client.post("/api/demo/reset")
    assert response.status_code == 200
    state = response.json()
    assert state["bookings"] == []
    assert state["drivers"] == initial
    assert state["stats"]["total_bookings"] == 0
    assert state["stats"]["completed_rides"] == 0
    assert any("reset" in event["type"].lower() for event in state["events"])


def test_bookings_and_driver_reservations_persist_across_restart(db_path, clock, pickup):
    with TestClient(create_app(db_path=db_path, clock=clock)) as first:
        booking = create_booking(first, pickup)
        driver_id = candidate(booking)
        assert accept(first, booking["id"], driver_id).status_code == 200
    with TestClient(create_app(db_path=db_path, clock=clock)) as restarted:
        current = restarted.get(f"/api/bookings/{booking['id']}").json()
        assert current["status"] == "assigned"
        assert current["driver_id"] == driver_id
        assert current["assigned_driver"]["status"] == "busy"
        assert len(restarted.get("/api/drivers").json()) == 12
        assert len(restarted.get("/api/bookings").json()) == 1


def race_accept(client_a, client_b, booking_a, driver_a, booking_b, driver_b):
    barrier = Barrier(2)

    def attempt(client, booking_id, driver_id):
        barrier.wait(timeout=5)
        return accept(client, booking_id, driver_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_a = executor.submit(attempt, client_a, booking_a, driver_a)
        future_b = executor.submit(attempt, client_b, booking_b, driver_b)
        return [future_a.result(timeout=10), future_b.result(timeout=10)]


def test_concurrent_drivers_racing_for_one_booking(db_path, clock, pickup):
    # Distinct app instances mean distinct store connections/locks. A barrier
    # starts both requests together and exercises the database's atomic guard.
    with TestClient(create_app(db_path=db_path, clock=clock)) as first, TestClient(
        create_app(db_path=db_path, clock=clock)
    ) as second:
        booking = create_booking(first, pickup)
        driver_a, driver_b = [d["driver_id"] for d in booking["eligible_drivers"][:2]]
        results = race_accept(first, second, booking["id"], driver_a, booking["id"], driver_b)
        assert sorted(response.status_code for response in results) == [200, 409]
        assigned = first.get(f"/api/bookings/{booking['id']}").json()
        assert assigned["driver_id"] in {driver_a, driver_b}
        fleet = first.get("/api/drivers").json()
        assert len([d for d in fleet if d["status"] == "busy"]) == 1


def test_concurrent_bookings_racing_for_one_driver(db_path, clock, pickup):
    with TestClient(create_app(db_path=db_path, clock=clock)) as first, TestClient(
        create_app(db_path=db_path, clock=clock)
    ) as second:
        booking_a = create_booking(first, pickup)
        booking_b = create_booking(first, pickup)
        driver_id = candidate(booking_a)
        results = race_accept(first, second, booking_a["id"], driver_id, booking_b["id"], driver_id)
        assert sorted(response.status_code for response in results) == [200, 409]
        bookings = first.get("/api/bookings").json()
        assert len([b for b in bookings if b["status"] == "assigned"]) == 1
        assert len([b for b in bookings if b["status"] == "pending"]) == 1
