/* ZENIT's dashboard uses the same persistent API as the documented REST demo. */
"use strict";

const $ = (selector) => document.querySelector(selector);
const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );
const titleCase = (value) =>
  String(value).charAt(0).toUpperCase() + String(value).slice(1);
const presets = {
  center: { lat: 12.9716, lng: 77.5946, label: "City center, Bengaluru" },
  park: { lat: 12.9762, lng: 77.5929, label: "Cubbon Park, Bengaluru" },
  mg: { lat: 12.9755, lng: 77.6068, label: "MG Road, Bengaluru" },
};
const ui = {
  state: null,
  vehicle: "bike",
  selected: null,
  filter: "active",
  view: "overview",
  mapCenter: { lat: 12.9716, lng: 77.5946 },
  zoom: 1,
  movement: false,
  tickBusy: false,
  refreshBusy: false,
  mutationBusy: false,
  mapDriver: null,
  stateGeneration: 0,
  refreshPromise: null,
  tickPromise: null,
};

function haversine(lat1, lng1, lat2, lng2) {
  const rad = Math.PI / 180;
  const dLat = (lat2 - lat1) * rad,
    dLng = (lng2 - lng1) * rad;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLng / 2) ** 2;
  return (
    6371.0088 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(Math.max(0, 1 - a)))
  );
}
function pickup() {
  return {
    lat: Number($("#pickup-lat").value),
    lng: Number($("#pickup-lng").value),
  };
}
function currentBooking() {
  return ui.state?.bookings.find((booking) => booking.id === ui.selected);
}
function selectedPickup() {
  const b = currentBooking();
  return b ? { lat: b.pickup_lat, lng: b.pickup_lng } : pickup();
}
function selectedRadius() {
  return currentBooking()?.radius_km ?? Number($("#radius").value);
}
function eligibleDrivers() {
  const p = pickup(),
    radius = Number($("#radius").value);
  return (ui.state?.drivers ?? [])
    .filter((d) => d.status === "available" && d.vehicle_type === ui.vehicle)
    .map((d) => ({
      ...d,
      distance: haversine(p.lat, p.lng, d.latitude, d.longitude),
    }))
    .filter((d) => d.distance <= radius)
    .sort((a, b) => a.distance - b.distance);
}
function toast(message, error = false) {
  const item = document.createElement("div");
  item.className = `toast${error ? " error" : ""}`;
  item.textContent = message;
  $("#toast-region").append(item);
  setTimeout(() => item.remove(), 5500);
}
async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...options.headers },
      signal: controller.signal,
    });
    const data = await response.json();
    if (!response.ok) {
      const detail = Array.isArray(data.detail)
        ? data.detail
            .map((e) => `${e.loc?.slice(1).join(".") || "Input"}: ${e.msg}`)
            .join("; ")
        : data.detail;
      throw new Error(detail || `Request failed (${response.status})`);
    }
    return data;
  } finally {
    clearTimeout(timeout);
  }
}
function setConnected(connected) {
  $("#connection").classList.toggle("disconnected", !connected);
  $("#connection").innerHTML =
    `<i></i>${connected ? "System online" : "Reconnecting"}`;
  $("#connection-error").classList.toggle("hidden", connected);
}
async function refresh(force = false) {
  if (ui.refreshBusy) return ui.refreshPromise;
  if ((ui.mutationBusy && !force) || ui.tickBusy) return;
  ui.refreshBusy = true;
  const generation = ++ui.stateGeneration;
  ui.refreshPromise = (async () => {
    try {
      const state = await api("/api/state");
      if (generation === ui.stateGeneration) {
        ui.state = state;
        setConnected(true);
        render();
      }
    } catch (error) {
      if (generation === ui.stateGeneration) setConnected(false);
    } finally {
      ui.refreshBusy = false;
    }
  })();
  return ui.refreshPromise;
}
async function mutate(path, body, success, method = "POST") {
  if (ui.mutationBusy) return null;
  ui.mutationBusy = true;
  ++ui.stateGeneration;
  setActionButtons(true);
  // Let earlier state requests finish; never let their snapshots replace a mutation.
  await Promise.all([ui.refreshPromise, ui.tickPromise]);
  try {
    const data = await api(path, {
      method,
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    if (success) toast(success);
    return data;
  } catch (error) {
    toast(
      error.name === "AbortError"
        ? "The server took too long. Please try again."
        : error.message,
      true,
    );
    return null;
  } finally {
    await refresh(true);
    ui.mutationBusy = false;
    setActionButtons(false);
  }
}
function setActionButtons(disabled) {
  document
    .querySelectorAll(
      "#create-ride, .accept-button, [data-cancel], [data-complete], [data-availability], #reset-confirm",
    )
    .forEach(
      (button) =>
        (button.disabled = disabled || button.dataset.busy === "true"),
    );
}
function render() {
  if (!ui.state) return;
  if (
    ui.selected &&
    !ui.state.bookings.some((booking) => booking.id === ui.selected)
  )
    ui.selected = null;
  const stats = ui.state.stats;
  $("#stat-drivers").textContent = stats.available_drivers;
  $("#stat-active").textContent = stats.active_bookings;
  $("#stat-completed").textContent = stats.completed_rides;
  $("#stat-total").textContent = stats.total_bookings;
  $("#nav-queue-count").textContent = stats.active_bookings;
  renderMatches();
  renderMap();
  renderQueue();
  renderEvents();
  renderFleet();
  renderBooking();
}
function renderMatches() {
  const drivers = eligibleDrivers();
  $("#match-count").textContent = ui.state
    ? `${drivers.length} ${ui.vehicle} driver${drivers.length === 1 ? "" : "s"} within ${Number($("#radius").value)} km`
    : "Finding nearby drivers…";
  $("#match-description").textContent = drivers.length
    ? `Nearest driver is ${drivers[0].distance.toFixed(2)} km from pickup.`
    : "Requests stay open for 90 seconds while drivers move nearby.";
  const b = currentBooking();
  $("#map-available").textContent = b
    ? (ui.state?.drivers ?? []).filter(
        (d) =>
          d.status === "available" &&
          d.vehicle_type === b.vehicle_type &&
          haversine(b.pickup_lat, b.pickup_lng, d.latitude, d.longitude) <=
            b.radius_km,
      ).length
    : drivers.length;
}
function toPoint(lat, lng) {
  const k = 1000 / (6.5 / ui.zoom);
  return {
    x:
      500 +
      (lng - ui.mapCenter.lng) *
        111.195 *
        Math.cos((ui.mapCenter.lat * Math.PI) / 180) *
        k,
    y: 330 - (lat - ui.mapCenter.lat) * 111.195 * k,
  };
}
function fromPoint(x, y) {
  const km = 6.5 / ui.zoom;
  return {
    lat: ui.mapCenter.lat + (((330 - y) / 1000) * km) / 111.195,
    lng:
      ui.mapCenter.lng +
      (((x - 500) / 1000) * km) /
        (111.195 * Math.cos((ui.mapCenter.lat * Math.PI) / 180)),
  };
}
function basePoint(x, y) {
  const lat = 12.9716 + (((330 - y) / 1000) * 6.5) / 111.195;
  const lng =
    77.5946 +
    (((x - 500) / 1000) * 6.5) /
      (111.195 * Math.cos((12.9716 * Math.PI) / 180));
  return toPoint(lat, lng);
}
function mapLine(points, width, color, extra = "") {
  return `<polyline points="${points
    .map(([x, y]) => {
      const p = basePoint(x, y);
      return `${p.x},${p.y}`;
    })
    .join(
      " ",
    )}" fill="none" stroke="${color}" stroke-width="${width * ui.zoom}" stroke-linecap="round" stroke-linejoin="round" ${extra}/>`;
}
function renderMap() {
  const parts = [
    '<rect width="1000" height="660" fill="#ecefe3"/><rect width="1000" height="660" fill="url(#map-grain)"/>',
  ];
  // A local vector map is deliberately illustrative and available completely offline.
  for (let row = -5; row < 14; row++)
    for (let col = -6; col < 16; col++) {
      const x = col * 94 + (row % 2 ? 14 : 0),
        y = row * 66,
        p = basePoint(x, y);
      const green = Math.abs(row * 17 + col * 11) % 9 === 0;
      const width = (70 + (Math.abs(col * 3 + row) % 9)) * ui.zoom,
        height = (44 + (Math.abs(row * 3 - col) % 7)) * ui.zoom;
      parts.push(
        `<rect x="${p.x}" y="${p.y}" width="${width}" height="${height}" rx="${4 * ui.zoom}" fill="${green ? "#e0e8d4" : "#e4e8dc"}" stroke="#dde3d3" stroke-width="${ui.zoom}"/>`,
      );
      if (!green && ui.zoom > 0.7)
        parts.push(
          `<path d="M${p.x + width * 0.48} ${p.y + 3 * ui.zoom}v${height - 6 * ui.zoom}" stroke="#eaf0e1" stroke-width="${2 * ui.zoom}"/>`,
        );
    }
  const park = basePoint(375, 120);
  parts.push(
    `<rect x="${park.x}" y="${park.y}" width="${170 * ui.zoom}" height="${160 * ui.zoom}" rx="${20 * ui.zoom}" fill="#d9e5c9" stroke="#cfdcbd" stroke-width="2"/>`,
  );
  for (let i = 0; i < 22; i++) {
    const p = basePoint(390 + ((i * 37) % 140), 137 + ((i * 29) % 125));
    parts.push(
      `<circle cx="${p.x}" cy="${p.y}" r="${(5 + (i % 4)) * ui.zoom}" fill="#c9dab7" opacity=".6"/>`,
    );
  }
  const lake = basePoint(803, 437);
  parts.push(
    `<path d="M${lake.x},${lake.y}c${80 * ui.zoom},${-15 * ui.zoom} ${94 * ui.zoom},${50 * ui.zoom} ${65 * ui.zoom},${85 * ui.zoom}s${-110 * ui.zoom},${9 * ui.zoom} ${-97 * ui.zoom},${-26 * ui.zoom}s${-17 * ui.zoom},${-38 * ui.zoom} ${32 * ui.zoom},${-59 * ui.zoom}" fill="#d4e5db" stroke="#c4dacd" stroke-width="2"/>`,
  );
  const roads = [
    [
      [
        [-300, 344],
        [400, 344],
        [580, 276],
        [1350, 276],
      ],
      22,
    ],
    [
      [
        [665, -300],
        [665, 290],
        [611, 433],
        [600, 1000],
      ],
      19,
    ],
    [
      [
        [-100, 42],
        [434, 112],
        [578, 274],
        [757, 372],
        [1260, 376],
      ],
      15,
    ],
    [
      [
        [170, -200],
        [239, 148],
        [231, 498],
        [355, 1000],
      ],
      14,
    ],
    [
      [
        [-300, 531],
        [393, 531],
        [621, 589],
        [1350, 589],
      ],
      18,
    ],
    [
      [
        [408, -300],
        [408, 81],
        [342, 398],
        [405, 1000],
      ],
      13,
    ],
    [
      [
        [-150, 174],
        [319, 174],
        [619, 91],
        [1350, 91],
      ],
      12,
    ],
  ];
  for (const [points, width] of roads) {
    parts.push(mapLine(points, width + 3, "#dce3d3"));
    parts.push(mapLine(points, width, "#fafbf4"));
    parts.push(mapLine(points, 1, "#e5e9dd", 'stroke-dasharray="7 10"'));
  }
  const p = selectedPickup(),
    point = toPoint(p.lat, p.lng),
    radius = (selectedRadius() / 6.5) * 1000 * ui.zoom;
  if (Number.isFinite(point.x) && Number.isFinite(point.y))
    parts.push(
      `<circle cx="${point.x}" cy="${point.y}" r="${radius}" fill="#78a864" fill-opacity=".08" stroke="#8fac74" stroke-opacity=".6" stroke-width="1.4" stroke-dasharray="5 6"/>`,
    );
  const labels = [
    [105, 89, "VASANTH NAGAR"],
    [688, 162, "SHIVAJINAGAR"],
    [445, 211, "CUBBON PARK"],
    [462, 413, "ASHOK NAGAR"],
    [407, 579, "RICHMOND TOWN"],
    [848, 557, "ULSOOR"],
  ];
  for (const [x, y, label] of labels) {
    const l = basePoint(x, y);
    parts.push(
      `<text x="${l.x}" y="${l.y}" text-anchor="middle" fill="#a2ad94" font-size="${Math.max(8, 10 * ui.zoom)}" font-weight="550" letter-spacing="2" class="map-label">${label}</text>`,
    );
  }
  const roadLabel = basePoint(727, 272);
  parts.push(
    `<text x="${roadLabel.x}" y="${roadLabel.y}" fill="#abb59b" font-size="10" letter-spacing=".7" class="map-label">Mahatma Gandhi Rd</text>`,
  );
  const booking = currentBooking(),
    category = booking?.vehicle_type ?? ui.vehicle;
  for (const d of ui.state?.drivers ?? []) {
    const xy = toPoint(d.latitude, d.longitude);
    if (xy.x < -40 || xy.x > 1040 || xy.y < -40 || xy.y > 700) continue;
    const available = d.status === "available",
      color = available
        ? d.vehicle_type === "bike"
          ? "#5e8756"
          : "#c59a50"
        : d.status === "busy"
          ? "#8ca19b"
          : "#afb6a5";
    const match =
      available &&
      d.vehicle_type === category &&
      haversine(p.lat, p.lng, d.latitude, d.longitude) <= selectedRadius();
    const icon =
      d.vehicle_type === "bike"
        ? '<circle cx="-8" cy="4" r="4"/><circle cx="8" cy="4" r="4"/><path d="m-8 4 6-10 5 10H-8l6-6h8l2 6M-4-6h5m4-3h3L6-2"/>'
        : '<path d="M-10 5V-3l4-5H5l6 8v5H-10M-9-3H7M0-8V5"/><circle cx="-6" cy="7" r="2.5"/><circle cx="7" cy="7" r="2.5"/>';
    parts.push(
      `<g class="map-driver" transform="translate(${xy.x},${xy.y})" data-map-driver="${escapeHTML(d.id)}" role="button" tabindex="0" aria-label="${escapeHTML(`${d.name}, ${d.vehicle_type}, ${d.status}`)}"><title>${escapeHTML(`${d.name} · ${d.vehicle_type} · ${d.status}`)}</title>${match ? '<circle r="23" fill="#ffffff80" stroke="#d9e5ce" stroke-width="1"/>' : ""}<circle r="17" fill="${color}" stroke="#fff" stroke-width="2.5" filter="url(#marker-shadow)"/><g fill="none" stroke="white" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round">${icon}</g></g>`,
    );
  }
  if (Number.isFinite(point.x) && Number.isFinite(point.y)) {
    parts.push(
      `<g transform="translate(${point.x},${point.y})" class="map-label"><circle r="29" fill="#375f3c" fill-opacity=".08"/><circle r="21" fill="white" stroke="#d6e3c7" stroke-width="1"/><path d="M0 14s-11-11-11-18a11 11 0 1 1 22 0C11 3 0 14 0 14Z" fill="#375f3c"/><circle cy="-4" r="4" fill="#ecf5df"/><rect x="-36" y="29" width="72" height="21" rx="4" fill="white" stroke="#e0e7d6"/><text y="43" text-anchor="middle" font-size="8" fill="#80916c" font-weight="550">Your pickup</text></g>`,
    );
  }
  if (ui.mapDriver) {
    const d = ui.state?.drivers.find((driver) => driver.id === ui.mapDriver);
    if (d) {
      const xy = toPoint(d.latitude, d.longitude);
      parts.push(
        `<g class="driver-tooltip" transform="translate(${Math.max(80, Math.min(920, xy.x))},${Math.max(75, xy.y - 36)})" pointer-events="none"><rect x="-76" y="-35" width="152" height="42" rx="5" fill="white" stroke="#dfe7d5"/><text y="-18" text-anchor="middle" fill="#6b8159" font-size="10" font-weight="600">${escapeHTML(d.name)}</text><text y="-4" text-anchor="middle" fill="#97a88a" font-size="8">${titleCase(d.vehicle_type)} · ${d.status} · ${haversine(p.lat, p.lng, d.latitude, d.longitude).toFixed(2)} km</text></g>`,
      );
    }
  }
  $("#map-scene").innerHTML = parts.join("");
}
function formatTime(value) {
  return new Date(value).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
function shortId(id) {
  return String(id).slice(-6).toUpperCase();
}
function statusPill(status) {
  return `<span class="status-pill status-${escapeHTML(status)}">${escapeHTML(titleCase(status))}</span>`;
}
function renderQueue() {
  if (!ui.state) return;
  const all = ui.state.bookings;
  const bookings = all.filter(
    (b) =>
      ui.filter === "all" ||
      (ui.filter === "active"
        ? ["pending", "assigned"].includes(b.status)
        : b.status === "completed"),
  );
  $("#queue-total").textContent = all.length;
  if (!bookings.length) {
    $("#queue-content").innerHTML =
      `<div class="empty-state"><span class="empty-icon">${ui.filter === "completed" ? "✓" : "↗"}</span><strong>${ui.filter === "completed" ? "Great rides start here" : "A little quiet. A lot of possibility."}</strong><p>${ui.filter === "completed" ? "Complete an assigned ride to see it here." : "Create a ride request and watch the right driver connect."}</p><button data-new-ride>＋ Create your first ride</button></div>`;
    return;
  }
  $("#queue-content").innerHTML =
    `<table class="queue-table"><thead><tr><th>RIDER / REQUEST</th><th>VEHICLE</th><th>DRIVER</th><th>STATUS</th><th></th></tr></thead><tbody>${bookings.map((b) => `<tr data-booking="${escapeHTML(b.id)}" class="${b.id === ui.selected ? "selected" : ""}" tabindex="0" aria-label="View booking for ${escapeHTML(b.rider_name)}"><td><strong>${escapeHTML(b.rider_name)}</strong><small>#${escapeHTML(shortId(b.id))} · ${escapeHTML(b.pickup_label)}</small></td><td><span class="vehicle-mini ${b.vehicle_type}">${b.vehicle_type}</span></td><td>${escapeHTML(b.assigned_driver?.name ?? "Awaiting match")}</td><td>${statusPill(b.status)}</td><td>↗</td></tr>`).join("")}</tbody></table>`;
}
function renderEvents() {
  const events = ui.state?.events ?? [];
  if (!events.length) {
    $("#event-feed").innerHTML =
      '<div class="empty-state"><span class="empty-icon">◷</span><strong>Ready when you are</strong><p>Requests, acceptances, and cancellations will show up here in real time.</p></div>';
    return;
  }
  const icons = {
    "booking.created": "＋",
    "booking.assigned": "✓",
    "booking.accepted": "✓",
    "booking.cancelled": "×",
    "booking.completed": "✓",
    "booking.expired": "◷",
    "simulation.tick": "↗",
    "demo.reset": "↺",
    "demo.ready": "✦",
    "driver.updated": "♧",
  };
  $("#event-feed").innerHTML = events
    .map(
      (event) =>
        `<div class="event-item"><span class="event-dot">${icons[event.type] ?? "·"}</span><div class="event-text"><strong>${escapeHTML(event.message)}</strong><time datetime="${escapeHTML(event.created_at)}">${escapeHTML(formatTime(event.created_at))}</time></div></div>`,
    )
    .join("");
}
function renderFleet() {
  if (!ui.state) return;
  $("#fleet-total").textContent = `${ui.state.drivers.length} drivers`;
  $("#fleet-content").innerHTML = ui.state.drivers
    .map(
      (d) =>
        `<article class="fleet-card"><span class="driver-avatar">${escapeHTML(
          d.name
            .split(" ")
            .map((x) => x[0])
            .slice(0, 2)
            .join(""),
        )}</span><div class="driver-info"><strong>${escapeHTML(d.name)}</strong><small>${titleCase(d.vehicle_type)} · ${escapeHTML(d.id)} · ${titleCase(d.status)}</small><span class="fleet-coordinate">${d.latitude.toFixed(5)}, ${d.longitude.toFixed(5)}</span><button class="availability-button ${d.status === "offline" ? "offline" : ""}" data-availability="${escapeHTML(d.id)}" data-online="${d.status === "offline"}" data-busy="${d.status === "busy"}" ${d.status === "busy" ? "disabled" : ""}>${d.status === "busy" ? "On an assigned ride" : d.status === "offline" ? "Bring online" : "Take offline"}</button></div></article>`,
    )
    .join("");
}
function renderBooking() {
  const booking = currentBooking();
  $("#request-form-view").classList.toggle("hidden", Boolean(booking));
  $("#booking-detail-view").classList.toggle("hidden", !booking);
  if (!booking) return;
  let content = `<div class="panel-heading"><div><h2>Your ride request ${statusPill(booking.status)}</h2><p>Booking #${escapeHTML(shortId(booking.id))}</p></div><span class="request-icon">↗</span></div><div class="booking-content"><button class="booking-back" data-new-ride>← Create another request</button><div class="booking-summary"><h3>${escapeHTML(booking.rider_name)}</h3><p>⌖ ${escapeHTML(booking.pickup_label)}</p><p>${booking.pickup_lat.toFixed(5)}, ${booking.pickup_lng.toFixed(5)}</p><div class="booking-meta"><span>${titleCase(booking.vehicle_type)} · ${booking.radius_km} km radius</span><span>${formatTime(booking.created_at)}</span></div></div>`;
  if (booking.status === "pending") {
    content += `<div class="countdown">◷ <span>Driver inbox closes in <strong id="booking-countdown"></strong></span></div><div class="driver-list-title">Driver inbox<span>${booking.eligible_drivers.length} eligible</span></div><div class="candidate-list">`;
    if (!booking.eligible_drivers.length)
      content +=
        '<div class="empty-state"><span class="empty-icon">⌖</span><strong>Waiting for a nearby driver</strong><p>No available drivers of this category are within the radius. Start movement or create a request with a wider radius.</p></div>';
    for (const candidate of booking.eligible_drivers) {
      const d = ui.state.drivers.find(
        (driver) => driver.id === candidate.driver_id,
      );
      if (d)
        content += `<div class="candidate-row"><span class="driver-avatar">${escapeHTML(
          d.name
            .split(" ")
            .map((x) => x[0])
            .slice(0, 2)
            .join(""),
        )}</span><div class="driver-info"><strong>${escapeHTML(d.name)}</strong><small>${titleCase(d.vehicle_type)} · ${candidate.distance_km.toFixed(2)} km away</small></div><button class="accept-button" data-accept="${escapeHTML(d.id)}" ${ui.mutationBusy ? "disabled" : ""}>Accept ride</button></div>`;
    }
    content += `</div><p class="form-footnote">Act as a driver to accept. Only one can win.</p><div class="booking-actions"><button class="cancel-button" data-cancel="${escapeHTML(booking.id)}">Cancel request</button></div>`;
  } else if (booking.status === "assigned") {
    const d = booking.assigned_driver;
    content += `<div class="assigned-card"><span class="driver-avatar">${escapeHTML(
      d?.name
        .split(" ")
        .map((x) => x[0])
        .slice(0, 2)
        .join("") ?? "Z",
    )}</span><h3>${escapeHTML(d?.name ?? "Assigned driver")}</h3><p>Your ${booking.vehicle_type} driver is reserved for this ride.<br>Start movement to watch them approach pickup.</p><span class="assigned-badge">✓ MATCH CONFIRMED</span></div><div class="booking-actions"><button class="cancel-button" data-cancel="${escapeHTML(booking.id)}">Cancel ride</button><button class="primary-button" data-complete="${escapeHTML(booking.id)}">Complete ride ✓</button></div>`;
  } else {
    const details = {
      completed: [
        "✓",
        "A connection well made.",
        "Your ride is complete. The driver is available for their next request.",
      ],
      cancelled: [
        "↺",
        "Plans change. That’s okay.",
        "This request was cancelled. Any assigned driver has been released.",
      ],
      expired: [
        "◷",
        "The inbox has closed.",
        "No driver accepted within 90 seconds. Try a new request with a larger radius.",
      ],
    };
    const [icon, title, description] = details[booking.status] ?? [
      "·",
      "Request closed.",
      "",
    ];
    content += `<div class="booking-terminal"><span class="terminal-icon">${icon}</span><h3>${title}</h3><p>${description}</p></div><button class="primary-button submit-button" data-new-ride>Find your next ride <span>→</span></button>`;
  }
  $("#booking-detail-view").innerHTML = content + "</div>";
  updateCountdown();
  setActionButtons(ui.mutationBusy);
}
function updateCountdown() {
  const b = currentBooking(),
    el = $("#booking-countdown");
  if (b && el) {
    const seconds = Math.max(
      0,
      Math.ceil((new Date(b.expires_at) - Date.now()) / 1000),
    );
    el.textContent = `${seconds}s`;
  }
}
function switchView(view) {
  ui.view = view;
  document.body.classList.remove("view-queue", "view-fleet", "view-activity");
  if (view !== "overview") document.body.classList.add(`view-${view}`);
  $("#fleet-section").classList.toggle("hidden", view !== "fleet");
  document
    .querySelectorAll("[data-nav]")
    .forEach((button) =>
      button.classList.toggle("active", button.dataset.nav === view),
    );
  const content = {
    overview: [
      "Overview",
      "Every ride, right on time.",
      "Find the right driver. Make the connection. Keep Bengaluru moving.",
    ],
    queue: [
      "Dispatch queue",
      "Good connections start here.",
      "Manage every request, from the first match to the final stop.",
    ],
    fleet: [
      "Driver fleet",
      "The people who move the city.",
      "A bike or an auto. A nearby driver. One shared, live fleet.",
    ],
    activity: [
      "Activity log",
      "Every connection has a story.",
      "Follow the complete history of your dispatch simulation.",
    ],
  }[view];
  $("#breadcrumb-current").textContent = content[0];
  $("#page-title").textContent = content[1];
  $("#page-subtitle").textContent = content[2];
}
function newRide() {
  ui.selected = null;
  switchView("overview");
  renderBooking();
  renderQueue();
  renderMap();
  $("#rider-name").focus();
  $("#request-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
}
function choosePickup(p, label) {
  $("#pickup-lat").value = p.lat.toFixed(6);
  $("#pickup-lng").value = p.lng.toFixed(6);
  $("#pickup-label").value = label;
  ui.mapCenter = { ...p };
  ui.mapDriver = null;
  renderMatches();
  renderMap();
}
async function tick() {
  if (!ui.movement || ui.tickBusy || ui.mutationBusy || ui.refreshBusy) return;
  ui.tickBusy = true;
  const generation = ++ui.stateGeneration;
  ui.tickPromise = (async () => {
    try {
      const state = await api("/api/simulation/tick", {
        method: "POST",
        body: JSON.stringify({ seconds: 5 }),
      });
      if (generation === ui.stateGeneration) {
        ui.state = state;
        setConnected(true);
        render();
      }
    } catch (error) {
      if (generation === ui.stateGeneration) setConnected(false);
    } finally {
      ui.tickBusy = false;
    }
  })();
  return ui.tickPromise;
}

$("#ride-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const p = pickup();
  if (
    !Number.isFinite(p.lat) ||
    !Number.isFinite(p.lng) ||
    Math.abs(p.lat) > 90 ||
    Math.abs(p.lng) > 180
  ) {
    toast("Enter valid latitude and longitude coordinates.", true);
    return;
  }
  $("#create-ride").textContent = "Finding your drivers…";
  const booking = await mutate(
    "/api/bookings",
    {
      pickup_lat: p.lat,
      pickup_lng: p.lng,
      pickup_label: $("#pickup-label").value.trim(),
      rider_name: $("#rider-name").value.trim() || "Guest rider",
      vehicle_type: ui.vehicle,
      radius_km: Number($("#radius").value),
    },
    "Ride request created. The driver inbox is open.",
  );
  $("#create-ride").innerHTML = "Find my driver <span>→</span>";
  if (booking) {
    ui.selected = booking.id;
    ui.mapCenter = p;
    render();
    $("#request-panel").scrollIntoView({
      behavior: "smooth",
      block: "nearest",
    });
  }
});
document.addEventListener("click", async (event) => {
  const nav = event.target.closest("[data-nav]");
  if (nav) {
    switchView(nav.dataset.nav);
    return;
  }
  const preset = event.target.closest("[data-preset]");
  if (preset) {
    choosePickup(
      presets[preset.dataset.preset],
      presets[preset.dataset.preset].label,
    );
    document
      .querySelectorAll("[data-preset]")
      .forEach((b) => b.classList.toggle("active", b === preset));
    return;
  }
  const vehicle = event.target.closest("[data-vehicle]");
  if (vehicle) {
    ui.vehicle = vehicle.dataset.vehicle;
    document.querySelectorAll("[data-vehicle]").forEach((b) => {
      b.classList.toggle("active", b === vehicle);
      b.setAttribute("aria-pressed", String(b === vehicle));
    });
    renderMatches();
    renderMap();
    return;
  }
  const filter = event.target.closest("[data-filter]");
  if (filter) {
    ui.filter = filter.dataset.filter;
    document
      .querySelectorAll("[data-filter]")
      .forEach((b) => b.classList.toggle("active", b === filter));
    renderQueue();
    return;
  }
  if (event.target.closest("[data-new-ride]")) {
    newRide();
    return;
  }
  const row = event.target.closest("[data-booking]");
  if (row) {
    ui.selected = row.dataset.booking;
    const b = currentBooking();
    if (b) ui.mapCenter = { lat: b.pickup_lat, lng: b.pickup_lng };
    switchView("overview");
    render();
    $("#request-panel").scrollIntoView({
      behavior: "smooth",
      block: "nearest",
    });
    return;
  }
  const accept = event.target.closest("[data-accept]");
  if (accept) {
    await mutate(
      `/api/bookings/${encodeURIComponent(ui.selected)}/accept`,
      { driver_id: accept.dataset.accept },
      "Matched! Your driver is reserved for this ride.",
    );
    return;
  }
  const cancel = event.target.closest("[data-cancel]");
  if (cancel) {
    await mutate(
      `/api/bookings/${encodeURIComponent(cancel.dataset.cancel)}/cancel`,
      { reason: "Cancelled by rider" },
      "Ride cancelled. Any reserved driver is available again.",
    );
    return;
  }
  const complete = event.target.closest("[data-complete]");
  if (complete) {
    await mutate(
      `/api/bookings/${encodeURIComponent(complete.dataset.complete)}/complete`,
      undefined,
      "Ride complete. Here’s to the next connection.",
    );
    return;
  }
  const availability = event.target.closest("[data-availability]");
  if (availability && !availability.disabled) {
    await mutate(
      `/api/drivers/${encodeURIComponent(availability.dataset.availability)}`,
      { available: availability.dataset.online === "true" },
      "Driver availability updated.",
      "PATCH",
    );
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    const row = event.target.closest("[data-booking], [data-map-driver]");
    if (row) {
      event.preventDefault();
      row.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    }
  }
});
$("#radius").addEventListener("input", () => {
  $("#radius-output").value = `${Number($("#radius").value)} km`;
  renderMatches();
  renderMap();
});
for (const id of ["#pickup-lat", "#pickup-lng"]) {
  $(id).addEventListener("input", () => {
    document
      .querySelectorAll("[data-preset]")
      .forEach((button) => button.classList.remove("active"));
    renderMatches();
    renderMap();
  });
  $(id).addEventListener("change", () => {
    const p = pickup();
    if (
      Number.isFinite(p.lat) &&
      Number.isFinite(p.lng) &&
      Math.abs(p.lat) <= 90 &&
      Math.abs(p.lng) <= 180
    ) {
      ui.mapCenter = p;
      renderMap();
    }
  });
}
$("#city-map").addEventListener("click", (event) => {
  const driver = event.target.closest("[data-map-driver]");
  if (driver) {
    ui.mapDriver =
      ui.mapDriver === driver.dataset.mapDriver
        ? null
        : driver.dataset.mapDriver;
    renderMap();
    return;
  }
  if (ui.selected) {
    toast("Create another request to choose a new pickup.");
    return;
  }
  const svg = $("#city-map"),
    point = svg.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  const transformed = point.matrixTransform(svg.getScreenCTM().inverse()),
    p = fromPoint(transformed.x, transformed.y);
  if (Math.abs(p.lat) > 90 || Math.abs(p.lng) > 180) return;
  // Preserve the current viewport when selecting a map point.
  $("#pickup-lat").value = p.lat.toFixed(6);
  $("#pickup-lng").value = p.lng.toFixed(6);
  $("#pickup-label").value = "Custom map pickup, Bengaluru";
  ui.mapDriver = null;
  document
    .querySelectorAll("[data-preset]")
    .forEach((button) => button.classList.remove("active"));
  renderMatches();
  renderMap();
});
$("#map-zoom-in").addEventListener("click", () => {
  ui.zoom = Math.min(4, ui.zoom * 1.3);
  renderMap();
});
$("#map-zoom-out").addEventListener("click", () => {
  ui.zoom = Math.max(0.35, ui.zoom / 1.3);
  renderMap();
});
$("#map-center").addEventListener("click", () => {
  ui.mapCenter = selectedPickup();
  ui.zoom = 1;
  ui.mapDriver = null;
  renderMap();
});
$("#new-ride-top").addEventListener("click", newRide);
$("#retry").addEventListener("click", () => refresh());
$("#simulation-toggle").addEventListener("click", () => {
  ui.movement = !ui.movement;
  $("#simulation-label").textContent = ui.movement
    ? "Pause movement"
    : "Start movement";
  $(".play-icon").textContent = ui.movement ? "Ⅱ" : "▷";
  if (ui.movement) {
    toast("Simulation running. Drivers move every 5 seconds.");
    tick();
  }
});
$("#reset-open").addEventListener("click", () =>
  $("#reset-dialog").showModal(),
);
$("#reset-cancel").addEventListener("click", () => $("#reset-dialog").close());
$("#reset-confirm").addEventListener("click", async () => {
  const result = await mutate(
    "/api/demo/reset",
    undefined,
    "Fresh start. Your demo fleet is ready.",
  );
  if (result) {
    ui.selected = null;
    ui.mapDriver = null;
    ui.movement = false;
    $("#simulation-label").textContent = "Start movement";
    $(".play-icon").textContent = "▷";
    $("#reset-dialog").close();
    choosePickup(presets.center, presets.center.label);
    switchView("overview");
    render();
  }
});
$("#local-date").textContent = new Date().toLocaleDateString(undefined, {
  day: "numeric",
  month: "short",
  year: "numeric",
});
renderMap();
refresh();
setInterval(refresh, 2000);
setInterval(tick, 5000);
setInterval(updateCountdown, 1000);
