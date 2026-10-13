# Live pilot implementation contract

The existing simulator remains at `/demo` with its existing `/api/*` routes and
SQLite synthetic data. `/` becomes the real rider/driver app, using `/api/live/*`.
Never mix real users or GPS into the demo store. A PostgreSQL database (Supabase)
stores live pilot accounts, sessions, drivers, rides and events in a private
`zenit` schema; no public Data API grants. Local tests use SQLite through the same
SQLAlchemy models. Production must fail closed if live database is unavailable.

## Backend API (JSON detail errors)

Authentication is application-managed email/password stored with scrypt hashes,
using Supabase PostgreSQL as storage. This avoids relying on Supabase's restricted
built-in email sender. Login/signup create HTTP-only same-site session cookies;
store token hashes only, expire sessions after 7 days. Check Origin/Fetch-Site for
unsafe cookie-auth requests. Argon2/scrypt password handling, constant time checks,
generic login failures and rate limiting. No email required for initial account
creation; email verification/password recovery requires optional SMTP config.

- `GET /api/live/config`: `{enabled, maps_enabled, email_enabled,
  geoapify_key: string|null, default_radius_km:2, offer_ttl_seconds:180,
  deployment_mode:"pilot", location_stale_seconds:90}`. Only public map key.
- `POST /api/live/auth/signup`: `{name,email,password,role:"rider"|"driver"}` ->
  `{user}` (201). Password 10–128 chars. User: `{id,name,email,role}`.
- `POST /api/live/auth/login`: `{email,password}` -> `{user}`.
- `POST /api/live/auth/logout`: 204, revoke current session.
- `GET /api/live/auth/me`: `{user}` or401.
- `POST /api/live/auth/forgot-password`: `{email}` generic response; SMTP reset
  link when configured; never expose token. If SMTP disabled return503 explanatory.
- `POST /api/live/auth/reset-password`: `{token,password}` -> generic success;
  one-time reset token hash, short expiry, revoke all old sessions.
- `GET /api/live/state`: authenticated, `{user,driver:Driver|null,rides:Ride[],
  offers:Ride[],events:Event[]}` scoped to the user. Rider sees only own rides;
  driver sees assigned/history and eligible pending offers. No unrelated PII.
- `PUT /api/live/driver/profile`: driver only `{vehicle_type:"bike"|"auto",
  vehicle_number}` -> Driver. Driver: `{id,user_id,name,vehicle_type,
  vehicle_number,latitude:null|float,longitude:null|float,status:"offline"|
  "available"|"busy",last_seen_at:null|ISO}`. Busy vehicle cannot change.
- `POST /api/live/driver/location`: driver only `{latitude,longitude,accuracy?}`
  -> Driver; update location while busy allowed, never alter reserved status.
- `POST /api/live/driver/availability`: `{available:boolean}` -> Driver; going
  online requires position updated in last90s; offline busy409.
- `POST /api/live/rides`: rider only `{pickup_lat,pickup_lng,pickup_label,
  dropoff_lat,dropoff_lng,dropoff_label,vehicle_type,radius_km?:2}` -> Ride201.
  One active ride per rider. Radius .25–10km. Real offers expire180s.
- `GET /api/live/rides/{id}` -> Ride authorized participants only.
- `POST /api/live/rides/{id}/accept`: driver only emptybody -> Ride; atomic first
  acceptance, revalidate type, radius, fresh GPS and availability.
- `POST /api/live/rides/{id}/start`: assigned driver only, assigned->in_progress.
- `POST /api/live/rides/{id}/complete`: assigned driver only, in_progress->completed.
- `POST /api/live/rides/{id}/cancel`: rider owner or assigned driver, pending or
  assigned only. Release driver. Cannot cancel after trip started.
- `GET /api/live/geocode?q=...`: authenticated -> `{results:[{label,latitude,
  longitude}]}` Geoapify proxy; max5, bounded query, timeout, no URL override.
- `GET /api/live/route?pickup_lat=...&pickup_lng=...&dropoff_lat=...&dropoff_lng=...`:
  authenticated -> `{coordinates:[[lng,lat],...],distance_meters,duration_seconds}`;
  Geoapify drive routing. Clearly estimate, not fare or motorcycle routing.

Ride: `{id,rider_id,rider_name,driver_id:null|string,driver:Driver|null,
pickup_lat,pickup_lng,pickup_label,dropoff_lat,dropoff_lng,dropoff_label,
vehicle_type,radius_km,status:"pending"|"assigned"|"in_progress"|"completed"|
"cancelled"|"expired",created_at,updated_at,expires_at}`. Expose rider name only
to eligible drivers in offers (no email). Event: `{id,type,message,ride_id,
created_at}`. Driver data shown to rider only once assigned.

## Configuration

`DATABASE_URL` PostgreSQL via Supabase; `GEOAPIFY_API_KEY`; `APP_BASE_URL` public
HTTPS URL; `LIVE_ENABLED=true`; `COOKIE_SECURE=true` on Render (false locally).
SMTP optional: `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USERNAME`,
`SMTP_PASSWORD` (Gmail app password), `SMTP_FROM`. Use STARTTLS. No credentials
in Git, logs, public config, errors, or rendered HTML. `.env` local ignored.

## Frontend

Independent new `static/live.html`, `static/live.css`, `static/live.js`. Existing
demo assets remain untouched except demo title/clear navigation link. Use vendored
Leaflet for real map tiles from Geoapify and marker updates, fail clearly and keep
coordinate inputs functional if map unavailable. No API keys hardcoded.
Auth signup/login, role onboarding, rider pickup/dropoff search/manual coordinates
and geolocation, category/radius, create/cancel and status; driver vehicle setup,
GPS start/stop permission, availability, nearby offers accept/start/complete.
Poll state every4s while visible, location updates throttle10s and freshness90s.
Pause GPS/logout gracefully. No background tracking promise. Optional browser
notifications for incoming offers after explicit permission. Password recovery
UI only when email enabled. Demo link to `/demo`. Mobile first and accessible.

## Ownership

Backend agent owns `app/live*` and `app/main.py` integration. Frontend agent owns
`static/live*` and vendored Leaflet. Root owns requirements/deploy/docs/tests
coordination. Existing 63 tests must remain green.
