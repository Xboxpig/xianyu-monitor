import asyncio
import os
from types import SimpleNamespace

import pytest

from src.infrastructure.external.ai_client import AIClient, _sanitize_no_proxy_env
from src.services.ai_endpoint_cache import EndpointCapabilityCache
from src.services.ai_request_compat import build_responses_input


def _build_fake_client(responses_create_impl, chat_create_impl=None):
    responses = SimpleNamespace(create=responses_create_impl)
    chat = SimpleNamespace(
        completions=SimpleNamespace(create=chat_create_impl or responses_create_impl)
    )
    return SimpleNamespace(responses=responses, chat=chat)


def test_build_messages_without_images_uses_text_only_content():
    client = AIClient.__new__(AIClient)

    messages = client._build_messages(
        {"商品信息": {"商品标题": "MacBook Pro M2"}, "卖家信息": {"卖家信用等级": "优秀"}},
        [],
        "只分析文字描述和卖家资质。",
    )

    assert messages[0]["role"] == "system"
    assert "只分析文字描述和卖家资质" in messages[0]["content"]
    assert "MacBook Pro M2" not in messages[0]["content"]
    assert messages[1]["role"] == "user"
    content = messages[1]["content"]
    assert isinstance(content, str)
    assert "MacBook Pro M2" in content
    assert "只分析文字描述和卖家资质" not in content


def test_build_messages_with_images_uses_multimodal_content(monkeypatch):
    client = AIClient.__new__(AIClient)
    monkeypatch.setattr(AIClient, "encode_image", staticmethod(lambda _path: "ZmFrZQ=="))

    messages = client._build_messages(
        {"商品信息": {"商品标题": "MacBook Pro M2"}},
        ["fake-image.jpg"],
        "结合图片和文字综合判断。",
    )

    assert messages[0]["role"] == "system"
    assert "结合图片和文字综合判断" in messages[0]["content"]
    assert "MacBook Pro M2" not in messages[0]["content"]
    assert messages[1]["role"] == "user"
    content = messages[1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert "MacBook Pro M2" in content[0]["text"]
    assert content[1]["type"] == "image_url"


def test_build_messages_keep_the_system_prefix_stable_between_products():
    client = AIClient.__new__(AIClient)

    first = client._build_messages(
        {"商品信息": {"商品标题": "商品 A"}},
        [],
        "固定分析规则",
    )
    second = client._build_messages(
        {"商品信息": {"商品标题": "商品 B"}},
        [],
        "固定分析规则",
    )

    assert first[0] == second[0]
    assert first[1] != second[1]


def test_build_responses_input_converts_multimodal_messages():
    result = build_responses_input(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,ZmFrZQ=="}},
                ],
            }
        ]
    )

    assert result == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "hello"},
                {
                    "type": "input_image",
                    "image_url": "data:image/jpeg;base64,ZmFrZQ==",
                    "detail": "auto",
                },
            ],
        }
    ]


def test_call_ai_retries_without_structured_output_when_model_rejects_it():
    client = AIClient.__new__(AIClient)
    client.settings = SimpleNamespace(
        model_name="fake-model",
        enable_response_format=True,
        enable_thinking=False,
    )
    request_history = []

    async def fake_create(**kwargs):
        request_history.append(kwargs)
        if len(request_history) == 1:
            raise Exception(
                "Error code: 400 - {'error': {'code': 'InvalidParameter', "
                "'message': 'The parameter `response_format.type` specified in "
                "the request are not valid: `json_object` is not supported by "
                "this model.', 'param': 'response_format.type'}}"
            )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"ok":true}')
                )
            ]
        )

    client.client = _build_fake_client(fake_create)

    response = asyncio.run(client._call_ai([{"role": "user", "content": "hi"}]))

    assert response == '{"ok":true}'
    assert request_history[0]["messages"][0]["content"] == "hi"
    assert request_history[0]["response_format"]["type"] == "json_object"
    assert "response_format" not in request_history[1]


