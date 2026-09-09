"""
设置管理路由
"""
import os
from types import SimpleNamespace
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.ai_reasoning import (
    DEFAULT_REASONING_EFFORT,
    ReasoningEffort,
    normalize_reasoning_effort,
)
from src.api.dependencies import get_process_service
from src.infrastructure.config.env_manager import env_manager
from src.infrastructure.config.settings import (
    AISettings,
    reload_settings,
    scraper_settings,
)
from src.infrastructure.external.ai_client import AIClient
from src.services.ai_endpoint_cache import EndpointCapabilityCache
from src.services.notification_config_service import (
    NotificationSettingsValidationError,
    build_configured_channels,
    build_notification_settings_response,
    build_notification_status_flags,
    load_notification_settings,
    model_dump,
    prepare_notification_test_settings,
    prepare_notification_settings_update,
)
from src.services.notification_service import build_notification_service
from src.services.llm_request_queue import global_llm_request_queue
from src.services.process_service import ProcessService


router = APIRouter(prefix="/api/settings", tags=["settings"])
AI_TEST_PROMPT = "Reply with OK only."
AI_TEST_MAX_OUTPUT_TOKENS = 32


def _reload_env() -> None:
    load_dotenv(dotenv_path=env_manager.env_file, override=True)
    reload_settings()


