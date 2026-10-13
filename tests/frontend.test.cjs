"use strict";

// Exercise the dashboard's actual request coordination without a browser,
// network, third-party packages, or timing-dependent sleeps.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(
  path.join(__dirname, "../static/app.js"),
  "utf8",
);
const flush = () => new Promise((resolve) => setImmediate(resolve));

function state(label, drivers = []) {
  return {
    label,
    drivers,
    bookings: [],
    events: [],
    stats: {
      available_drivers: 0,
      active_bookings: 0,
      completed_rides: 0,
      total_bookings: 0,
    },
    settings: { default_radius_km: 2, offer_ttl_seconds: 90 },
  };
}

async function dashboard() {
  const nodes = new Map();
  const requests = [];
  const rendered = [];
  let fleetButtons = [];

  function element(selector = "") {
    let html = "";
    return {
      dataset: {},
      disabled: false,
      textContent: "",
      value:
        selector === "#pickup-lat"
          ? "12.9716"
          : selector === "#pickup-lng"
            ? "77.5946"
            : "2",
      classList: { add() {}, remove() {}, toggle() {} },
      addEventListener() {},
      append() {},
      remove() {},
      focus() {},
      scrollIntoView() {},
      get innerHTML() {
        return html;
      },
      set innerHTML(value) {
        html = value;
        if (selector === "#fleet-content") {
          fleetButtons = [...value.matchAll(/<button\b([^>]*)>/g)].map(
            (match) => {
              const button = element();
              button.disabled = /\bdisabled\b/.test(match[1]);
              for (const attribute of match[1].matchAll(
                /data-([a-z-]+)="([^"]*)"/g,
              )) {
                button.dataset[attribute[1]] = attribute[2];
              }
              return button;
            },
          );
        }
      },
    };
  }

  const document = {
    querySelector(selector) {
      if (!nodes.has(selector)) nodes.set(selector, element(selector));
      return nodes.get(selector);
    },
    querySelectorAll(selector) {
      if (selector.includes("#create-ride"))
        return [document.querySelector("#create-ride"), ...fleetButtons];
      return [];
    },
    addEventListener() {},
    createElement() {
      return element();
    },
  };

  const context = vm.createContext({
    document,
    AbortController,
    // Dashboard timers are registered but never fired. Tests control each
    // individual request and resolve responses in a specified order.
    setTimeout: () => 1,
    clearTimeout() {},
    setInterval: () => 1,
    fetch(url, options) {
      return new Promise((resolve, reject) => {
        requests.push({
          url,
          options,
          reject,
          respond(data, status = 200) {
            resolve({
              ok: status >= 200 && status < 300,
              status,
              json: async () => data,
            });
          },
        });
      });
    },
    recordRender: (snapshot) => rendered.push(snapshot.label),
  });
  vm.runInContext(source, context, { filename: "static/app.js" });
  // Request coordination is the unit under test; avoid unrelated visual
  // rendering work while preserving the real fleet renderer for its test.
  vm.runInContext("render = () => recordRender(ui.state);", context);
  const ui = vm.runInContext("ui", context);
  assert.equal(requests.length, 1, "startup requests the initial state");
  requests[0].respond(state("initial"));
  await ui.refreshPromise;
  requests.length = 0;
  rendered.length = 0;
  return {
    context,
    ui,
    requests,
    rendered,
    document,
    fleetButtons: () => fleetButtons,
  };
}

test("a delayed poll is discarded before mutation and a fresh snapshot arrives before unlocking", async () => {
  const app = await dashboard();
  const poll = app.context.refresh();
  const mutation = app.context.mutate("/api/bookings/ride-1/accept", {
    driver_id: "bike-01",
  });

  assert.equal(app.ui.mutationBusy, true);
  assert.equal(app.requests.length, 1, "mutation waits for the preceding poll");
  app.requests[0].respond(state("stale-pending"));
  await poll;
  await flush();
  assert.equal(
    app.ui.state.label,
    "initial",
    "invalidated poll cannot change the displayed state",
  );
  assert.equal(app.requests[1].url, "/api/bookings/ride-1/accept");

  const assigned = { id: "ride-1", status: "assigned" };
  app.requests[1].respond(assigned);
  await flush();
  assert.equal(
    app.requests[2].url,
    "/api/state",
    "mutation always requests fresh authoritative state",
  );
  assert.equal(
    app.ui.mutationBusy,
    true,
    "actions remain locked while the fresh snapshot is pending",
  );
  assert.equal(app.document.querySelector("#create-ride").disabled, true);

  app.requests[2].respond(state("fresh-assigned"));
  assert.equal(await mutation, assigned);
  assert.equal(app.ui.state.label, "fresh-assigned");
  assert.deepEqual(app.rendered, ["fresh-assigned"]);
  assert.equal(app.ui.mutationBusy, false);
  assert.equal(app.document.querySelector("#create-ride").disabled, false);
});

