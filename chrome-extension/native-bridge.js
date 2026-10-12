import { readPage, interactPage, collectSnapshot } from "./collector.js";

const ALARM = "xianyu-native-collector";
let busy = false;
let timer;

export function validateEndpoint(value) {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) ||
      url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
    throw new Error("服务地址必须是本机 HTTP 地址，例如 http://127.0.0.1:8000");
  }
  return url.origin;
}

async function request(config, path, body) {
  const response = await fetch(validateEndpoint(config.endpoint) + "/api/browser/" + path, {
    method: "POST", headers: { "Content-Type": "application/json", "X-Xianyu-Token": config.token },
    body: JSON.stringify(body), cache: "no-store", signal: AbortSignal.timeout(20000),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof data.detail === "string" ? data.detail : "请求失败：" + response.status);
    error.status = response.status;
    throw error;
  }
  return data;
}

async function save(state) {
  await chrome.storage.local.set({ [state.slotId ? 'nativeJob:' + state.slotId : 'nativeState']: state });
}

async function closeOwnedTabs(state) {
  for (const id of Object.values(state.tabs || {})) {
    try {
      const tab = await chrome.tabs.get(id);
      // A user may have navigated an owned tab elsewhere. Leave that tab alone.
      if (tab.windowId === state.windowId && new URL(tab.url).hostname === "www.goofish.com") {
        await chrome.tabs.remove(id);
      }
    } catch (_) {}
  }
}

async function showAttention(state, message) {
  if (state.attention !== message) {
    try { await chrome.windows.update(state.windowId, { state: "normal", focused: true }); } catch (_) {}
    const id = state.tabs?.[state.tabKey];
    if (id) { try { await chrome.tabs.update(id, { active: true }); } catch (_) {} }
  }
  state.attention = message;
  await save(state);
}

async function execute(tabId, func, args) {
  const result = await chrome.scripting.executeScript({ target: { tabId }, world: "MAIN", func, args });
  return result[0]?.result;
}

export function pageCheckpoint(snapshot) {
  return JSON.stringify([snapshot.href, snapshot.sequence || 0, !!snapshot.attention,
    snapshot.currentPage, snapshot.domItems?.length, snapshot.domItem?.id, snapshot.empty]);
}

async function resumeWhenReady(state, attention) {
  const command = state.lastCommand;
  if (!command || command.run_id !== attention.run_id) return;
  let tab;
  try { tab = await chrome.tabs.get(state.tabs?.[state.tabKey]); } catch (_) { return; }
  if (tab.windowId !== state.windowId || tab.status !== 'complete' || new URL(tab.url).hostname !== 'www.goofish.com') return;
  const expected = new URL(command.url);
  const actual = new URL(tab.url);
  if (actual.pathname !== expected.pathname) return;
  const idKey = command.action === 'detail' ? 'id' : command.action === 'seller' ? 'userId' : null;
  if (idKey && actual.searchParams.get(idKey) !== expected.searchParams.get(idKey)) return;
  const snapshot = await execute(tab.id, readPage, [command.action]);
  if (!snapshot?.ready || snapshot.attention) return;
  const result = collectSnapshot(snapshot, command);
  if (result?.outcome !== 'ok') return;
  const updatedFilters = state.phase === 'filters' && attention.message.includes('发布时间筛选') &&
    state.collectorVersion !== chrome.runtime.getManifest().version;
  if (state.phase === 'filters' && attention.message.includes('发布时间筛选') && !updatedFilters) return;
  if (!updatedFilters && !attention.message.includes('扩展已断开') && pageCheckpoint(snapshot) === state.attentionCheckpoint) return;
  if (state.slotId) {
    const { nativeConfig: config } = await chrome.storage.local.get('nativeConfig');
    await request(config, 'resume', { client_id: config.client_id, run_id: attention.run_id, command_id: attention.command_id });
    state.attention = '';
    state.command = null;
    state.collectorVersion = chrome.runtime.getManifest().version;
    if (state.phase === 'filters') state.filterAttempts = 0;
    await save(state);
  } else await resume();
}