def test_call_ai_falls_back_to_responses_when_chat_completions_api_is_missing():
    client = AIClient.__new__(AIClient)
    client.settings = SimpleNamespace(
        model_name="fake-model",
        enable_response_format=True,
        enable_thinking=False,
    )
    request_history = []

    async def fake_chat_create(**kwargs):
        request_history.append(("chat", kwargs))
        raise Exception("Error code: 404 - page not found")

    async def fake_responses_create(**kwargs):
        request_history.append(("responses", kwargs))
        if len([item for item in request_history if item[0] == "responses"]) == 1:
            raise Exception(
                "Error code: 400 - {'error': {'code': 'InvalidParameter', "
                "'message': 'The parameter `text.format.type` specified in "
                "the request are not valid: `json_object` is not supported by "
                "this model.', 'param': 'text.format.type'}}"
            )
        return SimpleNamespace(output_text='{"ok":true}')

    client.client = _build_fake_client(fake_responses_create, fake_chat_create)

    response = asyncio.run(client._call_ai([{"role": "user", "content": "hi"}]))

    assert response == '{"ok":true}'
    assert request_history[0][0] == "chat"
    assert request_history[1][0] == "responses"
    assert request_history[1][1]["text"]["format"]["type"] == "json_object"
    assert request_history[2][0] == "responses"
    assert "text" not in request_history[2][1]


def test_call_ai_retries_without_temperature_when_gateway_rejects_it():
    client = AIClient.__new__(AIClient)
    client.settings = SimpleNamespace(
        model_name="fake-model",
        enable_response_format=False,
        enable_thinking=False,
    )
    request_history = []

    async def fake_create(**kwargs):
        request_history.append(kwargs)
        if len(request_history) == 1:
            raise Exception("temperature is not supported by this gateway")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"ok":true}')
                )
            ]
        )

    client.client = _build_fake_client(fake_create)

    response = asyncio.run(client._call_ai([{"role": "user", "content": "hi"}]))

    assert response == '{"ok":true}'
    assert request_history[0]["temperature"] == 0.1
    assert "temperature" not in request_history[1]


def test_call_ai_retries_without_reasoning_effort_when_gateway_rejects_it():
    client = AIClient.__new__(AIClient)
    client.settings = SimpleNamespace(
        model_name="fake-model",
        reasoning_effort="xhigh",
        enable_response_format=False,
        enable_thinking=False,
    )
    request_history = []

    async def fake_create(**kwargs):
        request_history.append(kwargs)
        if len(request_history) == 1:
            raise Exception("reasoning_effort is not supported by this gateway")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))]
        )

    client.client = _build_fake_client(fake_create)

    response = asyncio.run(client._call_ai([{"role": "user", "content": "hi"}]))

    assert response == '{"ok":true}'
    assert request_history[0]["reasoning_effort"] == "xhigh"
    assert "reasoning_effort" not in request_history[1]


def test_call_ai_retries_when_response_content_is_empty():
    client = AIClient.__new__(AIClient)
    client.settings = SimpleNamespace(
        model_name="fake-model",
        enable_response_format=False,
        enable_thinking=False,
    )
    request_history = []

    async def fake_create(**kwargs):
        request_history.append(kwargs)
        if len(request_history) < 4:
            return SimpleNamespace(output_text="")
        return SimpleNamespace(output_text='{"ok":true}')

    client.client = _build_fake_client(fake_create)

    response = asyncio.run(client._call_ai([{"role": "user", "content": "hi"}]))

    assert response == '{"ok":true}'
    assert len(request_history) == 4


def test_call_ai_raises_after_all_empty_response_retries_are_exhausted():
    client = AIClient.__new__(AIClient)
    client.settings = SimpleNamespace(
        model_name="fake-model",
        enable_response_format=False,
        enable_thinking=False,
    )
    request_history = []

    async def fake_create(**kwargs):
        request_history.append(kwargs)
        return SimpleNamespace(output_text="")

    client.client = _build_fake_client(fake_create)

    with pytest.raises(ValueError, match="AI响应内容为空"):
        asyncio.run(client._call_ai([{"role": "user", "content": "hi"}]))

    assert len(request_history) == 4


