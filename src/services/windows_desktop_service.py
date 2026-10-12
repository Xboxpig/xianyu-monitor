"""Manage the Windows tray and runtime notifications."""
import asyncio
import hashlib
import logging
import os
import sys
import threading

from src.infrastructure.desktop.windows_notifications import APP_NAME, ICON_PATH, register_identity, show_toast
from src.services.native_chrome_launcher import launch_chrome

logger = logging.getLogger(__name__)


class WindowsDesktopService:
    def __init__(self):
        self.url = "http://127.0.0.1:8000"
        self.on_quit = None
        self.icon = None
        self.thread = None
        self._ready = threading.Event()
        self._quitting = threading.Event()
        self._error = None

    def configure(self, url, on_quit):
        self.url = url
        self.on_quit = on_quit

    def start(self):
        if sys.platform != "win32" or os.getenv("WINDOWS_TRAY_ENABLED", "true").lower() in ("false", "0", "off"):
            return False
        if self.icon is not None:
            return True
        import pystray
        from PIL import Image
        register_identity()
        self._ready.clear()
        self._quitting.clear()
        self._error = None
        with Image.open(ICON_PATH) as image:
            icon_image = image.copy()
        self.icon = pystray.Icon(
            "xianyu-autoagent", icon_image, APP_NAME,
            pystray.Menu(
                pystray.MenuItem("打开管理界面", lambda: self.open_dashboard(), default=True),
                pystray.MenuItem("测试 Windows 通知", lambda: self.test_notification()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", lambda: self.request_quit(), enabled=lambda item: self.on_quit is not None),
            ),
        )

        def setup(icon):
            try:
                icon.visible = True
            except Exception as exc:
                self._error = exc
            finally:
                self._ready.set()

        def run():
            try:
                self.icon.run(setup=setup)
            except Exception as exc:
                self._error = exc
                self._ready.set()

        self.thread = threading.Thread(target=run, name="windows-tray", daemon=True)
        self.thread.start()
        if not self._ready.wait(5) or self._error:
            self.stop()
            raise RuntimeError("Windows 托盘注册失败。") from self._error
        return True

    def open_dashboard(self):
        try:
            launch_chrome(self.url)
        except Exception:
            logger.exception("无法打开 Chrome 管理界面")

    def request_quit(self):
        if self.on_quit and not self._quitting.is_set():
            self._quitting.set()
            self.on_quit()

    def stop(self):
        icon, self.icon = self.icon, None
        if icon:
            icon.stop()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=5)
        self.thread = None

    def enabled(self):
        if sys.platform != "win32":
            return False
        from src.services.notification_config_service import load_notification_settings
        return load_notification_settings().windows_toast_enabled

    def test_notification(self):
        threading.Thread(target=self._notify_sync, args=("Windows 通知测试", "闲鱼 AutoAgent 的 Windows 通知已连接。", self.url, "test"),
                         daemon=True).start()

    def _notify_sync(self, title, body, url, tag):
        if not self.enabled():
            return
        try:
            show_toast(title, body, url=url, tag=tag)
        except Exception:
            logger.exception("Windows 通知发送失败")

    async def notify_startup(self):
        if self.icon:
            await asyncio.to_thread(self._notify_sync, "闲鱼 AutoAgent 已启动", "服务正在运行，可通过托盘打开管理界面或退出。", self.url, "startup")

    async def notify_attention(self, run_id, task_name, message):
        if self.icon:
            tag = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
            await asyncio.to_thread(self._notify_sync, "需要处理：" + task_name, message, self.url + "/settings?tab=browser", tag)


windows_desktop_service = WindowsDesktopService()
