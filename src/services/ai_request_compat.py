"""AI 请求兼容性辅助逻辑。"""

import copy
import inspect
from typing import Any, Awaitable, Callable, Dict, Iterable, List

from src.ai_reasoning import normalize_reasoning_effort
from src.services.ai_endpoint_cache import (
    CHAT_COMPLETIONS_API_MODE,
    RESPONSES_API_MODE,
)


INPUT_TEXT_TYPE = "input_text"
INPUT_IMAGE_TYPE = "input_image"
IMAGE_DETAIL_AUTO = "auto"
JSON_OUTPUT_TYPE = "json_object"
UNSUPPORTED_JSON_OUTPUT_MARKERS = (
    "not supported by this model",
    "json_object",
    "json_schema",
    "text.format",
    "response_format.type",
)
RESPONSES_API_UNSUPPORTED_MARKERS = (
    "404 page not found",
    "page not found",
    "/responses",
    "/v1/responses",
)
CHAT_COMPLETIONS_API_UNSUPPORTED_MARKERS = (
    "404 page not found",
    "page not found",
    "/chat/completions",
    "/v1/chat/completions",
)
UNSUPPORTED_TEMPERATURE_MARKERS = (
    "temperature",
    "sampling temperature",
)
UNSUPPORTED_REASONING_EFFORT_MARKERS = (
    "reasoning_effort",
    "reasoning.effort",
    "reasoning effort",
)
UNSUPPORTED_STREAMING_MARKERS = (
    "stream is not supported",
    "streaming is not supported",
    "stream must be false",
    "unsupported stream",
    "text/event-stream",
    "sse is not supported",
)

TextDeltaCallback = Callable[[str], Awaitable[None] | None]


class AIStreamingError(RuntimeError):
    """Raised when an SSE stream reports a terminal error event."""


