# ZENIT demonstration

Start the project using the README and open `http://127.0.0.1:8000`.
The project runs with no API keys or network map service.

## Three-minute walkthrough

1. **Initial fleet:** show the twelve seeded drivers, bike/auto markers, and
   availability. Explain that the map is an offline simulation of central
   Bengaluru.
2. **Create a request:** select bike, keep the central pickup and 2 km radius,
   and select **Find my driver**. Show nearest-first matching with distances and the
   90-second offer expiry.
3. **Accept:** select **Accept ride** for an eligible driver in the **Driver inbox**. Show the booking become assigned and
   the driver become busy. Only one driver can accept; busy drivers are removed
   from candidates for other rides.
4. **Cancel and reuse:** cancel that assigned booking. Show the driver become
   available and the history retain the assigned driver. Create another request
   and accept it with the released driver.
5. **Movement and completion:** select **Start movement** to move driver markers. An
   assigned driver moves toward pickup. Complete the ride and show the completed
   count and driver's availability update.
6. **Category and radius:** create an auto request to show category-specific
   matching, then adjust the radius to show its effect on eligible drivers.
7. **Proof:** open `http://127.0.0.1:8000/docs` and run `python -m pytest -q`.
   Point out the two concurrency tests in `tests/test_dispatch.py`.

Use **Reset simulation** to restore a fresh fleet before another demonstration.
Reset deletes demo bookings, restores the initial drivers, and logs a reset
event. The API equivalent is `POST /api/demo/reset`.

## API-only demonstration

Keep the server running and use a second terminal with the virtual environment
activated:

```bash
python scripts/demo.py --reset
```

The script prints each HTTP status and route. It verifies creation (`201`),
acceptance (`200`), duplicate rejection (`409`), cancellation, driver reuse,
movement, completion, and final stats. `--reset` clears demo data first; omit it
to use the existing fleet.

## Submission artifacts

- [Repository README](../README.md) includes setup, requirements, API, and design choices.
- [Dashboard screenshot](screenshots/dashboard.jpg) shows the working interface.
- [Driver inbox](screenshots/driver-inbox.jpg), [assigned ride](screenshots/assigned-ride.jpg),
  and [mobile view](screenshots/mobile.jpg) show the acceptance flow and responsive layout.
- [Automated test report](TEST_REPORT.md) records executed checks.
- [API contract](API_CONTRACT.md) records the implemented data model and routes.

The screenshot is a static illustration of a running demo. The repository and
local walkthrough are the functional proof; no hosted deployment is required.
