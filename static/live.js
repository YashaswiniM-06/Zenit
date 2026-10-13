(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const state = { mode: "login", user: null, data: null, pickup: null, dropoff: null, map: null, markers: {}, route: null, gpsWatch: null, gpsTimer: null, mapMode: "pickup", config: null };
  const api = async (path, options = {}) => {
    const response = await fetch(`/api/live${path}`, { credentials: "same-origin", headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
    if (response.status === 204) return null;
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || "Something went wrong. Please try again.");
    return body;
  };
  const message = (id, text = "") => { const el = $(id); el.textContent = text; el.classList.toggle("hidden", !text); };
  const toast = (text) => { const el = $("toast"); el.textContent = text; el.classList.remove("hidden"); clearTimeout(toast.timer); toast.timer = setTimeout(() => el.classList.add("hidden"), 4200); };
  const setMode = (mode) => {
    state.mode = mode; const signup = mode === "signup";
    $("signup-fields").classList.toggle("hidden", !signup); $("login-tab").classList.toggle("selected", !signup); $("signup-tab").classList.toggle("selected", signup);
    $("login-tab").setAttribute("aria-pressed", String(!signup)); $("signup-tab").setAttribute("aria-pressed", String(signup));
    $("auth-title").textContent = signup ? "Make your move." : "Welcome back."; $("auth-subtitle").textContent = signup ? "Create your pilot account in a minute." : "Your next connection starts here.";
    $("auth-submit").innerHTML = signup ? "Create account <span aria-hidden=\"true\">↗</span>" : "Log in <span aria-hidden=\"true\">↗</span>";
    $("password-hint").classList.toggle("hidden", !signup); $("forgot-button").classList.toggle("hidden", signup || !state.config?.email_enabled); message("auth-error");
  };
  const selectedRole = () => document.querySelector('input[name="role"]:checked')?.value || "rider";
  const selectChoice = (selector) => document.querySelectorAll(selector).forEach((item) => item.classList.toggle("selected", item.querySelector("input")?.checked));
  const mapMarker = (key, lat, lng, cls, label) => {
    if (!state.map || !Number.isFinite(lat) || !Number.isFinite(lng)) return;
    const icon = L.divIcon({ className: `zenit-marker ${cls}`, html: label, iconSize: [31,31], iconAnchor: [15,15] });
    if (state.markers[key]) state.markers[key].setLatLng([lat,lng]).setIcon(icon); else state.markers[key] = L.marker([lat,lng], { icon }).addTo(state.map);
  };
  const refreshMap = () => {
    if (!state.map) return;
    if (state.pickup) mapMarker("pickup", state.pickup.latitude, state.pickup.longitude, "pickup", "P");
    if (state.dropoff) mapMarker("dropoff", state.dropoff.latitude, state.dropoff.longitude, "dropoff", "D");
    const active = state.data?.rides?.find((r) => ["assigned","in_progress"].includes(r.status));
    if (active?.driver?.latitude != null) mapMarker("driver", active.driver.latitude, active.driver.longitude, "driver", "D");
    if (state.route) { if (state.markers.route) state.map.removeLayer(state.markers.route); state.markers.route = L.polyline(state.route.map((p) => [p[1],p[0]]), { color: "#27744d", weight: 4 }).addTo(state.map); }
  };
  const fitMap = () => { const items = [state.pickup, state.dropoff].filter(Boolean); if (items.length === 2) state.map.fitBounds(items.map((x) => [x.latitude,x.longitude]), { padding: [35,35] }); else if (items.length) state.map.setView([items[0].latitude,items[0].longitude], 14); };
  const initMap = () => {
    if (!window.L || state.map) return;
    state.map = L.map("map", { zoomControl: true }).setView([12.9716, 77.5946], 12);
    const key = state.config?.geoapify_key;
    if (key) L.tileLayer(`https://maps.geoapify.com/v1/tile/{style}/{z}/{x}/{y}.png?apiKey=${encodeURIComponent(key)}`, { style: "osm-bright", maxZoom: 20, attribution: '© <a href="https://www.geoapify.com/">Geoapify</a> · © OpenStreetMap' }).addTo(state.map);
    else message("map-notice", "Map tiles are unavailable. You can still enter coordinates manually.");
    state.map.on("click", (event) => { setPoint(state.mapMode, { latitude: event.latlng.lat, longitude: event.latlng.lng, label: state.mapMode === "pickup" ? "Map pickup" : "Map destination" }); });
  };
  const setPoint = (kind, point) => {
    state[kind] = point; $(kind + "-lat").value = point.latitude.toFixed(6); $(kind + "-lng").value = point.longitude.toFixed(6);
    $(kind + "-selected").textContent = point.label || `${point.latitude.toFixed(5)}, ${point.longitude.toFixed(5)}`; refreshMap(); routeEstimate();
  };
  const routeEstimate = async () => {
    if (!state.user || !state.pickup || !state.dropoff || !state.config?.maps_enabled) return;
    try { const r = await api(`/route?pickup_lat=${state.pickup.latitude}&pickup_lng=${state.pickup.longitude}&dropoff_lat=${state.dropoff.latitude}&dropoff_lng=${state.dropoff.longitude}`); state.route = r.coordinates; $("route-estimate").innerHTML = `<strong>${(r.distance_meters/1000).toFixed(1)} km · ~${Math.ceil(r.duration_seconds/60)} min</strong><small>Driving estimate from Geoapify</small>`; $("route-estimate").classList.remove("hidden"); refreshMap(); } catch (_) { /* coordinates still work when routing is unavailable */ }
  };
  const auth = async () => {
    try { state.config = await api("/config"); const result = await api("/auth/me"); state.user = result.user; showWorkspace(); } catch (error) { if (error.message.includes("Live pilot") || error.message.includes("unavailable")) $("service-unavailable").classList.remove("hidden"); setMode("login"); }
  };
  const showWorkspace = async () => {
    $("auth-screen").classList.add("hidden"); $("workspace").classList.remove("hidden"); $("welcome-name").textContent = state.user.name; $("user-avatar").textContent = state.user.name.slice(0,1).toUpperCase();
    const driver = state.user.role === "driver"; $("workspace-role").textContent = driver ? "Driver workspace" : "Rider workspace"; $("rider-composer").classList.toggle("hidden", driver); $("driver-setup").classList.toggle("hidden", !driver); $("driver-offers").classList.toggle("hidden", !driver);
    $("workspace-title").innerHTML = driver ? "Ready when you are<span>.</span>" : "Where are we headed<span>?</span>"; $("workspace-subtitle").textContent = driver ? "Share your location and choose when to connect." : "Choose your destination. Connect with a nearby driver.";
    initMap(); await loadState(); clearInterval(showWorkspace.timer); showWorkspace.timer = setInterval(() => { if (!document.hidden) loadState(); }, 4000);
  };
  const render = () => {
    const d = state.data; if (!d) return; const active = d.rides.find((r) => ["pending","assigned","in_progress"].includes(r.status));
    $("journey-count").textContent = d.rides.length; $("activity-count").textContent = d.events.length; $("ride-history").innerHTML = d.rides.length ? d.rides.map((r) => `<article class="history-row"><span class="history-icon">${r.vehicle_type === "bike" ? "↗" : "◒"}</span><div><strong>${escapeHtml(r.pickup_label)} → ${escapeHtml(r.dropoff_label)}</strong><small>${new Date(r.created_at).toLocaleString()}</small></div><span class="badge ${r.status}">${r.status.replace("_"," ")}</span></article>`).join("") : '<div class="empty-state"><span class="empty-icon">↗</span><h3>Your first journey is ahead.</h3><p>Your ride history will appear here.</p></div>';
    $("activity-list").innerHTML = d.events.map((e) => `<div class="activity-item"><i class="status-dot"></i><p>${escapeHtml(e.message)}<small>${new Date(e.created_at).toLocaleString()}</small></p></div>`).join("") || '<p class="muted">No activity yet.</p>';
    const activeBox = $("active-ride"); activeBox.classList.toggle("hidden", !active); if (active) { const action = active.status === "pending" ? "Cancel request" : active.status === "assigned" && state.user.role === "driver" ? "Start trip" : active.status === "in_progress" && state.user.role === "driver" ? "Complete trip" : active.status === "assigned" ? "Cancel ride" : "Trip in progress"; activeBox.innerHTML = `<div class="active-heading"><span class="eyebrow">${active.status.replace("_"," ")}</span><span class="badge ${active.status}">${active.status}</span></div><h2>${escapeHtml(active.pickup_label)}</h2><p class="active-description">→ ${escapeHtml(active.dropoff_label)}</p>${active.driver ? `<div class="assigned-driver"><span class="avatar">${escapeHtml(active.driver.name[0])}</span><div><strong>${escapeHtml(active.driver.name)}</strong><small>${escapeHtml(active.driver.vehicle_type)} · ${escapeHtml(active.driver.vehicle_number)}</small></div></div>` : '<p class="muted">Waiting for a nearby driver to accept.</p>'}<button class="button ${action.includes("Complete") ? "primary" : "secondary"} full" data-ride-action="${active.status === "pending" ? "cancel" : active.status === "assigned" && state.user.role === "driver" ? "start" : active.status === "in_progress" && state.user.role === "driver" ? "complete" : active.status === "assigned" ? "cancel" : ""}" data-ride-id="${active.id}" ${action === "Trip in progress" ? "disabled" : ""}>${action}</button>`; }
    if (state.user.role === "driver") { const driver = d.driver; $("driver-status").textContent = driver?.status || "Offline"; $("driver-status").className = `badge ${driver?.status || "offline"}`; $("availability-button").disabled = !driver || !driver.last_seen_at || driver.status === "busy"; $("availability-button").textContent = driver?.status === "available" ? "Go offline" : "Go online"; $("offers-count").textContent = d.offers.length; $("offers-list").innerHTML = d.offers.length ? d.offers.map((r) => `<article class="offer-card"><h3>${escapeHtml(r.pickup_label)} → ${escapeHtml(r.dropoff_label)}</h3><small>${r.vehicle_type} · within ${r.radius_km} km</small><button class="button primary full" data-accept="${r.id}">Accept ride</button></article>`).join("") : '<div class="empty-state"><span class="empty-icon">⌾</span><h3>No nearby requests yet.</h3><p>Keep GPS fresh and stay online to receive rides.</p></div>'; }
    refreshMap();
  };
  const escapeHtml = (v) => String(v || "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[c]);
  const loadState = async () => { try { state.data = await api("/state"); $("connection").classList.remove("is-error"); $("connection").lastElementChild.textContent = "Live"; $("connection-error").classList.add("hidden"); render(); } catch (error) { $("connection").classList.add("is-error"); $("connection").lastElementChild.textContent = "Reconnecting"; $("connection-error").classList.remove("hidden"); } };
  const gps = () => {
    if (!navigator.geolocation) return message("driver-error", "This browser does not support location sharing.");
    if (state.gpsWatch !== null) { navigator.geolocation.clearWatch(state.gpsWatch); state.gpsWatch = null; clearTimeout(state.gpsTimer); $("gps-button").textContent = "Start GPS"; $("gps-status").textContent = "Location sharing is off"; $("gps-light").classList.add("inactive"); return; }
    const send = async (p) => { try { await api("/driver/location", { method: "POST", body: JSON.stringify({ latitude: p.coords.latitude, longitude: p.coords.longitude, accuracy: p.coords.accuracy }) }); $("gps-status").textContent = "Live location shared"; $("gps-light").classList.remove("inactive"); await loadState(); } catch (e) { message("driver-error", e.message); } };
    state.gpsWatch = navigator.geolocation.watchPosition(send, () => message("driver-error", "Location permission is needed before going online."), { enableHighAccuracy: true, maximumAge: 8000, timeout: 15000 }); $("gps-button").textContent = "Stop GPS"; $("gps-status").textContent = "Getting your location…";
  };
  document.addEventListener("DOMContentLoaded", () => {
    $("login-tab").onclick = () => setMode("login"); $("signup-tab").onclick = () => setMode("signup"); document.querySelectorAll('.choice input,.vehicle-choice input').forEach((x) => x.onchange = () => { selectChoice(".choice"); selectChoice(".vehicle-choice"); });
    $("auth-form").onsubmit = async (e) => { e.preventDefault(); message("auth-error"); const fd = new FormData(e.currentTarget); const payload = { email: fd.get("email"), password: fd.get("password") }; if (state.mode === "signup") Object.assign(payload, { name: fd.get("name"), role: selectedRole() }); try { const r = await api(`/auth/${state.mode === "signup" ? "signup" : "login"}`, { method: "POST", body: JSON.stringify(payload) }); state.user = r.user; showWorkspace(); } catch (err) { message("auth-error", err.message); } };
    $("logout").onclick = async () => { try { await api("/auth/logout", { method: "POST" }); } finally { clearInterval(showWorkspace.timer); if (state.gpsWatch !== null) navigator.geolocation.clearWatch(state.gpsWatch); location.reload(); } }; $("retry").onclick = loadState; $("gps-button").onclick = gps;
    $("availability-button").onclick = async () => { try { const online = state.data.driver?.status !== "available"; await api("/driver/availability", { method: "POST", body: JSON.stringify({ available: online }) }); loadState(); } catch (e) { message("driver-error", e.message); } };
    $("driver-form").onsubmit = async (e) => { e.preventDefault(); try { await api("/driver/profile", { method: "PUT", body: JSON.stringify({ vehicle_type: $("driver-vehicle").value, vehicle_number: $("vehicle-number").value }) }); toast("Vehicle details saved."); loadState(); } catch (err) { message("driver-error", err.message); } };
    $("ride-radius").oninput = () => { $("radius-output").textContent = `${$("ride-radius").value} km radius`; };
    ["pickup","dropoff"].forEach((kind) => { ["lat","lng"].forEach((side) => $(`${kind}-${side}`).onchange = () => { const latitude = Number($(`${kind}-lat`).value), longitude = Number($(`${kind}-lng`).value); if (Number.isFinite(latitude) && Number.isFinite(longitude)) setPoint(kind, { latitude, longitude, label: `${kind === "pickup" ? "Manual pickup" : "Manual destination"}` }); }); $(kind + "-search").onchange = () => { const text = $(kind + "-search").value.trim(); if (text) search(kind, text); }; });
    $("rider-locate").onclick = () => navigator.geolocation?.getCurrentPosition((p) => setPoint("pickup", { latitude: p.coords.latitude, longitude: p.coords.longitude, label: "Your current location" }), () => message("ride-error", "Location permission was not granted."), { enableHighAccuracy: true, timeout: 12000 });
    $("ride-form").onsubmit = async (e) => { e.preventDefault(); if (!state.pickup || !state.dropoff) return message("ride-error", "Choose both pickup and destination."); try { const r = await api("/rides", { method: "POST", body: JSON.stringify({ pickup_lat: state.pickup.latitude, pickup_lng: state.pickup.longitude, pickup_label: state.pickup.label, dropoff_lat: state.dropoff.latitude, dropoff_lng: state.dropoff.longitude, dropoff_label: state.dropoff.label, vehicle_type: document.querySelector('input[name="vehicle_type"]:checked').value, radius_km: Number($("ride-radius").value) }) }); toast("Your ride request is live."); state.data.rides.unshift(r); loadState(); } catch (err) { message("ride-error", err.message); } };
    $("map-pickup").onclick = () => setMapMode("pickup"); $("map-dropoff").onclick = () => setMapMode("dropoff"); $("fit-map").onclick = fitMap;
    document.body.addEventListener("click", async (e) => { const accept = e.target.closest("[data-accept]"); const action = e.target.closest("[data-ride-action]"); try { if (accept) await api(`/rides/${accept.dataset.accept}/accept`, { method: "POST", body: "{}" }); if (action?.dataset.rideAction) await api(`/rides/${action.dataset.rideId}/${action.dataset.rideAction}`, { method: "POST", body: "{}" }); if (accept || action) loadState(); } catch (err) { toast(err.message); } });
    $("notifications-button").onclick = async () => { if ("Notification" in window) { const permission = await Notification.requestPermission(); toast(permission === "granted" ? "Ride notifications enabled." : "Notifications were not enabled."); } };
    auth();
  });
  const setMapMode = (mode) => { state.mapMode = mode; ["pickup","dropoff"].forEach((x) => { $("map-" + x).classList.toggle("selected", x === mode); $("map-" + x).setAttribute("aria-pressed", String(x === mode)); }); };
  const search = async (kind, q) => { try { const data = await api(`/geocode?q=${encodeURIComponent(q)}`); const target = $(kind + "-results"); target.innerHTML = data.results.map((r, i) => `<button type="button" data-search-kind="${kind}" data-index="${i}">${escapeHtml(r.label)}</button>`).join("") || "<p>No places found. Use map or coordinates.</p>"; target.classList.remove("hidden"); target.querySelectorAll("button").forEach((b) => b.onclick = () => { const r = data.results[Number(b.dataset.index)]; setPoint(kind, { ...r, label: r.label }); target.classList.add("hidden"); }); } catch (_) { /* show nothing until the provider is available */ } };
})();
