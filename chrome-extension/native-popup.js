async function render() {
  const { nativeConnection: connection, nativeConfig: config } = await chrome.storage.local.get(["nativeConnection", "nativeConfig"]);
  const message = !config?.enabled ? "尚未启用采集连接" : connection?.error ||
    connection?.attention?.message || (connection?.online ? "已连接本机服务" : "正在连接本机服务");
  document.getElementById("nativeStatus").textContent = message;
  document.getElementById("resumeNative").disabled = !connection?.attention;
}
document.getElementById("openNativeOptions").addEventListener("click", () => chrome.runtime.openOptionsPage());
document.getElementById("resumeNative").addEventListener("click", async () => {
  const result = await chrome.runtime.sendMessage({ type: "nativeResume" });
  document.getElementById("nativeStatus").textContent = result?.ok ? "已继续，正在读取页面。" : result?.error || "连接失败";
});
chrome.storage.onChanged.addListener(() => { void render(); });
void render();
