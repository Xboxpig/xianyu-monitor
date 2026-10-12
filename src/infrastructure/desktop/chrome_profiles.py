"""Discover existing Windows Chrome profiles from Chrome's Local State."""
import json
import os
import sys
from pathlib import Path


def chrome_user_data() -> Path:
    configured = os.getenv("CHROME_USER_DATA_DIR")
    if configured:
        return Path(configured).resolve()
    return (Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/User Data").resolve()


def list_chrome_profiles() -> list[dict]:
    if sys.platform != "win32":
        return []
    root = chrome_user_data()
    try:
        cache = json.loads((root / "Local State").read_text(encoding="utf-8"))["profile"]["info_cache"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    if not isinstance(cache, dict):
        return []
    profiles = []
    for directory, info in sorted(cache.items()):
        if (not isinstance(info, dict) or not directory or len(directory) > 100
                or any(char in directory for char in '/\\:\x00') or directory in (".", "..")):
            continue
        path = root / directory
        if path.resolve().parent != root or not path.is_dir():
            continue
        profiles.append({
            "directory": directory, "name": str(info.get("name") or directory),
            "email": str(info.get("user_name") or ""), "path": "chrome-profile:" + directory,
        })
    return profiles


def require_chrome_profile(directory: str) -> str:
    if not any(profile["directory"] == directory for profile in list_chrome_profiles()):
        raise ValueError("所选 Chrome 账户不存在，请刷新账户列表。")
    return directory
