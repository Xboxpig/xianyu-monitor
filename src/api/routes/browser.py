"""Local, token-authenticated bridge for the Chrome extension."""
import secrets
import re
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from src.api.dependencies import get_process_service
from src.domain.models.browser import BrowserClient, BrowserReport, BrowserResume, BrowserSetup
from src.services.native_browser_service import NativeBrowserService
from src.services.native_chrome_launcher import launch_chrome
from src.infrastructure.desktop.chrome_profiles import list_chrome_profiles, require_chrome_profile

router = APIRouter(prefix="/api/browser", tags=["browser"])


def require_local(request: Request):
    if (not request.client or request.client.host not in ("127.0.0.1", "::1")
            or request.url.hostname not in ("127.0.0.1", "::1", "localhost")):
        raise HTTPException(403, "Chrome bridge is only available on localhost.")
    origin = request.headers.get("origin", "")
    if origin and not re.fullmatch(r"chrome-extension://[a-p]{32}", origin):
        from urllib.parse import urlparse
        parsed = urlparse(origin)
        if parsed.netloc != request.url.netloc or parsed.scheme != request.url.scheme:
            raise HTTPException(403, "Cross-origin access is not allowed.")


def get_native(request: Request, service=Depends(get_process_service)):
    require_local(request)
    if not isinstance(service, NativeBrowserService):
        raise HTTPException(409, "请使用 Windows 启动入口或设置 SCRAPER_BACKEND=native_chrome。")
    return service


def authenticated(service=Depends(get_native), token: str = Header(default="", alias="X-Xianyu-Token")):
    if not secrets.compare_digest(token, service.token()):
        raise HTTPException(401, "扩展配对令牌无效。")
    return service


@router.get("/status")
async def status(request: Request, service=Depends(get_process_service)):
    if isinstance(service, NativeBrowserService):
        require_local(request)
        return service.status()
    return {"backend": "playwright", "connected": False, "clients": [], "runs": []}


@router.post("/setup")
async def setup(data: BrowserSetup, service=Depends(get_native)):
    if data.launch:
        try:
            launch_chrome("https://www.goofish.com", profile_directory=data.profile_directory)
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"token": service.token()}


@router.get("/profiles")
async def profiles(service=Depends(get_native)):
    return {"profiles": list_chrome_profiles()}


@router.post("/poll")
async def poll(data: BrowserClient, service=Depends(authenticated)):
    if data.profile_directory:
        try:
            require_chrome_profile(data.profile_directory)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        data.account_state_file = "chrome-profile:" + data.profile_directory
    return await service.poll(data)


@router.post("/report")
async def report(data: BrowserReport, request: Request, service=Depends(authenticated)):
    if len(await request.body()) > 2_000_000:
        raise HTTPException(413, "Collection payload is too large.")
    try:
        return await service.report(data)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/resume")
async def resume(data: BrowserResume, service=Depends(authenticated)):
    try:
        return await service.resume(data.client_id, data.run_id, data.command_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