test("a delayed movement snapshot cannot restore old state during reset", async () => {
  const app = await dashboard();
  app.ui.movement = true;
  const movement = app.context.tick();
  const reset = app.context.mutate("/api/demo/reset");
  assert.equal(app.requests.length, 1, "reset waits for the preceding tick");

  app.requests[0].respond(state("old-fleet"));
  await movement;
  await flush();
  assert.equal(app.ui.state.label, "initial");
  assert.equal(app.requests[1].url, "/api/demo/reset");
  app.requests[1].respond(state("reset-response"));
  await flush();
  app.requests[2].respond(state("fresh-reset"));
  await reset;
  assert.equal(app.ui.state.label, "fresh-reset");
  assert.deepEqual(app.rendered, ["fresh-reset"]);
});

test("polling and movement requests never overlap in either direction", async () => {
  const app = await dashboard();
  app.ui.movement = true;
  const poll = app.context.refresh();
  await app.context.tick();
  assert.equal(app.requests.length, 1, "tick does not start while polling");
  assert.equal(app.requests[0].url, "/api/state");
  app.requests[0].respond(state("polled"));
  await poll;

  const movement = app.context.tick();
  await app.context.refresh();
  assert.equal(app.requests.length, 2, "poll does not start while moving");
  assert.equal(app.requests[1].url, "/api/simulation/tick");
  app.requests[1].respond(state("moved"));
  await movement;
  assert.equal(app.ui.state.label, "moved");
});

test("overlapping refresh callers wait for the same response", async () => {
  const app = await dashboard();
  const first = app.context.refresh();
  let secondFinished = false;
  const second = app.context.refresh().then(() => {
    secondFinished = true;
  });
  await flush();
  assert.equal(app.requests.length, 1);
  assert.equal(
    secondFinished,
    false,
    "another refresh must not return before the active response",
  );
  app.requests[0].respond(state("shared"));
  await Promise.all([first, second]);
  assert.equal(app.ui.state.label, "shared");
  assert.deepEqual(app.rendered, ["shared"]);
});

test("polls, movement, and duplicate commands are blocked while a mutation is pending", async () => {
  const app = await dashboard();
  app.ui.movement = true;
  const mutation = app.context.mutate("/api/bookings/ride-1/cancel", {});
  await flush();
  await app.context.refresh();
  await app.context.tick();
  const duplicate = await app.context.mutate("/api/bookings/ride-1/cancel", {});
  assert.equal(duplicate, null);
  assert.equal(app.requests.length, 1, "only the first command is sent");

  app.requests[0].respond({ id: "ride-1", status: "cancelled" });
  await flush();
  app.requests[1].respond(state("cancelled"));
  await mutation;
  assert.equal(app.ui.mutationBusy, false);
});

test("busy fleet controls stay disabled after global action buttons unlock", async () => {
  const app = await dashboard();
  app.ui.state = state(
    "fleet",
    ["busy", "available", "offline"].map((status, index) => ({
      id: `bike-${index}`,
      name: `Driver ${index}`,
      vehicle_type: "bike",
      status,
      latitude: 12.9716,
      longitude: 77.5946,
    })),
  );
  app.context.renderFleet();
  const [busy, available, offline] = app.fleetButtons();
  assert.equal(busy.disabled, true);
  app.context.setActionButtons(true);
  assert.equal(available.disabled, true);
  assert.equal(offline.disabled, true);
  app.context.setActionButtons(false);
  assert.equal(
    busy.disabled,
    true,
    "assigned drivers cannot be manually taken offline",
  );
  assert.equal(available.disabled, false);
  assert.equal(offline.disabled, false);
});