async function ensureTab(state, command) {
  const target = new URL(command.url);
  const expectedPath = command.action === "detail" ? "/item" : command.action === "seller" ? "/personal" : "/search";
  if (target.protocol !== "https:" || target.hostname !== "www.goofish.com" || target.pathname !== expectedPath) {
    throw new Error("后端返回了不支持的采集页面地址。");
  }
  state.tabKey = state.slotId ? 'page' : command.action.startsWith("search") ? "search" : command.action;
  state.tabs ||= {};
  let tab;
  try { tab = await chrome.tabs.get(state.tabs[state.tabKey]); } catch (_) {}
  if (tab && tab.windowId !== state.windowId) tab = null;
  if (tab && new URL(tab.url).hostname !== 'www.goofish.com') {
    // The user took over this window. Keep it and create a new owned collection window.
    tab = null;
    state.tabs = {};
    delete state.windowId;
  }
  if (!tab) {
    let window;
    try { window = await chrome.windows.get(state.windowId); } catch (_) {}
    if (!window) {
      window = await chrome.windows.create({ url: command.url, state: "minimized", focused: false });
      state.windowId = window.id;
      tab = window.tabs[0];
    } else {
      tab = await chrome.tabs.create({ windowId: window.id, url: command.url, active: false });
    }
    state.tabs[state.tabKey] = tab.id;
    if (command.action.startsWith('search')) {
      state.searchPage = 1;
      state.needsFilters = true;
    }
    await save(state);
  } else if (!command.action.startsWith("search") && tab.url !== command.url) {
    await chrome.tabs.update(tab.id, { url: command.url, active: false });
  }
  return tab.id;
}

