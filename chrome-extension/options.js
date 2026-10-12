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
  get("account").value = stored.account_state_file || "";
  get("enabled").checked = !!stored.enabled;
}
form.addEventListener("submit", async event => {
  event.preventDefault();
  try {
    const { nativeState: state } = await chrome.storage.local.get("nativeState");
    if (state?.command || state?.outbox) throw new Error("请先在管理界面停止当前采集任务，再修改连接设置。");
    const config = {
      endpoint: endpoint(get("endpoint").value.trim()), token: get("token").value.trim(),
      name: get("name").value.trim(), account_state_file: get("account").value.trim() || null,
      enabled: get("enabled").checked, client_id: clientId,
    };
    await chrome.storage.local.set({ nativeConfig: config });
    await chrome.runtime.sendMessage({ type: "nativeConnect" });
    status.textContent = config.enabled ? "设置已保存，正在连接。可在管理界面查看连接状态。" : "后台连接已关闭。";
  } catch (error) {
    status.textContent = error.message;
  }
});
