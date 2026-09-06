import asyncio

import pytest

from src.services.ai_request_compat import (
    AIStreamingError,
    CHAT_COMPLETIONS_API_MODE,
    RESPONSES_API_MODE,
    build_ai_request_params,
    collect_ai_stream_async,
    collect_ai_stream_sync,
    is_json_output_unsupported_error,
    is_reasoning_effort_unsupported_error,
    is_responses_api_unsupported_error,
    is_temperature_unsupported_error,
    remove_reasoning_effort_param,
    remove_temperature_param,
)


class _AsyncEvents:
    def __init__(self, events):
        self._events = list(events)

    def __aiter__(self):
        self._iterator = iter(self._events)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration


def test_collect_responses_sse_typed_events_and_reports_deltas():
    deltas = []
    stream = _AsyncEvents(
        [
            {"type": "response.created"},
            {"type": "response.output_text.delta", "delta": "hel"},
            {"type": "response.output_text.delta", "delta": "lo"},
            {"type": "response.completed", "response": {"output_text": "hello"}},
        ]
    )

    result = asyncio.run(
        collect_ai_stream_async(stream, RESPONSES_API_MODE, deltas.append)
    )

    assert result == "hello"
    assert deltas == ["hel", "lo"]


def test_collect_responses_sse_raises_terminal_error_event():
    stream = _AsyncEvents(
        [{"type": "error", "error": {"message": "gateway stream failed"}}]
    )

    with pytest.raises(AIStreamingError, match="gateway stream failed"):
        asyncio.run(collect_ai_stream_async(stream, RESPONSES_API_MODE))


def test_collect_chat_completions_sse_delta_chunks():
    stream = [
        {"choices": [{"delta": {"content": "你"}}]},
        {"choices": [{"delta": {"content": "好"}}]},
        {"choices": [{"delta": {}}]},
    ]

    assert collect_ai_stream_sync(stream, CHAT_COMPLETIONS_API_MODE) == "你好"


def test_is_temperature_unsupported_error_detects_unsupported_message():
    err = Exception("temperature is not supported by this gateway")
    assert is_temperature_unsupported_error(err) is True


def test_remove_temperature_param_removes_only_temperature():
    params = {"model": "x", "temperature": 0.5, "max_output_tokens": 128}
    result = remove_temperature_param(params)

    assert "temperature" not in result
    assert result["model"] == "x"
    assert result["max_output_tokens"] == 128


def test_build_ai_request_params_maps_reasoning_effort_by_api_mode():
    messages = [{"role": "user", "content": "hello"}]

    chat_params = build_ai_request_params(
        CHAT_COMPLETIONS_API_MODE,
        model="demo",
        messages=messages,
        reasoning_effort="xhigh",
    )
    responses_params = build_ai_request_params(
        RESPONSES_API_MODE,
        model="demo",
        messages=messages,
        reasoning_effort="high",
    )

    assert chat_params["reasoning_effort"] == "xhigh"
    assert "reasoning" not in chat_params
    assert responses_params["reasoning"] == {"effort": "high"}
    assert "reasoning_effort" not in responses_params


def test_reasoning_effort_unsupported_error_and_removal_support_both_apis():
    error = Exception("reasoning_effort is not supported by this gateway")
    assert is_reasoning_effort_unsupported_error(error) is True

    chat_result = remove_reasoning_effort_param(
        {"model": "demo", "reasoning_effort": "medium"}
    )
    responses_result = remove_reasoning_effort_param(
        {"model": "demo", "reasoning": {"effort": "medium"}}
    )

    assert chat_result == {"model": "demo"}
    assert responses_result == {"model": "demo"}


def test_is_responses_api_unsupported_error_detects_gemini_plain_404():
    class _Resp:
        text = ""

    class _Err(Exception):
        status_code = 404
        body = ""
        response = _Resp()

        def __str__(self):
            return "Error code: 404"

    assert is_responses_api_unsupported_error(_Err()) is True


# -- is_json_output_unsupported_error tests --


def test_json_output_error_detected_via_body_param_response_format():
    """Vercel AI Gateway returns 400 with param='response_format'."""

    class _Err(Exception):
        body = {
            "message": "Invalid input",
            "type": "invalid_request_error",
            "param": "response_format",
            "code": "invalid_request_error",
        }

    assert is_json_output_unsupported_error(_Err()) is True


def test_json_output_error_detected_via_body_param_response_format_type():
    class _Err(Exception):
        body = {
            "message": "Invalid input",
            "param": "response_format.type",
        }

    assert is_json_output_unsupported_error(_Err()) is True


def test_json_output_error_detected_via_legacy_string_matching():
    err = Exception(
        "response_format.type is not supported by this model"
    )
    assert is_json_output_unsupported_error(err) is True


def test_json_output_error_not_triggered_by_unrelated_400():
    class _Err(Exception):
        body = {
            "message": "Invalid input",
            "param": "messages",
        }

    assert is_json_output_unsupported_error(_Err()) is False


def test_json_output_error_not_triggered_without_body():
    err = Exception("some random 400 error")
    assert is_json_output_unsupported_error(err) is False
