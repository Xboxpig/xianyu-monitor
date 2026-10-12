import test from "node:test";
import assert from "node:assert/strict";

const storage = {};
const events = [];
let nextCommand = null;
let failReport = false;
let snapshot = { entries: [], domItems: [{ id: "123", title: "item", price: "10" }], hasMore: false };
let nextClicks = 0;
let nextMissing = false;
let attention = null;
let filterResult = { personalApplied: false };
let version = '2.0.4';
const originalTimer = globalThis.setTimeout;
globalThis.setTimeout = () => 1;
const event = { addListener() {} };
globalThis.chrome = {
  alarms: { onAlarm: event, async create() {} },
  runtime: { onStartup: event, onInstalled: event, onMessage: event, getManifest: () => ({ version }) },
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
      if (nextMissing) return [{ result: { clicked: false, reason: 'missing' } }];
      nextClicks++;
      snapshot = { ...snapshot, currentPage: 2, sequence: 1,
        entries: [{ url: "idlemtopsearch.pc.search", sequence: 1,
          body: { data: { resultList: [] } } }] };
      return [{ result: { clicked: true, sequenceFloor: 0 } }];
    }
    return [{ result: filterResult }];
  } },
};
globalThis.fetch = async (url, options) => {
  if (url.endsWith('/resume')) {
    events.push(['resume', JSON.parse(options.body)]);
    attention = null;
  }
  if (url.endsWith("/report")) {
    if (failReport) throw Error("network unavailable");
    events.push(["report", JSON.parse(options.body)]);
    nextCommand = null;
  }
  return { ok: true, async json() { return url.endsWith("/poll") ? { command: nextCommand, attention } : { accepted: true }; } };
};
// Importing a service worker also runs its startup tick. Initially it is disabled.
const { tick, validateEndpoint, pageCheckpoint } = await import("../native-bridge.js");
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