async function collectStep(state) {
  const command = state.command;
  if (state.phase === "navigate") {
    await ensureTab(state, command);
    state.phase = "load";
    await save(state);
    return;
  }
  const tabId = state.tabs[state.tabKey];
  let tab;
  try { tab = await chrome.tabs.get(tabId); } catch (_) {
    await queueReport(state, { outcome: "attention", message: "采集标签页已关闭，请在管理界面停止任务后重新启动。" });
    return;
  }
  if (tab.discarded) {
    await chrome.tabs.reload(tabId);
    return;
  }
  if (tab.status !== "complete") return;
  if (!/(^|\.)goofish\.com$/.test(new URL(tab.url).hostname)) {
    await queueReport(state, { outcome: "attention", message: "采集标签页已离开闲鱼。请停止任务后重新启动；其他网站的标签页会保留。" });
    return;
  }
  const snapshot = await execute(tabId, readPage, [command.action]);
  state.pageCheckpoint = pageCheckpoint(snapshot || {});
  // Chrome Stable reports JS heap only. Add a conservative native/DOM/image reserve;
  // retain the peak while this window exists so navigation cannot release budget early.
  if (state.slotId) state.memoryMb = Math.max(state.memoryMb || 256, 256,
    Math.ceil((snapshot?.heapBytes || 0) * 2 / 1048576) + 128);
  if (command.resume && snapshot?.sequence < state.searchSequenceFloor) state.searchSequenceFloor = 0;
  if (snapshot?.attention) {
    await queueReport(state, { outcome: "attention", message: snapshot.attention });
    return;
  }
  if (state.phase === "turning") {
    const received = (snapshot.entries || []).some(entry => entry.url.includes("idlemtopsearch.pc.search") && entry.sequence > state.searchSequenceFloor);
    if (received || (snapshot.currentPage && snapshot.currentPage > state.searchPage)) {
      state.searchPage = snapshot.currentPage || state.searchPage + 1;
      state.phase = state.searchPage < command.page ? "load" : "read";
      await save(state);
    } else if (Date.now() - state.startedAt > 120000) {
      await queueReport(state, { outcome: "attention", message: "分页请求未返回。请检查搜索页面，数据恢复后将自动继续。" });
    }
    return;
  }
  if (state.phase === "load") {
    if (command.action === "search_next") {
      if (state.needsFilters) {
        state.phase = 'filters';
        await save(state);
        return;
      }
      state.searchPage = snapshot.currentPage || state.searchPage || 1;
      if (state.searchPage >= command.page) {
        state.phase = "read";
        state.searchSequenceFloor = null;
        await save(state);
        return;
      }
      // Persist the transition before clicking, so worker restart waits for its response.
      state.phase = "turning";
      state.searchSequenceFloor = snapshot.sequence || 0;
      await save(state);
      const clicked = await execute(tabId, interactPage, ["next"]);
      if (!clicked?.clicked) {
        await queueReport(state, { outcome: "ok", items: [], has_more: false });
        return;
      }
      state.searchSequenceFloor = clicked.sequenceFloor;
      state.reads = 0;
    } else if (command.action === "search") {
      state.phase = "filters";
    } else {
      state.phase = command.action === "seller" ? "seller_items" : "read";
    }
    await save(state);
    return;
  }
  if (state.phase === "filters") {
    const result = await execute(tabId, interactPage, ["filters", command.task]);
    if (result?.changed) state.searchSequenceFloor = result.sequenceFloor;
    if (result?.pending) {
      if (Date.now() - state.startedAt > 120000) {
        await queueReport(state, { outcome: "error", message: result.error + "自动筛选超时。" });
      } else {
        await save(state);
      }
      return;
    }
    if (result?.retry) {
      state.filterAttempts = (state.filterAttempts || 0) + 1;
      if (state.filterAttempts < 4) {
        await save(state);
      } else {
        await queueReport(state, { outcome: "error", message: result.error + "扩展自动重试失败。" });
      }
      return;
    }
    if (result?.attention) {
      await queueReport(state, { outcome: "attention", message: result.attention });
      return;
    }
    state.personalApplied = !!result?.personalApplied;
    state.regionApplied = !!result?.regionApplied;
    state.searchSequenceFloor ??= null;
    state.needsFilters = false;
    state.phase = command.action === 'search_next' ? 'load' : 'read';
    state.reads = 0;
    await save(state);
    return;
  }
  if (state.phase === "seller_items" || state.phase === "seller_ratings") {
    const pattern = state.phase === "seller_items" ? "xyh.item.list" : "trade.rate.list";
    const responses = (snapshot.entries || []).filter(entry => entry.url.includes(pattern));
    const head = (snapshot.entries || []).filter(entry => entry.url.includes('mtop.idle.web.user.page.head')).at(-1);
    const total = head?.body?.data?.module?.tabs?.[state.phase === 'seller_items' ? 'item' : 'rate']?.number;
    const emptyList = total != null && Number(total) === 0;
    if (!responses.length && !emptyList) {
      if (Date.now() - state.startedAt > 120000) {
        await queueReport(state, { outcome: 'attention', message: '卖家列表请求未返回。请检查页面，数据恢复后将自动继续。' });
      } else await save(state);
      return;
    }
    const count = responses.reduce((sum, entry) => sum + (entry.body?.data?.cardList?.length || 0), 0);
    state.unchanged = count === state.previousCount ? (state.unchanged || 0) + 1 : 0;
    state.previousCount = count;
    const finished = emptyList || responses.at(-1)?.body?.data?.nextPage === false || state.unchanged >= 3 || count >= 500;
    if (finished) {
      if (state.phase === "seller_items") {
        const result = await execute(tabId, interactPage, ["ratings"]);
        state.phase = result?.clicked ? "seller_ratings" : "read";
        state.unchanged = 0;
        state.previousCount = -1;
      } else {
        state.phase = "read";
      }
    } else {
      await execute(tabId, interactPage, ["scroll"]);
    }
    await save(state);
    return;
  }
  state.reads = (state.reads || 0) + 1;
  let result;
  const awaitingSearch = command.action.startsWith("search") && state.searchSequenceFloor != null &&
    !(snapshot.entries || []).some(entry => entry.url.includes("idlemtopsearch.pc.search") && entry.sequence > state.searchSequenceFloor);
  if (!awaitingSearch) result = collectSnapshot(snapshot || {}, command);
  if (result) {
    await queueReport(state, {
      ...result, personal_applied: state.personalApplied || false,
      region_applied: state.regionApplied || false,
    });
  } else if (Date.now() - state.startedAt > 120000) {
    await queueReport(state, { outcome: "attention", message: "未读取到页面数据。请检查登录状态和页面内容，数据恢复后将自动继续；商品不存在时请停止任务。" });
  } else {
    await save(state);
  }
}

async function queueReport(state, result) {
  state.outbox = {
    ...result, client_id: state.clientId, run_id: state.command.run_id,
    command_id: state.command.command_id,
  };
  // Save before HTTP delivery so service-worker suspension cannot lose the result.
  await save(state);
  if (result.outcome === "attention") {
    state.attentionCheckpoint = state.pageCheckpoint;
    await showAttention(state, result.message);
  }
}

