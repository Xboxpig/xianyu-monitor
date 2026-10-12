"""Register the desktop identity and submit native Windows toast notifications."""
import base64
import ctypes
import json
import re
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse

APP_ID = "Xianyu.AutoAgent"
APP_NAME = "闲鱼 AutoAgent"
ICON_PATH = Path(__file__).parent / "assets" / "xianyu.png"
_lock = threading.Lock()
_registered = False


def register_identity():
    global _registered
    if sys.platform != "win32":
        raise RuntimeError("Windows 通知仅支持 Windows。")
    with _lock:
        if _registered:
            return
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Software\\Classes\\AppUserModelId\\" + APP_ID) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(ICON_PATH.resolve()))
            winreg.SetValueEx(key, "ShowInSettings", 0, winreg.REG_DWORD, 1)
        result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        if result:
            raise OSError("无法注册 Windows 应用标识：" + str(result))
        _run_helper("windows-register.ps1", {
            "app_id": APP_ID, "name": APP_NAME, "python": sys.executable,
            "directory": str(Path(__file__).resolve().parents[3]),
        })
        _registered = True


def _xml_text(value, limit):
    return re.sub(r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]", "", str(value))[:limit]


def build_payload(title, body, *, url="", tag="", group="runtime"):
    parsed = urlparse(url)
    if url and (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password):
        raise ValueError("通知链接必须是 HTTP/HTTPS 地址。")
    return {
        "app_id": APP_ID, "title": _xml_text(title, 160), "body": _xml_text(body, 600),
        "url": url[:2048], "icon": ICON_PATH.resolve().as_uri(),
        "tag": str(tag)[:16], "group": str(group)[:64],
    }


def show_toast(title, body, *, url="", tag="", group="runtime"):
    payload = build_payload(title, body, url=url, tag=tag, group=group)
    register_identity()
    _run_helper("windows-toast.ps1", payload)
    return True


def _run_helper(script, payload):
    encoded = base64.b64encode(json.dumps(payload, ensure_ascii=True).encode("utf-8")).decode("ascii")
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-WindowStyle", "Hidden", "-File", str(Path(__file__).with_name(script)),
         "-Payload", encoded],
        capture_output=True, encoding="utf-8", errors="replace", timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if completed.returncode:
        raise RuntimeError(completed.stdout.strip() or completed.stderr.strip() or "Windows 通知提交失败。")
