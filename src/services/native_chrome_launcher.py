"""Launch installed Chrome without a browser automation driver."""
import os
import subprocess
import sys
import time
from urllib.request import urlopen
from pathlib import Path


def find_chrome() -> Path:
    if sys.platform != "win32":
        raise RuntimeError("此启动入口仅用于 Windows。")
    candidates = []
    if os.getenv("CHROME_PATH"):
        candidates.append(Path(os.environ["CHROME_PATH"]))
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as key:
                candidates.append(Path(winreg.QueryValue(key, None)))
        except OSError:
            pass
    for root in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        if os.getenv(root):
            candidates.append(Path(os.environ[root]) / "Google/Chrome/Application/chrome.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError("未找到已安装的 Chrome，请安装 Chrome 或设置 CHROME_PATH。")


def launch_chrome(url: str) -> None:
    subprocess.Popen([str(find_chrome()), "--new-window", url], close_fds=True)


if __name__ == "__main__":
    import argparse
    from urllib.parse import urlparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-url", required=True)
    args = parser.parse_args()
    target = urlparse(args.wait_url)
    if target.scheme != "http" or target.hostname not in ("127.0.0.1", "localhost", "::1"):
        parser.error("The startup URL must point to localhost.")
    for _ in range(60):
        try:
            with urlopen("http://" + target.netloc + "/health", timeout=1) as response:
                if response.status == 200:
                    launch_chrome(args.wait_url)
                    break
        except OSError:
            pass
        time.sleep(1)
    else:
        raise SystemExit("The local backend did not become ready within 60 seconds.")
