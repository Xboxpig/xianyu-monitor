import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const storage = { nativeConfig: { enabled: false, token: 'token', client_id: 'chrome-client-1', endpoint: 'http://127.0.0.1:8000' } };
const tabs = new Map();
const windows = new Map();
const snapshots = new Map();
const reports = [];
const resumes = [];
const windowEvents = [];
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
      windowEvents.push({ operation: 'create', ...options });
      const id = serial++;
      const tab = { id: serial++, windowId: id, url: options.url, status: 'complete' };
      tabs.set(tab.id, tab);
      const window = { ...options, id, tabs: [tab] };
      windows.set(id, window);
      return window;
    },
    async update(id, options) {
      windowEvents.push({ operation: 'update', id, ...options });
      Object.assign(windows.get(id), options);
    },
  },
  tabs: {
    async get(id) { if (!tabs.has(id)) throw Error('missing'); return tabs.get(id); },
    async create(options) {
      windowEvents.push({ operation: 'createTab', ...options });
      const tab = { ...options, id: serial++, status: 'complete' };
      tabs.set(tab.id, tab);
      return tab;
    },
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
    if (body.outcome === 'attention') attentions.push({ run_id: body.run_id, command_id: body.command_id,
      message: body.message, needs_verification: body.needs_verification || false });
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
beforeEach(() => {
  for (const key of Object.keys(storage)) if (key !== 'nativeConfig') delete storage[key];
  storage.nativeConfig.enabled = true;
  tabs.clear(); windows.clear(); snapshots.clear();
  reports.length = 0; resumes.length = 0; windowEvents.length = 0;
  serial = 100; commands = []; attentions = []; activeRun = 'run';
  keepSearch = false; failReport = false; lastPoll = undefined;
});
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
  snapshots.set(null, { ready: true, entries: [{ url: 'mtop.idle.web.user.page.head',
    body: { data: { module: { tabs: { item: { number: 0 }, rate: { number: 0 } } } } } }] });
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

test('the search window keeps its profile open through final-page delays and closes after collection', async () => {
  activeRun = 'run'; attentions = []; keepSearch = true;
  const searchWindow = await chrome.windows.create({ url: 'https://www.goofish.com/search?q=test', state: 'minimized', focused: false });
  storage.nativeParallelState = { slots: ['search'] };
  storage['nativeJob:search'] = { slotId: 'search', runId: 'run', windowId: searchWindow.id,
    tabs: { search: searchWindow.tabs[0].id }, tabKey: 'search',
    lastCommand: { command_id: 'search-final', action: 'search', slot_id: 'search' } };
  commands = [];
  for (let i = 0; i < 5; i++) await tick();
  assert.equal(tabs.size, 1);
  assert.equal(storage['nativeJob:search'].windowId, searchWindow.id);
  commands = [detail('11')];
  await tick(); await tick(); await tick(); await tick();
  assert.ok(reports.some(r => r.command_id === 'command-11' && r.outcome === 'ok'));
  assert.equal(tabs.size, 1);
  for (let i = 0; i < 5; i++) await tick();
  assert.deepEqual(storage.nativeParallelState.slots, ['search']);
  assert.equal(storage['nativeJob:search'].windowId, searchWindow.id);
  activeRun = null; keepSearch = false;
  await tick();
  assert.equal(tabs.size, 0);
  assert.deepEqual(storage.nativeParallelState.slots, []);
});

async function backgroundSearch() {
  keepSearch = true;
  const host = await chrome.windows.create({ url: 'https://www.goofish.com/search?q=test', state: 'minimized', focused: false });
  storage.nativeParallelState = { slots: ['search'] };
  storage['nativeJob:search'] = { slotId: 'search', runId: 'run', windowId: host.id,
    tabs: { page: host.tabs[0].id }, tabKey: 'page',
    lastCommand: { command_id: 'search-final', action: 'search', slot_id: 'search' } };
  return host;
}

test('random gaps, seller collection and later items use inactive tabs without creating more native windows', async () => {
  const host = await backgroundSearch();
  snapshots.set(null, { ready: true, entries: [{ url: 'mtop.idle.web.user.page.head',
    body: { data: { module: { tabs: { item: { number: 0 }, rate: { number: 0 } } } } } }] });
  const stages = [detail('13'), { ...detail('13'), command_id: 'seller-13', action: 'seller',
    url: 'https://www.goofish.com/personal?userId=113' }, detail('14')];
  for (const command of stages) {
    commands = [command];
    await tick();
    assert.equal(storage['nativeJob:' + command.slot_id].windowId, host.id);
    assert.equal(tabs.size, 2);
    for (let i = 0; i < 8; i++) await tick();
    assert.ok(reports.some(report => report.command_id === command.command_id && report.outcome === 'ok'));
    assert.equal(tabs.size, 1);
    assert.equal(windows.get(host.id).state, 'minimized');
  }
  assert.equal(windowEvents.filter(e => e.operation === 'create').length, 1);
  assert.equal(windowEvents.filter(e => e.operation === 'createTab').length, 3);
  assert.ok(windowEvents.filter(e => e.operation === 'createTab').every(e => e.active === false));
  assert.equal(windowEvents.filter(e => e.focused === true).length, 0);
  activeRun = null;
  await tick();
  assert.equal(tabs.size, 0);
});

test('shared verification window stays visible until every blocked page recovers without repeated focus', async () => {
  const host = await backgroundSearch();
  commands = [detail('15'), detail('16')];
  for (const command of commands) snapshots.set(new URL(command.url).searchParams.get('id'),
    { href: command.url, ready: true, sequence: 1, attention: 'verification required' });
  await tick(); await tick(); await tick();
  assert.equal(windows.get(host.id).state, 'normal');
  const focusCount = windowEvents.filter(e => e.focused === true).length;
  for (const attention of attentions) attention.message = 'verification message changed';
  await tick();
  assert.equal(windowEvents.filter(e => e.focused === true).length, focusCount);
  snapshots.set('15', { href: detail('15').url, ready: true, sequence: 2, entries: [], domItem: { id: '15', title: 'ready', price: '10' } });
  await tick();
  assert.equal(windows.get(host.id).state, 'normal');
  snapshots.set('16', { href: detail('16').url, ready: true, sequence: 2, entries: [], domItem: { id: '16', title: 'ready', price: '10' } });
  await tick();
  assert.equal(windows.get(host.id).state, 'minimized');
  assert.equal(resumes.length, 2);
});

test('collection does not reuse a search window navigated to a different site by the user', async () => {
  const host = await backgroundSearch();
  tabs.get(host.tabs[0].id).url = 'https://example.com/user-page';
  commands = [detail('17')];
  await tick();
  assert.notEqual(storage['nativeJob:slot-17'].windowId, host.id);
  assert.equal(windowEvents.filter(e => e.operation === 'createTab').length, 0);
  commands = []; activeRun = null;
  await tick();
  assert.equal(tabs.size, 1);
  assert.equal(tabs.get(host.tabs[0].id).url, 'https://example.com/user-page');
});

test('collection and timeouts stay minimized while verification opens once and returns to background', async () => {
  activeRun = 'run'; attentions = []; keepSearch = false;
  const eventStart = windowEvents.length;
  const command = detail('12');
  commands = [command];
  await tick();
  const windowId = storage['nativeJob:slot-12'].windowId;
  assert.equal(windows.get(windowId).state, 'minimized');
  assert.equal(windows.get(windowId).focused, false);
  commands = [];
  attentions = [{ run_id: 'run', command_id: command.command_id, message: '页面采集超时', needs_verification: false }];
  snapshots.set('12', { href: command.url, ready: false });
  await tick();
  assert.equal(windowEvents.slice(eventStart).filter(e => e.focused === true).length, 0);
  assert.equal(windows.get(windowId).state, 'minimized');
  attentions[0].needs_verification = true;
  snapshots.set('12', { href: command.url, sequence: 1, attention: 'login required' });
  await tick(); await tick();
  assert.equal(windows.get(windowId).state, 'normal');
  assert.equal(windowEvents.slice(eventStart).filter(e => e.focused === true).length, 1);
  snapshots.set('12', { href: command.url, ready: true, sequence: 2, entries: [],
    domItem: { id: '12', title: 'ready', price: '10' } });
  await tick();
  assert.equal(windows.get(windowId).state, 'minimized');
  assert.equal(windows.get(windowId).focused, false);
  assert.equal(storage['nativeJob:slot-12'].verificationShown, false);
  assert.ok(resumes.some(r => r.command_id === command.command_id));
  activeRun = null;
  await tick();
  assert.equal(tabs.size, 0);
});
