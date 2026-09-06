import json
import time

from src.services.ai_endpoint_cache import (
    CHAT_COMPLETIONS_API_MODE,
    RESPONSES_API_MODE,
    EndpointCapabilityCache,
    build_endpoint_candidates,
    preferred_api_modes,
)


def test_host_only_url_prefers_v1_suffix():
    candidates = build_endpoint_candidates("https://gateway.example.com")

    assert [item.base_url for item in candidates] == [
        "https://gateway.example.com/v1",
        "https://gateway.example.com",
    ]


def test_complete_endpoint_is_stripped_and_preserves_mode_hint():
    candidates = build_endpoint_candidates(
        "https://gateway.example.com/openai/v1/chat/completions"
    )

    assert candidates[0].base_url == "https://gateway.example.com/openai/v1"
    assert candidates[0].mode_hint == CHAT_COMPLETIONS_API_MODE


def test_responses_endpoint_is_stripped_and_preserves_query_parameters():
    candidates = build_endpoint_candidates(
        "https://gateway.example.com/v1/responses?api-version=preview"
    )

    assert candidates[0].base_url == (
        "https://gateway.example.com/v1?api-version=preview"
    )
    assert candidates[0].mode_hint == RESPONSES_API_MODE


def test_v1_suffix_is_inserted_before_query_parameters():
    candidates = build_endpoint_candidates(
        "https://gateway.example.com?api-version=preview"
    )

    assert candidates[0].base_url == (
        "https://gateway.example.com/v1?api-version=preview"
    )


def test_auto_mode_prefers_responses_for_gpt_models_and_chat_for_others():
    assert preferred_api_modes("auto", model="gpt-5.6-terra") == [
        RESPONSES_API_MODE,
        CHAT_COMPLETIONS_API_MODE,
    ]
    assert preferred_api_modes("auto", model="qwen3") == [
        CHAT_COMPLETIONS_API_MODE,
        RESPONSES_API_MODE,
    ]


def test_capability_cache_round_trip_and_configuration_fingerprint(tmp_path):
    path = tmp_path / "capabilities.json"
    cache = EndpointCapabilityCache(path=path, ttl_seconds=60)
    capabilities = {
        "candidate_index": 1,
        "api_mode": RESPONSES_API_MODE,
        "streaming": True,
    }

    cache.save("https://gateway.example.com", "gpt-test", capabilities)

    assert cache.load("https://gateway.example.com", "gpt-test") == capabilities
    assert cache.load("https://other.example.com", "gpt-test") is None
    assert cache.load("https://gateway.example.com", "other-model") is None


def test_capability_cache_ttl_expiration(tmp_path):
    path = tmp_path / "capabilities.json"
    cache = EndpointCapabilityCache(path=path, ttl_seconds=1)
    cache.save("https://gateway.example.com", "gpt-test", {"streaming": True})
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["updated_at"] = time.time() - 2
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert cache.load("https://gateway.example.com", "gpt-test") is None


def test_zero_ttl_never_expires(tmp_path):
    path = tmp_path / "capabilities.json"
    cache = EndpointCapabilityCache(path=path, ttl_seconds=0)
    cache.save("https://gateway.example.com", "gpt-test", {"streaming": False})
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["updated_at"] = 0
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert cache.load("https://gateway.example.com", "gpt-test") == {
        "streaming": False
    }