def test_call_ai_auto_stream_falls_back_and_caches_non_stream_support(tmp_path):
    client = AIClient.__new__(AIClient)
    client.settings = SimpleNamespace(
        base_url="https://gateway.example.com/v1",
        model_name="demo-model",
        api_mode="chat_completions",
        stream_mode="auto",
        endpoint_auto_detect=True,
        reasoning_effort="medium",
        enable_response_format=False,
        enable_thinking=False,
    )
    request_history = []

    async def fake_create(**kwargs):
        request_history.append(kwargs)
        if kwargs.get("stream") is True:
            raise Exception("stream is not supported by this gateway")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
        )

    client.client = _build_fake_client(fake_create)
    client.endpoint_cache = EndpointCapabilityCache(
        path=tmp_path / "capabilities.json",
        ttl_seconds=60,
    )
    client._client_base_url = "https://gateway.example.com/v1"
    client.last_resolution = None

    response = asyncio.run(client._call_ai([{"role": "user", "content": "hi"}]))

    assert response == "OK"
    assert request_history[0]["stream"] is True
    assert "stream" not in request_history[1]
    cached = client.endpoint_cache.load(
        "https://gateway.example.com/v1",
        "demo-model",
    )
    assert cached["streaming"] is False


def test_call_ai_prefers_cached_candidate_and_api_mode(tmp_path):
    base_url = "https://gateway.example.com"
    model_name = "gpt-demo"
    cache = EndpointCapabilityCache(
        path=tmp_path / "capabilities.json",
        ttl_seconds=60,
    )
    cache.save(
        base_url,
        model_name,
        {
            "candidate_index": 1,
            "api_mode": "chat_completions",
            "streaming": False,
            "supports_json_output": False,
            "supports_temperature": True,
            "supports_reasoning_effort": True,
        },
    )
    client = AIClient.__new__(AIClient)
    client.settings = SimpleNamespace(
        base_url=base_url,
        model_name=model_name,
        api_mode="auto",
        stream_mode="auto",
        endpoint_auto_detect=True,
        reasoning_effort="medium",
        enable_response_format=True,
        enable_thinking=False,
    )
    request_history = []
    visited = []

    async def fake_chat_create(**kwargs):
        request_history.append(("chat", kwargs))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="cached"))]
        )

    async def fake_responses_create(**kwargs):
        request_history.append(("responses", kwargs))
        raise AssertionError("cached Chat Completions route should be used first")

    fake_client = _build_fake_client(fake_responses_create, fake_chat_create)
    client.client = fake_client
    client.endpoint_cache = cache
    client._client_base_url = ""
    client.last_resolution = None

    def client_for_candidate(candidate_url):
        visited.append(candidate_url)
        return fake_client, None

    client._client_for_candidate = client_for_candidate

    response = asyncio.run(client._call_ai([{"role": "user", "content": "hi"}]))

    assert response == "cached"
    assert visited == [base_url]
    assert request_history[0][0] == "chat"
    assert "stream" not in request_history[0][1]
    assert "response_format" not in request_history[0][1]


def test_call_ai_explicit_json_disable_overrides_cached_support(tmp_path):
    base_url = "https://gateway.example.com/v1"
    model_name = "gpt-demo"
    cache = EndpointCapabilityCache(
        path=tmp_path / "capabilities.json",
        ttl_seconds=60,
    )
    cache.save(
        base_url,
        model_name,
        {
            "candidate_index": 0,
            "api_mode": "responses",
            "streaming": False,
            "supports_json_output": True,
            "supports_temperature": True,
            "supports_reasoning_effort": True,
        },
    )
    client = AIClient.__new__(AIClient)
    client.settings = SimpleNamespace(
        base_url=base_url,
        model_name=model_name,
        api_mode="responses",
        stream_mode="off",
        endpoint_auto_detect=True,
        reasoning_effort="medium",
        enable_response_format=True,
        enable_thinking=False,
    )
    request_history = []

    async def fake_responses_create(**kwargs):
        request_history.append(kwargs)
        return SimpleNamespace(output_text="plain criteria text")

    fake_client = _build_fake_client(fake_responses_create)
    client.client = fake_client
    client.endpoint_cache = cache
    client._client_base_url = base_url
    client.last_resolution = None

    response = asyncio.run(
        client._call_ai(
            [{"role": "user", "content": "generate criteria"}],
            enable_json_output=False,
        )
    )

    assert response == "plain criteria text"
    assert "text" not in request_history[0]


