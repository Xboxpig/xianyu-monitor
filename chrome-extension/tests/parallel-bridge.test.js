import test from 'node:test';
import assert from 'node:assert/strict';

const storage = { nativeConfig: { enabled: false, token: 'token', client_id: 'chrome-client-1', endpoint: 'http://127.0.0.1:8000' } };
const tabs = new Map();
const windows = new Map();
const snapshots = new Map();
const reports = [];
const resumes = [];
let serial = 100;
let commands = [];
let attentions = [];
let activeRun = 'run';
let keepSearch = false;
let failReport = false;
let lastPoll;
const event = { addListener() {} };
globalThis.setTimeout = () => 1;
globalThis.chrome = {
  runtime: { id: 'extension', onStartup: event, onInstalled: event, onMessage: event, getManifest: () => ({ version: '2.1.0' }) },
  alarms: { onAlarm: event, async create() {} },
  storage: { local: {
    async get(keys) { return Object.fromEntries((Array.isArray(keys) ? keys : [keys]).map(key => [key, structuredClone(storage[key])])); },
    async set(values) { Object.assign(storage, structuredClone(values)); },
    async remove(key) { delete storage[key]; },
  } },
  windows: {
    async get(id) { if (!windows.has(id)) throw Error('missing'); return windows.get(id); },
    async create(options) {
      const id = serial++;
      const tab = { id: serial++, windowId: id, url: options.url, status: 'complete' };
      tabs.set(tab.id, tab);
      const window = { id, tabs: [tab] };
      windows.set(id, window);
      return window;
    },
    async update() {},
  },
  tabs: {
    async get(id) { if (!tabs.has(id)) throw Error('missing'); return tabs.get(id); },
    async update(id, options) { Object.assign(tabs.get(id), options); },
    async remove(id) { tabs.delete(id); },
    async reload() {},
  },
  scripting: { async executeScript(options) {
    const tab = tabs.get(options.target.tabId);
    if (options.func.name === 'readPage') {
      const id = new URL(tab.url).searchParams.get('id');
      return [{ result: snapshots.get(id) || { href: tab.url, ready: true, sequence: 1, heapBytes: 100 * 1048576,
        entries: [], domItem: { id, title: 'item', price: '10' } } }];
    }
    return [{ result: {} }];
  } },
};
globalThis.fetch = async (url, options) => {
  const body = JSON.parse(options.body);
  if (url.endsWith('/report')) {
    if (failReport) throw Error('network unavailable');
    reports.push(body);
    commands = commands.filter(c => c.command_id !== body.command_id);
    if (body.outcome === 'attention') attentions.push({ run_id: body.run_id, command_id: body.command_id, message: body.message });
  }
  if (url.endsWith('/resume')) {
    resumes.push(body);
    attentions = attentions.filter(a => a.command_id !== body.command_id);
  }
  if (url.endsWith('/poll')) lastPoll = body;
  return { ok: true, async json() {
    return url.endsWith('/poll') ? { commands, attentions, attention: attentions[0], active_run_id: activeRun, keep_search: keepSearch } : { accepted: true };
  } };
};
const { tick } = await import('../native-bridge.js');
await Promise.resolve();
storage.nativeConfig.enabled = true;
function detail(id) {
  return { run_id: 'run', command_id: 'command-' + id, slot_id: 'slot-' + id, action: 'detail',
    url: 'https://www.goofish.com/item?id=' + id, page: 1, task: {} };
}

test('multiple collection windows advance together and durable outboxes survive restart', async () => {
  commands = [detail('1'), detail('2'), detail('3'), detail('4')];
  await tick();
  assert.equal(tabs.size, 4);
  await tick();
  await tick();
  for (const c of commands) assert.equal(storage['nativeJob:' + c.slot_id].outbox.item.id, new URL(c.url).searchParams.get('id'));
  assert.equal(lastPoll.parallel, true);
  assert.equal(lastPoll.windows.length, 4);
  assert.ok(lastPoll.windows.every(w => w.memory_mb === 328));
  failReport = true;
  await tick();
  assert.equal(reports.length, 0);
  assert.equal(storage.nativeConnection.online, false);
  assert.equal(storage['nativeJob:slot-2'].outbox.item.id, '2');
  storage.nativeConfig.enabled = false;
  const restarted = await import('../native-bridge.js?parallel-restart');
  await Promise.resolve();
  storage.nativeConfig.enabled = true;
  failReport = false;
  await restarted.tick();
  assert.equal(reports.length, 4);
  assert.equal(tabs.size, 0);
  assert.deepEqual(storage.nativeParallelState.slots, []);
});

