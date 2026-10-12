"""Windows notification-center delivery."""
import asyncio
import hashlib
import sys

from .base import NotificationClient
from src.infrastructure.desktop.windows_notifications import show_toast


class WindowsToastClient(NotificationClient):
    channel_key = "windows"
    display_name = "Windows 通知"

    def __init__(self, enabled=False):
        super().__init__(enabled=bool(enabled) and sys.platform == "win32", pcurl_to_mobile=False)

    async def send(self, product_data, reason):
        message = self._build_message(product_data, reason)
        url = message.desktop_link if message.desktop_link != "#" else ""
        tag = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        return await asyncio.to_thread(
            show_toast, message.notification_title,
            "价格：" + str(message.price) + "\n" + str(reason),
            url=url, tag=tag, group="recommendations",
        )