def build_responses_input(messages: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """将 Chat Completions 风格的消息转换为 Responses API 输入。"""
    input_items: List[Dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        input_items.append(
            {
                "role": role,
                "content": _build_input_content(message.get("content")),
            }
        )
    return input_items


def add_json_text_format(
    request_params: Dict[str, Any],
    enabled: bool,
) -> Dict[str, Any]:
    """按需附加 Responses API 的结构化 JSON 输出参数。"""
    next_params = dict(request_params)
    if not enabled:
        return next_params

    text_config = dict(next_params.get("text") or {})
    text_config["format"] = {"type": JSON_OUTPUT_TYPE}
    next_params["text"] = text_config
    return next_params


def add_json_response_format(
    request_params: Dict[str, Any],
    enabled: bool,
) -> Dict[str, Any]:
    """按需附加 Chat Completions 的 JSON 输出参数。"""
    next_params = dict(request_params)
    if enabled:
        next_params["response_format"] = {"type": JSON_OUTPUT_TYPE}
    return next_params


def is_json_output_unsupported_error(error: Exception) -> bool:
    """识别模型或网关不支持结构化 JSON 输出参数的错误。"""
    body = getattr(error, "body", None)
    if isinstance(body, dict) and body.get("param") in (
        "response_format",
        "response_format.type",
    ):
        return True

    message = str(error)
    return (
        "not supported" in message.lower()
        and any(marker in message for marker in UNSUPPORTED_JSON_OUTPUT_MARKERS)
    )


def is_responses_api_unsupported_error(error: Exception) -> bool:
    """识别 OpenAI 兼容服务未实现 Responses API 的错误。"""
    return _is_api_unsupported_error(error, RESPONSES_API_UNSUPPORTED_MARKERS)


def is_chat_completions_api_unsupported_error(error: Exception) -> bool:
    """识别 OpenAI 兼容服务未实现 Chat Completions API 的错误。"""
    return _is_api_unsupported_error(error, CHAT_COMPLETIONS_API_UNSUPPORTED_MARKERS)


def build_ai_request_params(
    api_mode: str,
    *,
    model: str,
    messages: Iterable[Dict[str, Any]],
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    reasoning_effort: str | None = None,
    enable_json_output: bool = False,
) -> Dict[str, Any]:
    """根据 API 模式构建请求参数。"""
    request_params = {"model": model}
    if api_mode == RESPONSES_API_MODE:
        request_params["input"] = build_responses_input(messages)
        if max_output_tokens is not None:
            request_params["max_output_tokens"] = max_output_tokens
        if temperature is not None:
            request_params["temperature"] = temperature
        if reasoning_effort is not None:
            request_params["reasoning"] = {
                "effort": normalize_reasoning_effort(reasoning_effort)
            }
        return add_json_text_format(request_params, enable_json_output)

    if api_mode == CHAT_COMPLETIONS_API_MODE:
        request_params["messages"] = copy.deepcopy(list(messages))
        if max_output_tokens is not None:
            request_params["max_tokens"] = max_output_tokens
        if temperature is not None:
            request_params["temperature"] = temperature
        if reasoning_effort is not None:
            request_params["reasoning_effort"] = normalize_reasoning_effort(
                reasoning_effort
            )
        return add_json_response_format(request_params, enable_json_output)

    raise ValueError(f"不支持的 AI API 模式: {api_mode}")


async def create_ai_response_async(
    client: Any,
    api_mode: str,
    request_params: Dict[str, Any],
    *,
    stream: bool = False,
    on_text_delta: TextDeltaCallback | None = None,
) -> Any:
    """根据 API 模式发起异步请求。"""
    params = dict(request_params)
    if stream:
        params["stream"] = True
    if api_mode == RESPONSES_API_MODE:
        response = await client.responses.create(**params)
    elif api_mode == CHAT_COMPLETIONS_API_MODE:
        response = await client.chat.completions.create(**params)
    else:
        raise ValueError(f"不支持的 AI API 模式: {api_mode}")
    if not stream:
        return response
    return await collect_ai_stream_async(response, api_mode, on_text_delta)


def create_ai_response_sync(
    client: Any,
    api_mode: str,
    request_params: Dict[str, Any],
    *,
    stream: bool = False,
) -> Any:
    """根据 API 模式发起同步请求。"""
    params = dict(request_params)
    if stream:
        params["stream"] = True
    if api_mode == RESPONSES_API_MODE:
        response = client.responses.create(**params)
    elif api_mode == CHAT_COMPLETIONS_API_MODE:
        response = client.chat.completions.create(**params)
    else:
        raise ValueError(f"不支持的 AI API 模式: {api_mode}")
    if not stream:
        return response
    return collect_ai_stream_sync(response, api_mode)


async def collect_ai_stream_async(
    stream: Any,
    api_mode: str,
    on_text_delta: TextDeltaCallback | None = None,
) -> str:
    """Collect OpenAI SDK async SSE events into one response string."""
    parts: list[str] = []
    completed_response: Any = None
    async for event in stream:
        delta, completed_response = _consume_stream_event(
            event,
            api_mode,
            completed_response,
        )
        if not delta:
            continue
        parts.append(delta)
        if on_text_delta is not None:
            callback_result = on_text_delta(delta)
            if inspect.isawaitable(callback_result):
                await callback_result
    return _finish_stream(parts, completed_response)


def collect_ai_stream_sync(stream: Any, api_mode: str) -> str:
    """Collect OpenAI SDK sync SSE events into one response string."""
    parts: list[str] = []
    completed_response: Any = None
    for event in stream:
        delta, completed_response = _consume_stream_event(
            event,
            api_mode,
            completed_response,
        )
        if delta:
            parts.append(delta)
    return _finish_stream(parts, completed_response)


def _consume_stream_event(
    event: Any,
    api_mode: str,
    completed_response: Any,
) -> tuple[str, Any]:
    if api_mode == RESPONSES_API_MODE:
        event_type = _field(event, "type")
        if event_type == "response.output_text.delta":
            return str(_field(event, "delta") or ""), completed_response
        if event_type == "response.completed":
            return "", _field(event, "response") or completed_response
        if event_type in {"error", "response.failed", "response.incomplete"}:
            response_error = _field(_field(event, "response"), "error")
            message = (
                _field(event, "message")
                or _field(_field(event, "error"), "message")
                or _field(response_error, "message")
                or f"Responses SSE event: {event_type}"
            )
            raise AIStreamingError(str(message))
        return "", completed_response

    choices = _field(event, "choices") or []
    if not choices:
        return "", completed_response
    delta = _field(choices[0], "delta")
    content = _field(delta, "content")
    return _coerce_delta_text(content), completed_response


def _finish_stream(parts: list[str], completed_response: Any) -> str:
    text = "".join(parts)
    if text:
        return text
    if completed_response is not None:
        from src.services.ai_response_parser import extract_ai_response_content

        return extract_ai_response_content(completed_response)
    return ""


def _coerce_delta_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
            continue
        text = _field(item, "text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def is_temperature_unsupported_error(error: Exception) -> bool:
    """识别模型或中转站不支持 temperature 参数的错误。"""
    message = str(error).lower()
    return (
        "not supported" in message
        or "unsupported" in message
        or "invalid" in message
        or "参数错误" in message
    ) and any(marker in message for marker in UNSUPPORTED_TEMPERATURE_MARKERS)


def remove_temperature_param(request_params: Dict[str, Any]) -> Dict[str, Any]:
    """移除 temperature 参数，适配不支持采样温度的模型网关。"""
    next_params = dict(request_params)
    next_params.pop("temperature", None)
    return next_params


def is_reasoning_effort_unsupported_error(error: Exception) -> bool:
    """识别模型或中转站不支持 reasoning effort 参数的错误。"""
    body = getattr(error, "body", None)
    if isinstance(body, dict) and body.get("param") in (
        "reasoning_effort",
        "reasoning",
        "reasoning.effort",
    ):
        return True

    message = str(error).lower()
    return (
        "not supported" in message
        or "unsupported" in message
        or "invalid" in message
        or "unknown parameter" in message
        or "参数错误" in message
    ) and any(marker in message for marker in UNSUPPORTED_REASONING_EFFORT_MARKERS)


def remove_reasoning_effort_param(
    request_params: Dict[str, Any],
) -> Dict[str, Any]:
    """移除 Chat/Responses 两种形态的 reasoning effort 参数。"""
    next_params = dict(request_params)
    next_params.pop("reasoning_effort", None)
    reasoning = next_params.get("reasoning")
    if isinstance(reasoning, dict):
        next_reasoning = dict(reasoning)
        next_reasoning.pop("effort", None)
        if next_reasoning:
            next_params["reasoning"] = next_reasoning
        else:
            next_params.pop("reasoning", None)
    return next_params


def is_streaming_unsupported_error(error: Exception) -> bool:
    """Recognize gateways that accept the endpoint but reject SSE streaming."""
    message = str(error).lower()
    return any(marker in message for marker in UNSUPPORTED_STREAMING_MARKERS)


def _is_api_unsupported_error(
    error: Exception,
    markers: tuple[str, ...],
) -> bool:
    message = str(error).lower()
    if any(marker in message for marker in markers):
        return True

    status_code = getattr(error, "status_code", None)
    body = getattr(error, "body", None)
    response = getattr(error, "response", None)
    response_text = getattr(response, "text", None) if response else None
    return (
        status_code == 404
        and message.strip() == "error code: 404"
        and not body
        and not response_text
    )


def _build_input_content(content: Any) -> List[Dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": INPUT_TEXT_TYPE, "text": content}]
    if not isinstance(content, list):
        raise ValueError(f"AI消息内容类型不受支持: {type(content).__name__}")

    return [_coerce_content_item(item) for item in content]


def _coerce_content_item(item: Any) -> Dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(f"AI消息片段类型不受支持: {type(item).__name__}")

    item_type = item.get("type")
    if item_type in {"text", INPUT_TEXT_TYPE}:
        text = item.get("text")
        if not isinstance(text, str):
            raise ValueError("文本消息片段缺少 text 字段。")
        return {"type": INPUT_TEXT_TYPE, "text": text}

    if item_type in {"image_url", INPUT_IMAGE_TYPE}:
        return _build_image_input_item(item)

    raise ValueError(f"不支持的 AI 消息片段类型: {item_type}")


def _build_image_input_item(item: Dict[str, Any]) -> Dict[str, Any]:
    raw_image = item.get("image_url")
    if isinstance(raw_image, dict):
        image_url = raw_image.get("url")
    else:
        image_url = raw_image

    if not isinstance(image_url, str) or not image_url.strip():
        raise ValueError("图片消息片段缺少有效的 image_url。")

    return {
        "type": INPUT_IMAGE_TYPE,
        "image_url": image_url,
        "detail": item.get("detail", IMAGE_DETAIL_AUTO),
    }