def _env_bool(key: str, default: bool = False) -> bool:
    value = env_manager.get_value(key)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(key: str, default: int) -> int:
    value = env_manager.get_value(key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _normalize_bool_value(value: bool) -> str:
    return "true" if value else "false"


class NotificationSettingsModel(BaseModel):
    """通知设置模型"""

    NTFY_TOPIC_URL: Optional[str] = None
    GOTIFY_URL: Optional[str] = None
    GOTIFY_TOKEN: Optional[str] = None
    BARK_URL: Optional[str] = None
    WX_BOT_URL: Optional[str] = None
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None
    TELEGRAM_API_BASE_URL: Optional[str] = None
    WEBHOOK_URL: Optional[str] = None
    WEBHOOK_METHOD: Optional[str] = None
    WEBHOOK_HEADERS: Optional[str] = None
    WEBHOOK_CONTENT_TYPE: Optional[str] = None
    WEBHOOK_QUERY_PARAMETERS: Optional[str] = None
    WEBHOOK_BODY: Optional[str] = None
    PCURL_TO_MOBILE: Optional[bool] = None


class NotificationTestRequest(BaseModel):
    """通知测试请求"""

    channel: Optional[str] = None
    settings: NotificationSettingsModel = Field(default_factory=NotificationSettingsModel)


class AISettingsModel(BaseModel):
    """AI设置模型"""

    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: Optional[str] = None
    OPENAI_MODEL_NAME: Optional[str] = None
    AI_API_MODE: Optional[Literal["auto", "chat_completions", "responses"]] = None
    AI_STREAM_MODE: Optional[Literal["auto", "sse", "off"]] = None
    AI_ENDPOINT_AUTO_DETECT: Optional[bool] = None
    AI_REASONING_EFFORT: Optional[ReasoningEffort] = None
    AI_ANALYSIS_CONCURRENCY: Optional[int] = Field(None, ge=1, le=32)
    SKIP_AI_ANALYSIS: Optional[bool] = None
    PROXY_URL: Optional[str] = None


class RotationSettingsModel(BaseModel):
    ACCOUNT_ROTATION_ENABLED: Optional[bool] = None
    ACCOUNT_ROTATION_MODE: Optional[str] = None
    ACCOUNT_ROTATION_RETRY_LIMIT: Optional[int] = None
    ACCOUNT_BLACKLIST_TTL: Optional[int] = None
    ACCOUNT_STATE_DIR: Optional[str] = None
    PROXY_ROTATION_ENABLED: Optional[bool] = None
    PROXY_ROTATION_MODE: Optional[str] = None
    PROXY_POOL: Optional[str] = None
    PROXY_ROTATION_RETRY_LIMIT: Optional[int] = None
    PROXY_BLACKLIST_TTL: Optional[int] = None


@router.get("/notifications")
async def get_notification_settings():
    return build_notification_settings_response(load_notification_settings())


@router.put("/notifications")
async def update_notification_settings(settings: NotificationSettingsModel):
    try:
        updates, deletions, merged_settings = prepare_notification_settings_update(
            model_dump(settings, exclude_unset=True),
            load_notification_settings(),
        )
    except NotificationSettingsValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    success = env_manager.apply_changes(updates=updates, deletions=deletions)
    if not success:
        raise HTTPException(status_code=500, detail="更新通知设置失败")

    _reload_env()
    return {
        "message": "通知设置已成功更新",
        "configured_channels": build_configured_channels(merged_settings),
    }


@router.post("/notifications/test")
async def test_notification_settings(payload: NotificationTestRequest):
    try:
        merged_settings = prepare_notification_test_settings(
            model_dump(payload.settings, exclude_unset=True),
            load_notification_settings(),
            channel=payload.channel,
        )
    except NotificationSettingsValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    service = build_notification_service(merged_settings)
    if not service.clients:
        if payload.channel:
            raise HTTPException(
                status_code=422,
                detail=f"渠道 {payload.channel} 未配置或不受支持",
            )
        raise HTTPException(status_code=422, detail="请至少配置一个可用的通知渠道")

    results = await service.send_test_notification()
    if payload.channel:
        if payload.channel not in results:
            raise HTTPException(
                status_code=422,
                detail=f"渠道 {payload.channel} 未配置或不受支持",
            )
        results = {payload.channel: results[payload.channel]}

    return {
        "message": "测试通知已执行",
        "results": results,
    }


@router.get("/rotation")
async def get_rotation_settings():
    return {
        "ACCOUNT_ROTATION_ENABLED": _env_bool("ACCOUNT_ROTATION_ENABLED", False),
        "ACCOUNT_ROTATION_MODE": env_manager.get_value("ACCOUNT_ROTATION_MODE", "per_task"),
        "ACCOUNT_ROTATION_RETRY_LIMIT": _env_int("ACCOUNT_ROTATION_RETRY_LIMIT", 2),
        "ACCOUNT_BLACKLIST_TTL": _env_int("ACCOUNT_BLACKLIST_TTL", 300),
        "ACCOUNT_STATE_DIR": env_manager.get_value("ACCOUNT_STATE_DIR", "state"),
        "PROXY_ROTATION_ENABLED": _env_bool("PROXY_ROTATION_ENABLED", False),
        "PROXY_ROTATION_MODE": env_manager.get_value("PROXY_ROTATION_MODE", "per_task"),
        "PROXY_POOL": env_manager.get_value("PROXY_POOL", ""),
        "PROXY_ROTATION_RETRY_LIMIT": _env_int("PROXY_ROTATION_RETRY_LIMIT", 2),
        "PROXY_BLACKLIST_TTL": _env_int("PROXY_BLACKLIST_TTL", 300),
    }


@router.put("/rotation")
async def update_rotation_settings(settings: RotationSettingsModel):
    updates = {}
    payload = model_dump(settings, exclude_unset=True)
    for key, value in payload.items():
        if isinstance(value, bool):
            updates[key] = _normalize_bool_value(value)
        else:
            updates[key] = str(value)
    success = env_manager.update_values(updates)
    if not success:
        raise HTTPException(status_code=500, detail="更新轮换设置失败")
    _reload_env()
    return {"message": "轮换设置已成功更新"}


@router.get("/status")
async def get_system_status(
    process_service: ProcessService = Depends(get_process_service),
):
    state_file = "xianyu_state.json"
    login_state_exists = os.path.exists(state_file)
    env_file_exists = os.path.exists(env_manager.env_file)
    openai_api_key = env_manager.get_value("OPENAI_API_KEY", "")
    openai_base_url = env_manager.get_value("OPENAI_BASE_URL", "")
    openai_model_name = env_manager.get_value("OPENAI_MODEL_NAME", "")
    ai_settings = AISettings()
    notification_settings = load_notification_settings()
    running_task_ids = [
        task_id
        for task_id, process in process_service.processes.items()
        if process and process.returncode is None
    ]

    return {
        "ai_configured": ai_settings.is_configured(),
        "notification_configured": notification_settings.has_any_notification_enabled(),
        "headless_mode": scraper_settings.run_headless,
        "running_in_docker": scraper_settings.running_in_docker,
        "scraper_running": len(running_task_ids) > 0,
        "running_task_ids": running_task_ids,
        "login_state_file": {
            "exists": login_state_exists,
            "path": state_file,
        },
        "env_file": {
            "exists": env_file_exists,
            "openai_api_key_set": bool(openai_api_key),
            "openai_base_url_set": bool(openai_base_url),
            "openai_model_name_set": bool(openai_model_name),
            **build_notification_status_flags(notification_settings),
        },
        "configured_notification_channels": build_configured_channels(notification_settings),
    }


@router.get("/ai")
async def get_ai_settings():
    base_url = env_manager.get_value("OPENAI_BASE_URL", "")
    model_name = env_manager.get_value("OPENAI_MODEL_NAME", "")
    cache = EndpointCapabilityCache(
        ttl_seconds=_env_int("AI_ENDPOINT_CACHE_TTL_SECONDS", 604800)
    )
    return {
        "OPENAI_BASE_URL": base_url,
        "OPENAI_MODEL_NAME": model_name,
        "AI_API_MODE": env_manager.get_value("AI_API_MODE", "auto"),
        "AI_STREAM_MODE": env_manager.get_value("AI_STREAM_MODE", "auto"),
        "AI_ENDPOINT_AUTO_DETECT": _env_bool("AI_ENDPOINT_AUTO_DETECT", True),
        "AI_ENDPOINT_CACHE": cache.summary(base_url, model_name),
        "AI_REASONING_EFFORT": normalize_reasoning_effort(
            env_manager.get_value(
                "AI_REASONING_EFFORT",
                DEFAULT_REASONING_EFFORT,
            )
        ),
        "AI_ANALYSIS_CONCURRENCY": max(
            1,
            min(32, _env_int("AI_ANALYSIS_CONCURRENCY", 2)),
        ),
        "SKIP_AI_ANALYSIS": env_manager.get_value("SKIP_AI_ANALYSIS", "false").lower() == "true",
        "PROXY_URL": env_manager.get_value("PROXY_URL", ""),
    }


@router.get("/ai/queue")
async def get_ai_request_queue():
    return await global_llm_request_queue.snapshot()


@router.put("/ai")
async def update_ai_settings(settings: AISettingsModel):
    updates = {}
    if settings.OPENAI_API_KEY is not None:
        updates["OPENAI_API_KEY"] = settings.OPENAI_API_KEY
    if settings.OPENAI_BASE_URL is not None:
        updates["OPENAI_BASE_URL"] = settings.OPENAI_BASE_URL
    if settings.OPENAI_MODEL_NAME is not None:
        updates["OPENAI_MODEL_NAME"] = settings.OPENAI_MODEL_NAME
    if settings.AI_API_MODE is not None:
        updates["AI_API_MODE"] = settings.AI_API_MODE
    if settings.AI_STREAM_MODE is not None:
        updates["AI_STREAM_MODE"] = settings.AI_STREAM_MODE
    if settings.AI_ENDPOINT_AUTO_DETECT is not None:
        updates["AI_ENDPOINT_AUTO_DETECT"] = _normalize_bool_value(
            settings.AI_ENDPOINT_AUTO_DETECT
        )
    if settings.AI_REASONING_EFFORT is not None:
        updates["AI_REASONING_EFFORT"] = settings.AI_REASONING_EFFORT
    if settings.AI_ANALYSIS_CONCURRENCY is not None:
        updates["AI_ANALYSIS_CONCURRENCY"] = str(settings.AI_ANALYSIS_CONCURRENCY)
    if settings.SKIP_AI_ANALYSIS is not None:
        updates["SKIP_AI_ANALYSIS"] = str(settings.SKIP_AI_ANALYSIS).lower()
    if settings.PROXY_URL is not None:
        updates["PROXY_URL"] = settings.PROXY_URL

    success = env_manager.update_values(updates)
    if not success:
        raise HTTPException(status_code=500, detail="更新AI设置失败")
    _reload_env()
    return {"message": "AI设置已成功更新"}


@router.post("/ai/test")
async def test_ai_settings(settings: dict):
    """测试AI模型设置是否有效"""
    ai_client = None
    try:
        stored_api_key = env_manager.get_value("OPENAI_API_KEY", "")
        submitted_api_key = settings.get("OPENAI_API_KEY", "")
        api_key = submitted_api_key or stored_api_key
        base_url = settings.get("OPENAI_BASE_URL") or env_manager.get_value(
            "OPENAI_BASE_URL",
            "",
        )
        model_name = settings.get("OPENAI_MODEL_NAME") or env_manager.get_value(
            "OPENAI_MODEL_NAME",
            "",
        )
        reasoning_effort = normalize_reasoning_effort(
            settings.get("AI_REASONING_EFFORT")
            or env_manager.get_value(
                "AI_REASONING_EFFORT",
                DEFAULT_REASONING_EFFORT,
            )
        )
        messages = [{"role": "user", "content": AI_TEST_PROMPT}]
        endpoint_auto_detect = settings.get("AI_ENDPOINT_AUTO_DETECT")
        if endpoint_auto_detect is None:
            endpoint_auto_detect = _env_bool("AI_ENDPOINT_AUTO_DETECT", True)
        test_settings = SimpleNamespace(
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
            api_mode=settings.get("AI_API_MODE")
            or env_manager.get_value("AI_API_MODE", "auto"),
            stream_mode=settings.get("AI_STREAM_MODE")
            or env_manager.get_value("AI_STREAM_MODE", "auto"),
            endpoint_auto_detect=bool(endpoint_auto_detect),
            endpoint_cache_ttl_seconds=_env_int(
                "AI_ENDPOINT_CACHE_TTL_SECONDS",
                604800,
            ),
            reasoning_effort=reasoning_effort,
            proxy_url=settings.get("PROXY_URL")
            or env_manager.get_value("PROXY_URL", ""),
            enable_response_format=False,
            enable_thinking=_env_bool("ENABLE_THINKING", False),
            timeout=30.0,
            is_configured=lambda: bool(base_url and model_name),
        )
        ai_client = AIClient(settings=test_settings)
        if not ai_client.is_available():
            raise RuntimeError("AI客户端未初始化，请检查 Base URL 和模型名称。")
        response_text = await ai_client._call_ai(
            messages,
            max_output_tokens=AI_TEST_MAX_OUTPUT_TOKENS,
            enable_json_output=False,
        )

        return {
            "success": True,
            "message": "AI模型连接测试成功！",
            "response": response_text,
            "transport": ai_client.last_resolution,
        }
    except Exception as exc:
        return {
            "success": False,
            "message": f"AI模型连接测试失败: {exc}",
        }
    finally:
        if ai_client is not None:
            await ai_client.close()
