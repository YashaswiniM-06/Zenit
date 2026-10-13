#!/usr/bin/env python3
"""Exercise a running ZENIT server without third-party HTTP clients or API keys."""

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--reset", action="store_true", help="Delete local demo bookings and restore seeded drivers first"
    )
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    def request(method, path, body=None, expected=200):
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"} if body is not None else {}
        try:
            with urlopen(Request(base_url + path, data=data, headers=headers, method=method), timeout=10) as response:
                status = response.status
                result = json.load(response)
        except HTTPError as error:
            status = error.code
            result = json.loads(error.read().decode("utf-8"))
        except URLError as error:
            raise RuntimeError(f"Cannot connect to {base_url}. Start the ZENIT server first: {error.reason}") from error
        if status != expected:
            raise RuntimeError(f"{method} {path}: expected {expected}, received {status}: {result}")
        print(f"{status} {method} {path}")
        return result

    request("GET", "/health")
    if args.reset:
        request("POST", "/api/demo/reset")

    pickup = {
        "pickup_lat": 12.9716,
        "pickup_lng": 77.5946,
        "vehicle_type": "bike",
        "rider_name": "Demo rider",
        "pickup_label": "Central Bengaluru",
    }
    booking = request("POST", "/api/bookings", pickup, expected=201)
    if not booking["eligible_drivers"]:
        request("POST", f"/api/bookings/{booking['id']}/cancel", {"reason": "No demo candidates"})
        raise RuntimeError("No available bikes within 2 km. Use --reset for a fresh demo fleet.")
    driver_id = booking["eligible_drivers"][0]["driver_id"]
    request("POST", f"/api/bookings/{booking['id']}/accept", {"driver_id": driver_id})
    request("POST", f"/api/bookings/{booking['id']}/accept", {"driver_id": driver_id}, expected=409)
    request("POST", f"/api/bookings/{booking['id']}/cancel", {"reason": "Demonstrate driver release"})

    next_booking = request("POST", "/api/bookings", pickup, expected=201)
    request("POST", f"/api/bookings/{next_booking['id']}/accept", {"driver_id": driver_id})
    request("POST", "/api/simulation/tick", {"seconds": 5})
    request("POST", f"/api/bookings/{next_booking['id']}/complete")
    state = request("GET", "/api/state")
    print("\nPASS: matching, acceptance, duplicate rejection, cancellation, driver reuse, movement, completion.")
    print(json.dumps(state["stats"], indent=2))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(f"Demo failed: {error}", file=sys.stderr)
        sys.exit(1)
