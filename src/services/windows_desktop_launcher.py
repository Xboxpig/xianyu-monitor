"""Run the native Windows backend with a graceful tray exit."""
import argparse
import asyncio
import contextlib
import logging
import os

import uvicorn

from src.services.native_chrome_launcher import find_chrome, launch_chrome
from src.services.windows_desktop_service import windows_desktop_service


async def run(port, *, open_browser=True):
    if open_browser:
        find_chrome()
    server = uvicorn.Server(uvicorn.Config("src.app:app", host="127.0.0.1", port=port))

    def quit_server():
        server.should_exit = True

    url = "http://127.0.0.1:" + str(port)
    windows_desktop_service.configure(url, quit_server)

    async def open_when_ready():
        while not server.started and not server.should_exit:
            await asyncio.sleep(0.1)
        if open_browser and server.started:
            try:
                await asyncio.to_thread(launch_chrome, url + "/settings?tab=browser")
            except Exception:
                logging.getLogger(__name__).exception("无法打开 Chrome 管理界面，请使用托盘菜单重试")

    browser = asyncio.create_task(open_when_ready())
    try:
        await server.serve()
        if not server.started:
            raise RuntimeError("Windows 后端启动失败，请检查启动日志。")
    finally:
        browser.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await browser
        await asyncio.to_thread(windows_desktop_service.stop)
        windows_desktop_service.on_quit = None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.getenv("SERVER_PORT", "8000")))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    asyncio.run(run(args.port, open_browser=not args.no_browser))


if __name__ == "__main__":
    main()
