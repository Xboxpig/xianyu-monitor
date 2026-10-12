async function render() {
  const { nativeConnection: connection, nativeConfig: config } = await chrome.storage.local.get(["nativeConnection", "nativeConfig"]);
  const message = !config?.enabled ? "尚未启用采集连接" : connection?.error ||
    connection?.attention?.message || (connection?.online ? "已连接本机服务" : "正在连接本机服务");
  document.getElementById("nativeStatus").textContent = message;
}
document.getElementById("openNativeOptions").addEventListener("click", () => chrome.runtime.openOptionsPage());
chrome.storage.onChanged.addListener(() => { void render(); });
void render();
