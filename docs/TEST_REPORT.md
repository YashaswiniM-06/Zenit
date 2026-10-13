# Verification report

Verified on 13 October 2026 using Python 3.9.6 on macOS. The Python 3.11 CI
workflow is included; its remote result depends on the GitHub Actions run.

## Automated tests

Command:

```bash
.venv/bin/python -m pytest -q
```

Executed result:

```text
.........................................................                [100%]
57 passed in 0.68s
```

| Area | Verified behavior |
| --- | --- |
| Matching | Correct category, available drivers only, nearest first, no candidates still pending |
| Geography | Configurable radius, exact 2 km inclusion, 1 metre inside/outside, zero distance, antimeridian |
| Assignment | Booking and driver status update together, repeat acceptance rejected, busy driver excluded |
| Concurrency | Independent app/SQLite connections race for one booking and for one driver; exactly one winner |
| Cancellation | Pending and assigned cancellation, driver release, preserved history, terminal-state guards |
| Completion | Assigned-only completion, driver release, completed count, repeated completion rejected |
| Expiry | Pending at 89 seconds, expired at 90 seconds, stale acceptance rejected, assigned ride remains active |
| Validation | Coordinate limits; raw JSON `NaN`, `Infinity`, and overflow (`1e999`) return JSON `422` errors; category, radius bounds, text lengths, missing inputs, unknown IDs |
| Persistence | Assigned booking and busy reservation survive application restart without duplicate seeding |
| Simulation | Available drivers move; offline drivers stay still; assigned driver approaches pickup without auto-completion |
| Reset | Fleet restored, bookings cleared, counters cleared, reset event recorded |
| Events | Dispatch lifecycle recorded, driver identity included, latest events first |

Tests use temporary databases and an injected clock. Concurrency tests use a
barrier to begin both acceptance attempts simultaneously against separate
application instances sharing one SQLite file. They do not depend on one
in-process application lock.

## Running-server API demonstration

An isolated Uvicorn server was started with a temporary SQLite database on
`127.0.0.1:8012`. This command completed successfully:

```bash
.venv/bin/python scripts/demo.py --base-url http://127.0.0.1:8012 --reset
```

Observed statuses: health `200`, reset `200`, booking creation `201`, acceptance
`200`, repeated acceptance `409`, cancellation `200`, second creation `201`,
released-driver acceptance `200`, movement `200`, completion `200`, final state
`200`. Final counters were 10 available drivers, zero active bookings, one
completed ride, and two total bookings. The proof server was stopped afterward.

The Docker image and health check are provided but were not built in this
environment because the Docker executable was unavailable.

## Frontend regression tests

Command: `node --test tests/frontend.test.cjs` (Node.js 24.20.0).
Executed result: **6 passed, 0 failed**. No third-party JavaScript packages are
needed. Controlled deferred responses verify that old polling/movement snapshots
cannot overwrite a mutation, polling and movement never overlap, concurrent
refresh callers await the same result, commands remain locked until a fresh
snapshot arrives, and busy-driver controls remain disabled after unlocking.
`node --check static/app.js` also passes.

## Browser and layout verification

The running dashboard at `127.0.0.1:8010` was exercised in the Codex in-app
browser. Desktop layout was checked at 1440 × 1000 and mobile at 390 × 844.
The mobile document's scroll width and viewport width were both 390 pixels
(no horizontal overflow). Temporary browser sizing was restored after testing.

Verified through UI controls:

- Create bike and auto requests, enter a rider name, and choose MG Road and
  Cubbon Park presets. The map also updates coordinates on click.
- Increase the search radius to 10 km: the nearby bike count changed from four
  to five. The default remains 2 km.
- Accept a driver; observe assigned booking, busy driver, and reduced available
  count. Busy-driver availability controls remain disabled.
- Cancel an assigned ride; observe the driver return to available. Cancel a
  pending auto request on mobile.
- Complete an assigned auto ride; observe the completed counter increase and
  the driver become available again.
- Toggle a free driver offline and online; observe fleet and statistics update.
- Start and pause movement; observe changed positions and simulation events.
- Reset via the confirmation dialog; observe all bookings cleared and the
  original fleet restored.

No browser console errors or warnings were observed in the final session.
Screenshots capture actual running UI state:

- [Dashboard](screenshots/dashboard.jpg)
- [Driver inbox](screenshots/driver-inbox.jpg)
- [Assigned ride](screenshots/assigned-ride.jpg)
- [Mobile driver inbox](screenshots/mobile.jpg)
