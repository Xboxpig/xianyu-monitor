const form = document.getElementById("settings");
const status = document.getElementById("status");
const get = id => document.getElementById(id);
function endpoint(value) {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) ||
      url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
    throw new Error("请填写本机 HTTP 地址，例如 http://127.0.0.1:8000");
  }
  return url.origin;
}
const { nativeConfig: stored } = await chrome.storage.local.get("nativeConfig");
const clientId = stored?.client_id || crypto.randomUUID();
if (stored) {
  for (const id of ["endpoint", "token", "name"]) get(id).value = stored[id] || "";
  get("enabled").checked = !!stored.enabled;
}
async function browserRequest(path, options = {}) {
  const response = await fetch(endpoint(get('endpoint').value.trim()) + '/api/browser/' + path, {
    ...options, headers: { 'Content-Type': 'application/json', 'X-Xianyu-Token': get('token').value.trim() },
    cache: 'no-store', signal: AbortSignal.timeout(20000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
  return result;
}
async function refreshProfiles() {
  try {
    const selected = get('account').value || stored?.profile_directory || '';
    const { profiles } = await browserRequest('profiles');
    get('account').replaceChildren(new Option('请选择 Chrome 账户', ''));
    for (const profile of profiles) {
      get('account').add(new Option(profile.name + (profile.email ? ' · ' + profile.email : ''), profile.directory));
    }
    if (profiles.some(profile => profile.directory === selected)) get('account').value = selected;
    else if (profiles.length === 1) get('account').value = profiles[0].directory;
    status.textContent = profiles.length ? `检测到 ${profiles.length} 个 Chrome 账户。` : '未检测到 Chrome 账户，请先打开 Chrome。';
  } catch (error) { status.textContent = error.message; }
}
get('refreshProfiles').addEventListener('click', refreshProfiles);
get('openProfile').addEventListener('click', async () => {
  try {
    if (!get('account').value) throw new Error('请先选择 Chrome 账户。');
    await browserRequest('setup', { method: 'POST', body: JSON.stringify({ launch: true, profile_directory: get('account').value }) });
    status.textContent = '已打开所选 Chrome 账户，请在该窗口启用采集扩展。';
  } catch (error) { status.textContent = error.message; }
});
void refreshProfiles();
form.addEventListener("submit", async event => {
  event.preventDefault();
  try {
    const { nativeState: state } = await chrome.storage.local.get("nativeState");
    if (state?.runId || state?.command || state?.outbox) throw new Error("请先在管理界面停止当前采集任务，再修改连接设置。");
    if (!get('account').value) throw new Error('请先选择 Chrome 账户。');
    const config = {
      endpoint: endpoint(get("endpoint").value.trim()), token: get("token").value.trim(),
      name: get("name").value.trim(), profile_directory: get("account").value,
      account_state_file: 'chrome-profile:' + get("account").value,
      enabled: get("enabled").checked, client_id: clientId,
    };
    await chrome.storage.local.set({ nativeConfig: config });
    await chrome.runtime.sendMessage({ type: "nativeConnect" });
    status.textContent = config.enabled ? "设置已保存，正在连接。可在管理界面查看连接状态。" : "后台连接已关闭。";
  } catch (error) {
    status.textContent = error.message;
  }
});