test("resumed commands preserve an in-progress page turn when no page indicator exists", async () => {
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

test('waiting task resumes only after a verified page has new usable data', async () => {
  const command = { run_id: 'waiting-run', command_id: 'waiting-command', action: 'search',
    url: 'https://www.goofish.com/search?q=test', page: 1, task: {} };
  nextCommand = null;
  attention = { run_id: command.run_id, message: '请检查页面' };
  snapshot = { href: command.url, sequence: 1, ready: true, entries: [], hasMore: false, domItems: [{ id: '123', title: 'item', price: '10' }] };
  storage.nativeState = { runId: command.run_id, lastCommand: command, command: null, phase: 'read',
    tabs: { search: 20 }, tabKey: 'search', windowId: 10, attentionCheckpoint: pageCheckpoint(snapshot) };
  const before = events.filter(event => event[0] === 'resume').length;
  await tick();
  assert.equal(events.filter(event => event[0] === 'resume').length, before);
  snapshot = { ...snapshot, sequence: 2, attention: 'verification required' };
  await tick();
  assert.equal(events.filter(event => event[0] === 'resume').length, before);
  snapshot = { ...snapshot, attention: null };
  await tick();
  assert.equal(events.filter(event => event[0] === 'resume').length, before + 1);
  assert.equal(storage.nativeState.phase, 'read');
  assert.equal(storage.nativeState.attention, '');
  assert.deepEqual(events.findLast(event => event[0] === 'resume')[1], { client_id: 'chrome-client-1', run_id: 'waiting-run' });
});

test('ordinary filter retries preserve the final response checkpoint and do not request manual selection', async () => {
  nextCommand = { run_id: 'filter-run', command_id: 'filter-command', action: 'search',
    url: 'https://www.goofish.com/search?q=test', page: 1, task: { new_publish_option: '最新' } };
  storage.nativeState = { runId: nextCommand.run_id, lastCommand: nextCommand, command: nextCommand,
    phase: 'filters', tabs: { search: 20 }, tabKey: 'search', windowId: 10, startedAt: Date.now() };
  snapshot = { sequence: 3, entries: [{ url: 'idlemtopsearch.pc.search', sequence: 3, body: { data: { resultList: [] } } }] };
  filterResult = { retry: true, error: '菜单未加载', changed: true, sequenceFloor: 3 };
  await tick();
  assert.equal(storage.nativeState.phase, 'filters');
  assert.equal(storage.nativeState.filterAttempts, 1);
  assert.equal(storage.nativeState.outbox, undefined);
  filterResult = { personalApplied: false, changed: false };
  await tick();
  assert.equal(storage.nativeState.phase, 'read');
  assert.equal(storage.nativeState.searchSequenceFloor, 3);
  await tick();
  assert.equal(storage.nativeState.outbox, undefined, 'stale search data must not be reported');
  snapshot.sequence = 4;
  snapshot.entries[0].sequence = 4;
  await tick();
  assert.equal(storage.nativeState.outbox.outcome, 'ok');
  await tick();
});

test('persistent filter failure is reported only after bounded automatic retries', async () => {
  nextCommand = { run_id: 'failed-filter-run', command_id: 'failed-filter-command', action: 'search',
    url: 'https://www.goofish.com/search?q=test', page: 1, task: { new_publish_option: '最新' } };
  storage.nativeState = { runId: nextCommand.run_id, lastCommand: nextCommand, command: nextCommand,
    phase: 'filters', tabs: { search: 20 }, tabKey: 'search', windowId: 10, startedAt: Date.now() };
  filterResult = { retry: true, error: '发布时间筛选「最新」未找到菜单选项。', changed: false };
  for (let index = 0; index < 3; index++) {
    await tick();
    assert.equal(storage.nativeState.outbox, undefined);
  }
  await tick();
  assert.equal(storage.nativeState.outbox.outcome, 'error');
  assert.match(storage.nativeState.outbox.message, /扩展自动重试失败/);
  assert.doesNotMatch(storage.nativeState.outbox.message, /请在搜索页面设置/);
  await tick();
  filterResult = { personalApplied: false };
});

test('updating the extension resumes a filter pause without asking the user to change the page', async () => {
  const command = { run_id: 'old-filter-run', command_id: 'old-filter-command', action: 'search',
    url: 'https://www.goofish.com/search?q=test', page: 1, task: { new_publish_option: '最新' } };
  nextCommand = null;
  attention = { run_id: command.run_id, message: '请在搜索页面设置发布时间筛选：最新，然后点击继续。' };
  snapshot = { href: command.url, sequence: 1, ready: true, entries: [], hasMore: false, domItems: [{ id: '123', title: 'item', price: '10' }] };
  storage.nativeState = { runId: command.run_id, lastCommand: command, command: null, phase: 'filters',
    collectorVersion: '2.0.0', filterAttempts: 4, tabs: { search: 20 }, tabKey: 'search', windowId: 10,
    attentionCheckpoint: pageCheckpoint(snapshot) };
  const before = events.filter(event => event[0] === 'resume').length;
  snapshot.attention = 'login required';
  await tick();
  assert.equal(events.filter(event => event[0] === 'resume').length, before);
  snapshot.attention = null;
  await tick();
  assert.equal(events.filter(event => event[0] === 'resume').length, before + 1);
  assert.equal(storage.nativeState.phase, 'filters');
  assert.equal(storage.nativeState.filterAttempts, 0);
  assert.equal(storage.nativeState.collectorVersion, version);
});

test('pending publication selection yields between polls and times out without a resume loop', async () => {
  nextCommand = { run_id: 'pending-filter-run', command_id: 'pending-filter-command', action: 'search',
    url: 'https://www.goofish.com/search?q=test', page: 1, task: { new_publish_option: '最新' } };
  storage.nativeState = { runId: nextCommand.run_id, lastCommand: nextCommand, command: nextCommand,
    phase: 'filters', tabs: { search: 20 }, tabKey: 'search', windowId: 10, startedAt: Date.now() };
  filterResult = { pending: true, changed: true, sequenceFloor: 3, error: '发布时间筛选「最新」尚未生效。' };
  for (let index = 0; index < 5; index++) await tick();
  assert.equal(storage.nativeConnection.online, true);
  assert.equal(storage.nativeState.outbox, undefined);
  assert.equal(storage.nativeState.searchSequenceFloor, 3);
  storage.nativeState.startedAt = Date.now() - 120001;
  await tick();
  assert.equal(storage.nativeState.outbox.outcome, 'error');
  await tick();
  filterResult = { personalApplied: false };

  attention = { run_id: 'same-version-run', message: '发布时间筛选「最新」未找到菜单选项。' };
  const command = { run_id: attention.run_id, action: 'search', url: 'https://www.goofish.com/search?q=test', task: {} };
  storage.nativeState = { runId: command.run_id, lastCommand: command, phase: 'filters', collectorVersion: version,
    tabs: { search: 20 }, tabKey: 'search', windowId: 10, attentionCheckpoint: 'old checkpoint' };
  snapshot = { href: command.url, sequence: 99, ready: true, entries: [], domItems: [{ id: '1', title: 'item', price: '10' }] };
  const before = events.filter(event => event[0] === 'resume').length;
  await tick();
  assert.equal(events.filter(event => event[0] === 'resume').length, before);
  attention = null;
});

test('price submission keeps polling and rejects search data from before the price change', async () => {
  nextCommand = { run_id: 'price-run', command_id: 'price-command', action: 'search',
    url: 'https://www.goofish.com/search?q=test', page: 1, task: { min_price: '2500', max_price: '3200' } };
  storage.nativeState = { runId: nextCommand.run_id, command: nextCommand, lastCommand: nextCommand,
    phase: 'filters', tabs: { search: 20 }, tabKey: 'search', windowId: 10, startedAt: Date.now() };
  filterResult = { pending: true, changed: false, error: '页面价格筛选尚未生效。' };
  await tick();
  filterResult = { pending: true, changed: true, sequenceFloor: 7, error: '页面价格筛选尚未生效。' };
  await tick();
  assert.equal(storage.nativeState.searchSequenceFloor, 7);
  assert.equal(storage.nativeConnection.online, true);
  filterResult = { changed: false };
  await tick();
  assert.equal(storage.nativeState.phase, 'read');
  snapshot = { sequence: 7, entries: [{ url: 'idlemtopsearch.pc.search', sequence: 7, body: { data: { resultList: [] } } }] };
  await tick();
  assert.equal(storage.nativeState.outbox, undefined);
  snapshot.sequence = 8;
  snapshot.entries[0].sequence = 8;
  await tick();
  assert.equal(storage.nativeState.outbox.outcome, 'ok');
  await tick();
  filterResult = { personalApplied: false };
});

test.after(() => { globalThis.setTimeout = originalTimer; });

test('missing next controls are retried without reporting an empty last page', async () => {
  nextCommand = { run_id: 'delayed-pagination-run', command_id: 'delayed-page', action: 'search_next',
    url: 'https://www.goofish.com/search?q=test', page: 2, task: {} };
  storage.nativeState = { runId: nextCommand.run_id, clientId: 'chrome-client-1', command: nextCommand, lastCommand: nextCommand,
    phase: 'load', searchPage: 1, tabs: { search: 20 }, tabKey: 'search', windowId: 10, startedAt: Date.now() };
  snapshot = { sequence: 1, currentPage: 1, entries: [], nextPageState: 'missing', hasMore: null };
  nextMissing = true;
  await tick(); await tick();
  assert.equal(storage.nativeState.phase, 'load');
  assert.equal(storage.nativeState.outbox, undefined);
  nextMissing = false;
  await tick(); await tick(); await tick();
  assert.equal(storage.nativeState.outbox.current_page, 2);
  assert.equal(storage.nativeState.outbox.outcome, 'ok');
  await tick();
});
