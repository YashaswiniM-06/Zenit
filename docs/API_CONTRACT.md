# ZENIT implementation contract

FastAPI serves the static dashboard at `/` and the JSON API under `/api`.
SQLite persists drivers, bookings, and events. All state mutations are transactional;
acceptance must atomically reserve both a pending booking and an available driver.
No API keys, paid map services, or external runtime dependencies beyond Python packages.

## Models

Driver: `id` (string), `name`, `vehicle_type` (`bike` or `auto`), `latitude`,
`longitude`, `status` (`available`, `busy`, `offline`), `heading` (degrees).

Booking: `id` (string), `rider_name`, `pickup_label`, `pickup_lat`, `pickup_lng`,
`vehicle_type`, `radius_km`, `status` (`pending`, `assigned`, `cancelled`,
`completed`, `expired`), `driver_id` (nullable), `created_at`, `updated_at`,
`expires_at` (UTC ISO strings), `eligible_drivers` (array of `{driver_id,
distance_km}` sorted nearest first), `assigned_driver` (Driver or null).
Cancelled/completed history retains the driver identity; only assigned bookings
reserve a driver. An empty candidate set still creates a pending booking; future
nearby available drivers may become eligible before expiry.

Event: `id` (integer), `type`, `message`, `booking_id` (nullable), `driver_id`
(nullable), `created_at`. Most recent first.

## Routes

- `GET /health`: `{status: "ok"}`.
- `GET /api/state`: `{drivers: Driver[], bookings: Booking[], events: Event[],
  stats: {available_drivers, active_bookings, completed_rides, total_bookings},
  settings: {default_radius_km: 2, offer_ttl_seconds: 90}}`.
- `GET /api/drivers`: Driver array; optional vehicle_type/status filters.
- `GET /api/bookings`: Booking array, newest first.
- `GET /api/bookings/{id}`: Booking.
- `GET /api/events`: Event array (up to 100 newest).
- `POST /api/bookings`: `{pickup_lat, pickup_lng, vehicle_type, radius_km?: 2,
  rider_name?: "Guest rider", pickup_label?: "Custom pickup"}` → Booking, 201.
- `POST /api/bookings/{id}/accept`: `{driver_id}` → Booking.
- `POST /api/bookings/{id}/cancel`: `{reason?: "Cancelled by rider"}` → Booking.
- `POST /api/bookings/{id}/complete`: empty body → Booking (assigned only).
- `PATCH /api/drivers/{id}`: `{available?: bool, latitude?: number,
  longitude?: number}` → Driver. Busy drivers cannot be taken offline or moved.
- `POST /api/simulation/tick`: `{seconds?: 5}` (1–60) → state; move available
  drivers deterministically around demo area, assigned drivers toward pickup.
- `POST /api/demo/reset`: empty body → state with initial demo drivers and no
  bookings. Emit a reset event. Local demo operation, documented clearly.

Coordinates are finite and in world range. Radius is 0.25–10 km. Validate lengths
and reject unsupported vehicle types. Unknown IDs → 404. Invalid transitions,
wrong vehicle, unavailable/out-of-range driver, duplicate acceptance → 409.
Input validation → 422. JSON errors use `{detail: string}` (validation may be an
array). Expire pending bookings at 90 seconds on every API operation; use an
injectable clock for tests. Driver distance uses the haversine formula. On cancel
or complete, release assigned driver in the same transaction.

## Demo geography

Central Bengaluru: default pickup `(12.9716, 77.5946)`. Seed 12 drivers, six bikes
and six autos, with most inside 2 km, some outside and one offline of each type.
UI presents an offline SVG street map with distinct bike/auto markers; click map
to set pickup. Map is a simulation, not live geographic tiles or turn-by-turn routing.

## Factory

`app.main.create_app(db_path=None, clock=None)` creates isolated test apps. Default
database path from `ZENIT_DB_PATH`, else `data/zenit.sqlite3`. Seed only empty DB.
`app.main.app` supports `uvicorn app.main:app --reload`. Static files in `static/`.
Avoid import-time writes outside application lifespan. Use TestClient as a context.