test('seller navigation reuses its detail window and stop preserves user-navigated tabs', async () => {
  commands = [detail('5'), detail('6')];
  await tick(); await tick(); await tick();
  const windowId = storage['nativeJob:slot-5'].windowId;
  // Deliver detail results, then emulate the backend seller-stage response.
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    const result = await previousFetch(url, options);
    if (url.endsWith('/poll') && !commands.length) commands = [{ ...detail('5'), command_id: 'seller-5', action: 'seller', url: 'https://www.goofish.com/personal?userId=105' }];
    return result;
  };
  await tick();
  assert.equal(storage['nativeJob:slot-5'].windowId, windowId);
  assert.equal(tabs.size, 1);
  assert.equal([...tabs.values()][0].url, 'https://www.goofish.com/personal?userId=105');
  globalThis.fetch = previousFetch;
  [...tabs.values()][0].url = 'https://example.com/user-page';
  commands = []; activeRun = null;
  await tick();
  assert.equal(tabs.size, 1);
  assert.deepEqual(storage.nativeParallelState.slots, []);
  tabs.clear();
});

test('a verification pause targets its own command while other windows finish', async () => {
  activeRun = 'run';
  commands = [detail('7'), detail('8')];
  snapshots.set('7', { href: detail('7').url, ready: true, sequence: 1, attention: 'verification required' });
  await tick(); await tick(); await tick();
  await tick();
  assert.ok(reports.some(r => r.command_id === 'command-8' && r.outcome === 'ok'));
  assert.equal(tabs.size, 1);
  assert.equal(resumes.length, 0);
  snapshots.set('7', { href: detail('7').url, ready: true, sequence: 2, entries: [], domItem: { id: '7', title: 'ready', price: '10' } });
  await tick();
  assert.deepEqual(resumes.at(-1), { client_id: 'chrome-client-1', run_id: 'run', command_id: 'command-7' });
  activeRun = null;
  await tick();
  assert.equal(tabs.size, 0);
});

test('a seller with no items completes while a missing nonempty list waits for data', async () => {
  activeRun = 'run'; attentions = [];
  commands = [{ ...detail('9'), action: 'seller', url: 'https://www.goofish.com/personal?userId=109' }];
  const head = { url: 'mtop.idle.web.user.page.head', body: { data: { module: { tabs: { item: { number: 5 }, rate: { number: 0 } } } } } };
  snapshots.set(null, { ready: true, entries: [head] });
  await tick(); await tick(); await tick(); await tick();
  assert.equal(storage['nativeJob:slot-9'].phase, 'seller_items');
  assert.equal(storage['nativeJob:slot-9'].outbox, undefined);
  head.body.data.module.tabs.item.number = 0;
  await tick();
  assert.equal(storage['nativeJob:slot-9'].phase, 'read');
  await tick();
  assert.equal(storage['nativeJob:slot-9'].outbox.outcome, 'ok');
  await tick();
  assert.equal(tabs.size, 0);
});

test('a backend delay releases the detail window and the seller starts only when dispatched', async () => {
  activeRun = 'run'; attentions = [];
  commands = [detail('10')];
  await tick(); await tick(); await tick();
  const detailWindow = storage['nativeJob:slot-10'].windowId;
  await tick();
  assert.ok(reports.some(r => r.command_id === 'command-10' && r.outcome === 'ok'));
  for (let i = 0; i < 5; i++) await tick();
  assert.equal(tabs.size, 0);
  assert.deepEqual(storage.nativeParallelState.slots, []);
  commands = [{ ...detail('10'), command_id: 'seller-10', action: 'seller',
    url: 'https://www.goofish.com/personal?userId=110' }];
  await tick();
  assert.notEqual(storage['nativeJob:slot-10'].windowId, detailWindow);
  assert.equal(tabs.size, 1);
  assert.equal([...tabs.values()][0].url, commands[0].url);
  for (let i = 0; i < 10; i++) await tick();
  assert.ok(reports.some(r => r.command_id === 'seller-10' && r.outcome === 'ok'));
  assert.equal(tabs.size, 0);
});
