"""OpenAI-compatible endpoint discovery and capability cache."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit


AUTO_API_MODE = "auto"
RESPONSES_API_MODE = "responses"
CHAT_COMPLETIONS_API_MODE = "chat_completions"
VALID_API_MODES = {
    AUTO_API_MODE,
    RESPONSES_API_MODE,
    CHAT_COMPLETIONS_API_MODE,
}

AUTO_STREAM_MODE = "auto"
SSE_STREAM_MODE = "sse"
NON_STREAM_MODE = "off"
VALID_STREAM_MODES = {
    AUTO_STREAM_MODE,
    SSE_STREAM_MODE,
    NON_STREAM_MODE,
}

DEFAULT_CACHE_FILE = "data/ai_endpoint_capabilities.json"
DEFAULT_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class EndpointCandidate:
    base_url: str
    mode_hint: str | None = None


def normalize_api_mode(value: Any) -> str:
    normalized = str(value or AUTO_API_MODE).strip().lower()
    return normalized if normalized in VALID_API_MODES else AUTO_API_MODE


def normalize_stream_mode(value: Any) -> str:
    normalized = str(value or AUTO_STREAM_MODE).strip().lower()
    return normalized if normalized in VALID_STREAM_MODES else AUTO_STREAM_MODE


def build_endpoint_candidates(base_url: str) -> list[EndpointCandidate]:
    """Build safe base URL candidates and strip accidentally pasted endpoints.

    The OpenAI SDK expects a base URL and appends ``/responses`` or
    ``/chat/completions`` itself. Users frequently paste either the host only
    or a complete endpoint, so both forms are normalized here.
    """
    raw = str(base_url or "").strip().rstrip("/")
    if not raw:
        return []

    parts = urlsplit(raw)
    path = parts.path.rstrip("/")
    mode_hint: str | None = None
    lowered = path.lower()
    suffixes = (
        ("/chat/completions", CHAT_COMPLETIONS_API_MODE),
        ("/responses", RESPONSES_API_MODE),
    )
    for suffix, hinted_mode in suffixes:
        if lowered.endswith(suffix):
            path = path[: -len(suffix)].rstrip("/")
            mode_hint = hinted_mode
            break

    def with_path(next_path: str) -> str:
        return urlunsplit(
            (parts.scheme, parts.netloc, next_path, parts.query, "")
        ).rstrip("/")

    normalized = with_path(path)
    candidates: list[EndpointCandidate] = []

    def add(candidate_url: str) -> None:
        candidate_url = candidate_url.rstrip("/")
        if candidate_url and all(item.base_url != candidate_url for item in candidates):
            candidates.append(EndpointCandidate(candidate_url, mode_hint))

    normalized_path = path.rstrip("/")
    if normalized_path.lower().endswith("/v1"):
        add(normalized)
        root_path = normalized_path[:-3].rstrip("/")
        add(with_path(root_path))
    elif normalized_path in {"", "/"}:
        add(with_path("/v1"))
        add(normalized)
    else:
        add(normalized)
        add(with_path(normalized_path + "/v1"))

    return candidates


def preferred_api_modes(
    configured_mode: str,
    *,
    model: str,
    mode_hint: str | None = None,
) -> list[str]:
    configured = normalize_api_mode(configured_mode)
    if configured != AUTO_API_MODE:
        return [configured]
    if mode_hint in {RESPONSES_API_MODE, CHAT_COMPLETIONS_API_MODE}:
        first = mode_hint
    else:
        model_name = str(model or "").strip().lower()
        first = (
            RESPONSES_API_MODE
            if model_name.startswith(("gpt-", "o1", "o3", "o4"))
            else CHAT_COMPLETIONS_API_MODE
        )
    second = (
        CHAT_COMPLETIONS_API_MODE
        if first == RESPONSES_API_MODE
        else RESPONSES_API_MODE
    )
    return [first, second]


def order_candidates_from_cache(
    candidates: Iterable[EndpointCandidate],
    cached_candidate_index: Any,
) -> list[tuple[int, EndpointCandidate]]:
    indexed = list(enumerate(candidates))
    try:
        index = int(cached_candidate_index)
    except (TypeError, ValueError):
        return indexed
    if not 0 <= index < len(indexed):
        return indexed
    return [indexed[index], *[item for item in indexed if item[0] != index]]


class EndpointCapabilityCache:
    """Persist only transport capabilities for the currently configured API."""

    _lock = threading.RLock()

    def __init__(self, path: str | os.PathLike[str] | None = None, ttl_seconds: int | None = None):
        configured_path = path or os.getenv("AI_ENDPOINT_CACHE_FILE", DEFAULT_CACHE_FILE)
        self.path = Path(configured_path)
        configured_ttl = ttl_seconds
        if configured_ttl is None:
            try:
                configured_ttl = int(
                    os.getenv("AI_ENDPOINT_CACHE_TTL_SECONDS", str(DEFAULT_CACHE_TTL_SECONDS))
                )
            except ValueError:
                configured_ttl = DEFAULT_CACHE_TTL_SECONDS
        self.ttl_seconds = max(0, int(configured_ttl))

    @staticmethod
    def fingerprint(base_url: str, model: str) -> str:
        payload = f"{str(base_url).strip()}\0{str(model).strip()}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def load(self, base_url: str, model: str) -> dict[str, Any] | None:
        with self._lock:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, ValueError, TypeError):
                return None
            if payload.get("version") != 1:
                return None
            if payload.get("fingerprint") != self.fingerprint(base_url, model):
                return None
            updated_at = payload.get("updated_at")
            if self.ttl_seconds and isinstance(updated_at, (int, float)):
                if time.time() - float(updated_at) > self.ttl_seconds:
                    return None
            capabilities = payload.get("capabilities")
            return dict(capabilities) if isinstance(capabilities, dict) else None

    def save(self, base_url: str, model: str, capabilities: dict[str, Any]) -> None:
        payload = {
            "version": 1,
            "fingerprint": self.fingerprint(base_url, model),
            "updated_at": time.time(),
            "capabilities": dict(capabilities),
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(
                f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            try:
                temporary.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                os.replace(temporary, self.path)
            finally:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

    def summary(self, base_url: str, model: str) -> dict[str, Any]:
        cached = self.load(base_url, model)
        if not cached:
            return {"detected": False}
        return {
            "detected": True,
            "api_mode": cached.get("api_mode"),
            "streaming": cached.get("streaming"),
            "candidate_index": cached.get("candidate_index"),
            "supports_json_output": cached.get("supports_json_output"),
            "supports_temperature": cached.get("supports_temperature"),
            "supports_reasoning_effort": cached.get("supports_reasoning_effort"),
        }
