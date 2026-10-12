import test from "node:test";
import assert from "node:assert/strict";

const storage = {};
const events = [];
let nextCommand = null;
let failReport = false;
let snapshot = { entries: [], domItems: [{ id: "123", title: "item", price: "10" }], hasMore: false };
let nextClicks = 0;
const originalTimer = globalThis.setTimeout;
globalThis.setTimeout = () => 1;
const event = { addListener() {} };
globalThis.chrome = {
  alarms: { onAlarm: event, async create() {} },
  runtime: { onStartup: event, onInstalled: event, onMessage: event, getManifest: () => ({ version: "2.0.0" }) },
  storage: { local: {
    async get(keys) { return Object.fromEntries((Array.isArray(keys) ? keys : [keys]).map(key => [key, structuredClone(storage[key])])); },
    async set(values) { Object.assign(storage, structuredClone(values)); },
    async remove(key) { delete storage[key]; },
  } },
  windows: { async get(id) { if (!id) throw Error("missing"); return { id: 10 }; }, async create(options) {
    events.push(["window", options]); return { id: 10, tabs: [{ id: 20 }] };
  }, async update() {} },
  tabs: {
    async get(id) { if (!id) throw Error("missing"); return { id, windowId: 10, url: "https://www.goofish.com/search?q=test", status: "complete" }; },
    async create(options) { events.push(["tab", options]); return { id: 20 }; },
    async update() {}, async remove(id) { events.push(["close", id]); },
  },
  scripting: { async executeScript(options) {
    if (options.func.name === "readPage") return [{ result: snapshot }];
    if (options.args[0] === "next") {
      nextClicks++;
      snapshot = { ...snapshot, currentPage: 2, sequence: 1,
        entries: [{ url: "idlemtopsearch.pc.search", sequence: 1,
          body: { data: { resultList: [] } } }] };
      return [{ result: { clicked: true, sequenceFloor: 0 } }];
    }
    return [{ result: { personalApplied: false } }];
  } },
};
globalThis.fetch = async (url, options) => {
  if (url.endsWith("/report")) {
    if (failReport) throw Error("network unavailable");
    events.push(["report", JSON.parse(options.body)]);
    nextCommand = null;
  }
  return { ok: true, async json() { return url.endsWith("/poll") ? { command: nextCommand, attention: null } : { accepted: true }; } };
};
// Importing a service worker also runs its startup tick. Initially it is disabled.
const { tick, validateEndpoint } = await import("../native-bridge.js");
await Promise.resolve();

test("only loopback endpoints are accepted", () => {
  assert.equal(validateEndpoint("http://127.0.0.1:8000"), "http://127.0.0.1:8000");
  for (const value of ["https://remote.example", "http://127.0.0.1.evil.example", "http://user@localhost", "http://localhost/other"]) {
    assert.throws(() => validateEndpoint(value));
  }
});

test("service worker persists reports across a network failure and closes only owned tabs", async () => {
  storage.nativeConfig = { enabled: true, token: "token", client_id: "chrome-client-1", endpoint: "http://127.0.0.1:8000" };
  nextCommand = { run_id: "run", command_id: "command", action: "search", url: "https://www.goofish.com/search?q=test", task: {}, page: 1 };
  // Navigation, page load, filters, extraction.
  for (let index = 0; index < 4; index++) await tick();
  assert.equal(storage.nativeState.outbox.items[0].id, "123");
  failReport = true;
  await tick();
  assert.equal(storage.nativeState.outbox.command_id, "command");
  assert.equal(storage.nativeConnection.online, false);
  failReport = false;
  await tick();
  assert.equal(events.filter(event => event[0] === "report").length, 1);
  assert.equal(storage.nativeState, undefined);
  assert.equal(events.filter(event => event[0] === "close").length, 1);
});

test("pagination checkpoints do not click next again after a worker restart", async () => {
  nextCommand = { run_id: "page-run", command_id: "page-command", action: "search_next",
    url: "https://www.goofish.com/search?q=test", task: {}, page: 2 };
  storage.nativeState = { runId: "page-run", clientId: "chrome-client-1", command: nextCommand,
    lastCommand: nextCommand, phase: "load", searchPage: 1,
    tabs: { search: 20 }, tabKey: "search", windowId: 10, startedAt: Date.now() };
  snapshot = { entries: [], sequence: 0, currentPage: 1 };
  await tick();
  assert.equal(storage.nativeState.phase, "turning");
  assert.equal(nextClicks, 1);
  // Re-import to simulate module state being discarded while local storage survives.
  storage.nativeConfig.enabled = false;
  const restarted = await import("../native-bridge.js?restart");
  await Promise.resolve();
  storage.nativeConfig.enabled = true;
  await restarted.tick();
  assert.equal(storage.nativeState.phase, "read");
  await restarted.tick();
  assert.equal(storage.nativeState.outbox.command_id, "page-command");
  assert.equal(nextClicks, 1);
});

test("manual resume preserves an in-progress page turn when no page indicator exists", async () => {
  nextCommand = { run_id: "resume-run", command_id: "resume-command", action: "search_next",
    url: "https://www.goofish.com/search?q=test", task: {}, page: 2, resume: true };
  storage.nativeState = { runId: "resume-run", clientId: "chrome-client-1", command: null,
    lastCommand: { ...nextCommand, command_id: "old-command" }, phase: "turning", searchPage: 1,
    searchSequenceFloor: 5, tabs: { search: 20 }, tabKey: "search", windowId: 10 };
  snapshot = { sequence: 6, entries: [{ url: "idlemtopsearch.pc.search", sequence: 6,
    body: { data: { resultList: [] } } }] };
  const before = nextClicks;
  await tick();
  assert.equal(storage.nativeState.phase, "read");
  assert.equal(storage.nativeState.searchPage, 2);
  assert.equal(nextClicks, before);
});

test.after(() => { globalThis.setTimeout = originalTimer; });