def test_call_ai_tries_next_url_candidate_after_both_apis_return_404(tmp_path):
    base_url = "https://gateway.example.com"
    client = AIClient.__new__(AIClient)
    client.settings = SimpleNamespace(
        base_url=base_url,
        model_name="demo-model",
        api_mode="auto",
        stream_mode="off",
        endpoint_auto_detect=True,
        reasoning_effort="medium",
        enable_response_format=False,
        enable_thinking=False,
    )
    visited = []
    request_history = []

    async def unavailable(**kwargs):
        request_history.append(("unavailable", kwargs))
        raise Exception("Error code: 404 - page not found")

    async def available(**kwargs):
        request_history.append(("available", kwargs))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
        )

    first_client = _build_fake_client(unavailable)
    second_client = _build_fake_client(available)
    client.client = first_client
    client.endpoint_cache = EndpointCapabilityCache(
        path=tmp_path / "capabilities.json",
        ttl_seconds=60,
    )
    client._client_base_url = "https://gateway.example.com/v1"
    client.last_resolution = None

    def client_for_candidate(candidate_url):
        visited.append(candidate_url)
        selected = first_client if candidate_url.endswith("/v1") else second_client
        return selected, None

    client._client_for_candidate = client_for_candidate

    response = asyncio.run(client._call_ai([{"role": "user", "content": "hi"}]))

    assert response == "OK"
    assert visited == [
        "https://gateway.example.com/v1",
        "https://gateway.example.com",
    ]
    assert [kind for kind, _ in request_history] == [
        "unavailable",
        "unavailable",
        "available",
    ]


def test_close_closes_underlying_async_client_and_clears_reference():
    client = AIClient.__new__(AIClient)
    close_state = {"closed": False}

    async def fake_close():
        close_state["closed"] = True

    client.client = SimpleNamespace(close=fake_close)

    asyncio.run(client.close())

    assert close_state["closed"] is True
    assert client.client is None


def test_parse_response_uses_first_json_object_when_response_contains_multiple_objects():
    client = AIClient.__new__(AIClient)

    result = client._parse_response("""```json
{"ok": true, "reason": "first"}
{"ok": false, "reason": "second"}
```""")

    assert result == {"ok": True, "reason": "first"}


# -- _sanitize_no_proxy_env tests --


def test_sanitize_no_proxy_strips_ipv6_cidr(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.0/8,::1/128")
    _sanitize_no_proxy_env()
    assert os.environ["NO_PROXY"] == "localhost,127.0.0.0/8,::1"


def test_sanitize_no_proxy_strips_lowercase_variant(monkeypatch):
    monkeypatch.setenv("no_proxy", "localhost,::1/128,fe80::1/64")
    _sanitize_no_proxy_env()
    assert os.environ["no_proxy"] == "localhost,::1,fe80::1"


def test_sanitize_no_proxy_preserves_ipv4_cidr(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "10.0.0.0/8,192.168.0.0/16")
    _sanitize_no_proxy_env()
    assert os.environ["NO_PROXY"] == "10.0.0.0/8,192.168.0.0/16"


def test_sanitize_no_proxy_noop_without_env(monkeypatch):
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    _sanitize_no_proxy_env()


def test_sanitize_no_proxy_handles_both_keys(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "::1/128")
    monkeypatch.setenv("no_proxy", "fe80::1/10")
    _sanitize_no_proxy_env()
    assert os.environ["NO_PROXY"] == "::1"
    assert os.environ["no_proxy"] == "fe80::1"