async function legacyTick() {
  if (busy) return;
  busy = true;
  let config;
  try {
    const stored = await chrome.storage.local.get(["nativeConfig", "nativeState"]);
    config = stored.nativeConfig;
    if (!config?.enabled || !config.token) return;
    const state = stored.nativeState || {};
    state.clientId = config.client_id;
    if (state.outbox) {
      try {
        await request(config, "report", state.outbox);
      } catch (error) {
        if (error.status !== 409) throw error;
        await closeOwnedTabs(state);
        await chrome.storage.local.remove("nativeState");
        return;
      }
      delete state.outbox;
      state.command = null;
      await save(state);
    }
    const response = await request(config, "poll", {
      client_id: config.client_id, name: config.name || "Chrome",
      account_state_file: config.account_state_file || null, version: chrome.runtime.getManifest().version,
      profile_directory: config.profile_directory || null,
    });
    await chrome.storage.local.set({ nativeConnection: { online: true, error: "", checkedAt: Date.now(), attention: response.attention } });
    if (response.attention) {
      await showAttention(state, response.attention.message);
      await resumeWhenReady(state, response.attention);
      return;
    }
    const command = response.command;
    if (!command) {
      if (state.runId && !response.active_run_id) {
        await closeOwnedTabs(state);
        await chrome.storage.local.remove("nativeState");
      }
      return;
    }
    if (state.runId && state.runId !== command.run_id) {
      await closeOwnedTabs(state);
      state.tabs = {};
      delete state.windowId;
    }
    if (state.command?.command_id !== command.command_id) {
      const keepCheckpoint = command.resume && state.lastCommand?.run_id === command.run_id &&
        state.lastCommand?.action === command.action && state.lastCommand?.page === command.page;
      state.runId = command.run_id;
      state.command = command;
      state.lastCommand = command;
      state.startedAt = Date.now();
      if (!keepCheckpoint) state.phase = "navigate";
      state.reads = 0;
      state.unchanged = 0;
      state.previousCount = -1;
      if (!keepCheckpoint) state.searchSequenceFloor = null;
      if (!keepCheckpoint) state.filterAttempts = 0;
      state.collectorVersion = chrome.runtime.getManifest().version;
      state.attention = "";
      await save(state);
    }
    await collectStep(state);
  } catch (error) {
    await chrome.storage.local.set({ nativeConnection: { online: false, error: error.message, checkedAt: Date.now() } });
  } finally {
    busy = false;
    clearTimeout(timer);
    if (config?.enabled) timer = setTimeout(tick, 3000);
  }
}

async function resume() {
  const { nativeConfig: config, nativeState: state, nativeConnection: connection } =
    await chrome.storage.local.get(["nativeConfig", "nativeState", "nativeConnection"]);
  const runId = connection?.attention?.run_id || state?.runId;
  if (!runId) throw new Error("当前没有等待继续的任务。");
  if (state?.outbox) {
    await request(config, "report", state.outbox);
    delete state.outbox;
    await save(state);
  }
  await request(config, "resume", { client_id: config.client_id, run_id: runId });
  if (state) {
    // Preserve the page and pending interaction after login or an extension update.
    state.command = null;
    state.attention = "";
    state.collectorVersion = chrome.runtime.getManifest().version;
    if (state.phase === "filters") state.filterAttempts = 0;
    try {
      await chrome.tabs.get(state.tabs?.[state.tabKey]);
    } catch (_) {
      state.phase = "navigate";
      delete state.lastCommand;
    }
    await save(state);
  }
  void tick();
}

let parallelBusy = false;

async function retire(state) {
  await closeOwnedTabs(state);
  await chrome.storage.local.remove('nativeJob:' + state.slotId);
}

export async function tick() {
  if (parallelBusy || busy) return;
  parallelBusy = true;
  let config;
  try {
    const stored = await chrome.storage.local.get(['nativeConfig', 'nativeParallelState', 'nativeState']);
    config = stored.nativeConfig;
    if (!config?.enabled || !config.token) return;
    const root = stored.nativeParallelState || { slots: [] };
    const keys = root.slots.map(id => 'nativeJob:' + id);
    const saved = keys.length ? await chrome.storage.local.get(keys) : {};
    const jobs = Object.fromEntries(root.slots.flatMap(id => saved['nativeJob:' + id] ? [[id, saved['nativeJob:' + id]]] : []));
    const deliveries = await Promise.allSettled(Object.values(jobs).filter(s => s.outbox).map(async state => {
      try { await request(config, 'report', state.outbox); }
      catch (error) {
        if (error.status !== 409) throw error;
        await retire(state);
        delete jobs[state.slotId];
        return;
      }
      delete state.outbox;
      state.command = null;
      await save(state);
    }));
    const failed = deliveries.find(result => result.status === 'rejected');
    if (failed) throw failed.reason;
    const response = await request(config, 'poll', {
      client_id: config.client_id, name: config.name || 'Chrome', parallel: true,
      account_state_file: config.account_state_file || null, profile_directory: config.profile_directory || null,
      version: chrome.runtime.getManifest().version,
      windows: Object.values(jobs).filter(s => s.windowId).map(s => ({
        run_id: s.runId, slot_id: s.slotId, memory_mb: s.memoryMb || 256,
      })),
    });
    // Allow an older backend to finish a task during a rolling upgrade.
    if (!Array.isArray(response.commands)) {
      parallelBusy = false;
      await legacyTick();
      return;
    }
    if (stored.nativeState) {
      await closeOwnedTabs(stored.nativeState);
      await chrome.storage.local.remove('nativeState');
    }
    await chrome.storage.local.set({ nativeConnection: { online: true, error: '', checkedAt: Date.now(), attention: response.attention } });
    const commands = response.commands;
    const attentions = response.attentions || (response.attention ? [response.attention] : []);
    const liveIds = new Set([...commands.map(c => c.command_id), ...attentions.map(a => a.command_id)]);
    for (const [id, state] of Object.entries(jobs)) {
      const keepSearch = state.runId === response.active_run_id && response.keep_search && state.slotId === 'search';
      if (state.runId !== response.active_run_id || (!keepSearch && !liveIds.has(state.lastCommand?.command_id) && !commands.some(c => c.slot_id === id))) {
        await retire(state);
        delete jobs[id];
      }
    }
    for (const command of commands) {
      const id = command.slot_id;
      let state = jobs[id];
      if (!state) {
        state = jobs[id] = { slotId: id, tabs: {}, memoryMb: 256 };
      }
      if (state.command?.command_id !== command.command_id) {
        const keepCheckpoint = command.resume && state.lastCommand?.action === command.action && state.lastCommand?.page === command.page;
        Object.assign(state, { runId: command.run_id, clientId: config.client_id,
          command, lastCommand: command, startedAt: Date.now(), reads: 0, unchanged: 0,
          previousCount: -1, collectorVersion: chrome.runtime.getManifest().version, attention: '' });
        if (!keepCheckpoint) Object.assign(state, { phase: 'navigate', searchSequenceFloor: null, filterAttempts: 0 });
        await save(state);
      }
    }
    root.slots = Object.keys(jobs);
    await chrome.storage.local.set({ nativeParallelState: root });
    const steps = await Promise.allSettled(Object.values(jobs).map(async state => {
      const attention = attentions.find(a => a.command_id === state.lastCommand?.command_id);
      if (attention) {
        await showAttention(state, attention.message);
        await resumeWhenReady(state, attention);
      } else if (state.command && liveIds.has(state.command.command_id)) {
        await collectStep(state);
      }
    }));
    const stepError = steps.find(result => result.status === 'rejected');
    if (stepError) throw stepError.reason;
  } catch (error) {
    await chrome.storage.local.set({ nativeConnection: { online: false, error: error.message, checkedAt: Date.now() } });
  } finally {
    parallelBusy = false;
    clearTimeout(timer);
    if (config?.enabled) timer = setTimeout(tick, 1000);
  }
}

chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === ALARM) void tick(); });
chrome.runtime.onStartup.addListener(() => { void chrome.alarms.create(ALARM, { periodInMinutes: 0.5 }); void tick(); });
chrome.runtime.onInstalled.addListener(() => { void chrome.alarms.create(ALARM, { periodInMinutes: 0.5 }); });
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id) return false;
  if (message?.type === "nativeConnect") {
    void tick();
    respond({ ok: true });
    return false;
  }
  return false;
});
void chrome.alarms.create(ALARM, { periodInMinutes: 0.5 });
void tick();
