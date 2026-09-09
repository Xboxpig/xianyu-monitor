"""
AI 客户端封装
提供统一的 AI 调用接口
"""
import asyncio
import ipaddress
import inspect
import os
import json
import base64
import time
from typing import Awaitable, Callable, Dict, List, Optional
from datetime import datetime
from dotenv import load_dotenv
from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from src.ai_reasoning import normalize_reasoning_effort
from src.ai_message_builder import (
    build_analysis_messages,
)
from src.infrastructure.config.settings import AISettings
from src.infrastructure.config.env_manager import env_manager
from src.services.ai_request_compat import (
    CHAT_COMPLETIONS_API_MODE,
    RESPONSES_API_MODE,
    build_ai_request_params,
    create_ai_response_async,
    is_chat_completions_api_unsupported_error,
    is_json_output_unsupported_error,
    is_reasoning_effort_unsupported_error,
    is_responses_api_unsupported_error,
    is_temperature_unsupported_error,
    is_streaming_unsupported_error,
    remove_reasoning_effort_param,
    remove_temperature_param,
)
from src.services.ai_endpoint_cache import (
    AUTO_API_MODE,
    AUTO_STREAM_MODE,
    NON_STREAM_MODE,
    SSE_STREAM_MODE,
    EndpointCandidate,
    EndpointCapabilityCache,
    build_endpoint_candidates,
    normalize_api_mode,
    normalize_stream_mode,
    order_candidates_from_cache,
    preferred_api_modes,
)
from src.services.ai_response_parser import (
    EmptyAIResponseError,
    extract_ai_response_content,
    parse_ai_response_json,
)
from src.services.llm_request_queue import (
    LLM_PRIORITY_NORMAL,
    global_llm_request_queue,
)


def _sanitize_no_proxy_env() -> None:
    """Strip CIDR prefix lengths from IPv6 entries in NO_PROXY / no_proxy.

    httpx <= 0.28.1 wraps NO_PROXY IPv6 entries in brackets *including* the
    CIDR mask (e.g. ``[::1/128]``), which the URL parser rejects as an invalid
    port.  Stripping the ``/prefix`` part is safe because httpx doesn't
    support CIDR range matching anyway — it only does exact-host comparison.

    See https://github.com/encode/httpx/pull/3741
    """
    for key in ("NO_PROXY", "no_proxy"):
        value = os.environ.get(key)
        if not value:
            continue
        parts = [h.strip() for h in value.split(",")]
        cleaned: list[str] = []
        changed = False
        for part in parts:
            if "/" in part:
                host, _, prefix = part.partition("/")
                try:
                    ipaddress.IPv6Address(host)
                    cleaned.append(host)
                    changed = True
                    continue
                except ValueError:
                    pass
            cleaned.append(part)
        if changed:
            os.environ[key] = ",".join(cleaned)


class AIClient:
    """AI 客户端封装"""

    def __init__(
        self,
        settings: Optional[AISettings] = None,
        endpoint_cache: Optional[EndpointCapabilityCache] = None,
    ):
        self.settings: Optional[AISettings] = settings
        self.client: Optional[AsyncOpenAI] = None
        self.endpoint_cache = endpoint_cache
        self._client_base_url = ""
        self.last_resolution: Optional[dict] = None
        if settings is None:
            self.refresh()
        else:
            self._ensure_endpoint_cache()
            self.client = self._initialize_client(self._initial_base_url())

    def _load_settings(self) -> None:
        load_dotenv(dotenv_path=env_manager.env_file, override=True)
        self.settings = AISettings()

    def refresh(self) -> None:
        self._load_settings()
        self._ensure_endpoint_cache()
        self.client = self._initialize_client(self._initial_base_url())

    def _ensure_endpoint_cache(self) -> EndpointCapabilityCache:
        cache = getattr(self, "endpoint_cache", None)
        if cache is None:
            cache = EndpointCapabilityCache(
                ttl_seconds=getattr(
                    self.settings,
                    "endpoint_cache_ttl_seconds",
                    None,
                )
            )
            self.endpoint_cache = cache
        return cache

    def _initial_base_url(self) -> str:
        base_url = str(getattr(self.settings, "base_url", "") or "").strip()
        model = str(getattr(self.settings, "model_name", "") or "").strip()
        candidates = build_endpoint_candidates(base_url)
        if not candidates:
            return base_url
        cached = self._ensure_endpoint_cache().load(base_url, model)
        if cached:
            ordered = order_candidates_from_cache(
                candidates,
                cached.get("candidate_index"),
            )
            return ordered[0][1].base_url
        return candidates[0].base_url

    def _initialize_client(
        self,
        base_url: Optional[str] = None,
        *,
        track_base_url: bool = True,
    ) -> Optional[AsyncOpenAI]:
        """初始化 OpenAI 客户端"""
        if not self.settings or not self.settings.is_configured():
            print("警告：AI 配置不完整，AI 功能将不可用")
            return None

        try:
            if self.settings.proxy_url:
                print(f"正在为 AI 请求使用代理: {self.settings.proxy_url}")
                os.environ['HTTP_PROXY'] = self.settings.proxy_url
                os.environ['HTTPS_PROXY'] = self.settings.proxy_url

            _sanitize_no_proxy_env()

            resolved_base_url = str(base_url or self.settings.base_url).rstrip("/")
            client_params = {
                "api_key": self.settings.api_key,
                "base_url": resolved_base_url,
            }
            timeout = getattr(self.settings, "timeout", None)
            if timeout is not None:
                client_params["timeout"] = timeout
            proxy_url = str(getattr(self.settings, "proxy_url", "") or "").strip()
            if proxy_url:
                client_params["http_client"] = DefaultAsyncHttpxClient(
                    proxy=proxy_url
                )
            client = AsyncOpenAI(**client_params)
            if track_base_url:
                self._client_base_url = resolved_base_url
            return client
        except Exception as e:
            print(f"初始化 AI 客户端失败: {e}")
            return None

    def is_available(self) -> bool:
        """检查 AI 客户端是否可用"""
        return self.client is not None

    async def close(self) -> None:
        """关闭底层异步客户端，避免事件循环结束后再触发清理。"""
        client = self.client
        self.client = None
        if client is None:
            return

        close = getattr(client, "close", None)
        if close is None:
            return
        await close()

    @staticmethod
    def encode_image(image_path: str) -> Optional[str]:
        """将图片编码为 Base64"""
        if not image_path or not os.path.exists(image_path):
            return None
        try:
            with open(image_path, "rb") as f:
                return base64.b64encode(f.read()).decode('utf-8')
        except Exception as e:
            print(f"编码图片失败: {e}")
            return None

    async def analyze(
        self,
        product_data: Dict,
        image_paths: List[str],
        prompt_text: str
    ) -> Optional[Dict]:
        """
        分析商品数据

        Args:
            product_data: 商品数据
            image_paths: 图片路径列表
            prompt_text: 分析提示词

        Returns:
            分析结果
        """
        if not self.is_available():
            print("AI 客户端不可用")
            return None

        try:
            messages = self._build_messages(product_data, image_paths, prompt_text)
            response = await self._call_ai(messages)
            return self._parse_response(response)
        except Exception as e:
            print(f"AI 分析失败: {e}")
            return None

    def _build_messages(self, product_data: Dict, image_paths: List[str], prompt_text: str) -> List[Dict]:
        """构建 AI 消息"""
        product_json = json.dumps(product_data, ensure_ascii=False, indent=2)
        image_data_urls: List[str] = []
        for path in image_paths:
            base64_img = self.encode_image(path)
            if base64_img:
                image_data_urls.append(f"data:image/jpeg;base64,{base64_img}")

        return build_analysis_messages(
            product_json,
            prompt_text,
            image_data_urls,
        )

    async def _call_ai(
        self,
        messages: List[Dict],
        *,
        temperature: float = 0.1,
        max_output_tokens: int = 4000,
        enable_json_output: Optional[bool] = None,
        on_text_delta: Optional[Callable[[str], Awaitable[None] | None]] = None,
        request_priority: int = LLM_PRIORITY_NORMAL,
        request_label: str = "llm",
        request_summary: str = "",
        retry_attempt: int | None = None,
        retry_max_attempts: int | None = None,
        retry_error: str = "",
        request_task_id: int | None = None,
        request_generation_job_id: str = "",
        request_generation_mode: str = "",
        request_timeout_seconds: float | None = None,
    ) -> str:
        """Queue one logical LLM call before contacting an upstream endpoint."""
        async with global_llm_request_queue.slot(
            priority=request_priority,
            label=request_label,
            summary=request_summary,
            retry_attempt=retry_attempt,
            retry_max_attempts=retry_max_attempts,
            retry_error=retry_error,
            task_id=request_task_id,
            generation_job_id=request_generation_job_id,
            generation_mode=request_generation_mode,
        ) as queue_lease:
            streamed_parts: list[str] = []
            last_queue_update_at = 0.0

            async def update_queue_content(content: str) -> None:
                try:
                    await queue_lease.update_content(content)
                except Exception as exc:
                    print(f"[LLM队列] 更新流式内容失败: {exc}")

            async def report_text_delta(delta: str) -> None:
                nonlocal last_queue_update_at
                streamed_parts.append(delta)
                now = time.monotonic()
                if now - last_queue_update_at >= 1.0:
                    last_queue_update_at = now
                    await update_queue_content("".join(streamed_parts))
                if on_text_delta is not None:
                    callback_result = on_text_delta(delta)
                    if inspect.isawaitable(callback_result):
                        await callback_result

            request = self._call_ai_without_queue(
                messages,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                enable_json_output=enable_json_output,
                on_text_delta=report_text_delta,
            )
            if request_timeout_seconds is None:
                response_text = await request
            else:
                timeout_seconds = max(0.1, float(request_timeout_seconds))
                try:
                    async with asyncio.timeout(timeout_seconds):
                        response_text = await request
                except TimeoutError as exc:
                    raise TimeoutError(
                        f"上游 LLM 请求超过 {timeout_seconds:g} 秒仍未完成。"
                    ) from exc
            await update_queue_content(
                "".join(streamed_parts) or response_text
            )
            return response_text

    async def _call_ai_without_queue(
        self,
        messages: List[Dict],
        *,
        temperature: float = 0.1,
        max_output_tokens: int = 4000,
        enable_json_output: Optional[bool] = None,
        on_text_delta: Optional[Callable[[str], Awaitable[None] | None]] = None,
    ) -> str:
        """Call an OpenAI-compatible API with endpoint and SSE discovery."""
        requested_json_output = bool(
            self.settings.enable_response_format
            if enable_json_output is None
            else enable_json_output
        )
        reasoning_effort = normalize_reasoning_effort(
            getattr(self.settings, "reasoning_effort", "medium")
        )
        base_url = str(getattr(self.settings, "base_url", "") or "").strip()
        model_name = str(self.settings.model_name)
        auto_detect = bool(getattr(self.settings, "endpoint_auto_detect", True))
        configured_api_mode = normalize_api_mode(
            getattr(self.settings, "api_mode", AUTO_API_MODE)
        )
        stream_mode = normalize_stream_mode(
            getattr(self.settings, "stream_mode", NON_STREAM_MODE)
        )
        cache = self._ensure_endpoint_cache()
        cached = cache.load(base_url, model_name) if base_url else None
        candidates = build_endpoint_candidates(base_url)
        if not candidates:
            candidates = [EndpointCandidate(base_url)]
        if auto_detect:
            ordered_candidates = order_candidates_from_cache(
                candidates,
                cached.get("candidate_index") if cached else None,
            )
        else:
            ordered_candidates = list(enumerate(candidates[:1]))

        last_route_error: Optional[Exception] = None
        max_empty_attempts = 4
        for candidate_index, candidate in ordered_candidates:
            candidate_client, temporary_client = self._client_for_candidate(
                candidate.base_url
            )
            try:
                api_modes = preferred_api_modes(
                    configured_api_mode,
                    model=model_name,
                    mode_hint=candidate.mode_hint,
                )
                cached_matches_candidate = bool(
                    cached and cached.get("candidate_index") == candidate_index
                )
                cached_mode = cached.get("api_mode") if cached_matches_candidate else None
                if cached_mode in api_modes:
                    api_modes = [cached_mode, *[m for m in api_modes if m != cached_mode]]
                if not auto_detect:
                    api_modes = api_modes[:1]

                for api_mode in api_modes:
                    cached_matches_route = bool(
                        cached_matches_candidate and cached_mode == api_mode
                    )
                    use_response_format = requested_json_output
                    use_temperature = True
                    use_reasoning_effort = True
                    if cached_matches_route:
                        use_response_format = bool(
                            requested_json_output
                            and cached.get("supports_json_output", True) is not False
                        )
                        use_temperature = cached.get(
                            "supports_temperature",
                            use_temperature,
                        ) is not False
                        use_reasoning_effort = cached.get(
                            "supports_reasoning_effort",
                            use_reasoning_effort,
                        ) is not False

                    stream_choices = self._stream_choices(
                        stream_mode,
                        cached.get("streaming") if cached_matches_route else None,
                    )
                    route_unsupported = False
                    for use_streaming in stream_choices:
                        stream_unsupported = False
                        for empty_attempt in range(max_empty_attempts):
                            request_params = build_ai_request_params(
                                api_mode,
                                model=model_name,
                                messages=messages,
                                temperature=temperature,
                                max_output_tokens=max_output_tokens,
                                reasoning_effort=reasoning_effort,
                                enable_json_output=use_response_format,
                            )
                            if not use_temperature:
                                request_params = remove_temperature_param(request_params)
                            if not use_reasoning_effort:
                                request_params = remove_reasoning_effort_param(request_params)
                            if self.settings.enable_thinking:
                                request_params["extra_body"] = {"enable_thinking": False}

                            try:
                                response = await create_ai_response_async(
                                    candidate_client,
                                    api_mode,
                                    request_params,
                                    stream=use_streaming,
                                    on_text_delta=on_text_delta,
                                )
                                response_text = extract_ai_response_content(response)
                                capabilities = {
                                    "candidate_index": candidate_index,
                                    "api_mode": api_mode,
                                    "supports_temperature": bool(use_temperature),
                                    "supports_reasoning_effort": bool(use_reasoning_effort),
                                }
                                if stream_mode != NON_STREAM_MODE:
                                    capabilities["streaming"] = use_streaming
                                elif cached_matches_route and "streaming" in cached:
                                    capabilities["streaming"] = cached["streaming"]
                                if requested_json_output:
                                    capabilities["supports_json_output"] = bool(
                                        use_response_format
                                    )
                                elif (
                                    cached_matches_route
                                    and "supports_json_output" in cached
                                ):
                                    capabilities["supports_json_output"] = cached[
                                        "supports_json_output"
                                    ]
                                if base_url:
                                    cache.save(base_url, model_name, capabilities)
                                self.last_resolution = dict(capabilities)
                                print(
                                    "AI endpoint 已命中: "
                                    f"candidate={candidate_index + 1}, api={api_mode}, "
                                    f"stream={'sse' if use_streaming else 'off'}"
                                )
                                return response_text
                            except EmptyAIResponseError as exc:
                                if empty_attempt < max_empty_attempts - 1:
                                    print(
                                        "AI响应为空，正在自动重试 "
                                        f"({empty_attempt + 2}/{max_empty_attempts})"
                                    )
                                    continue
                                raise exc
                            except Exception as exc:
                                changed = False
                                if use_response_format and is_json_output_unsupported_error(exc):
                                    use_response_format = False
                                    changed = True
                                    print("当前模型不支持结构化 JSON 输出，正在移除该参数")
                                if use_temperature and is_temperature_unsupported_error(exc):
                                    use_temperature = False
                                    changed = True
                                    print("当前模型不支持 temperature 参数，正在移除该参数")
                                if (
                                    use_reasoning_effort
                                    and is_reasoning_effort_unsupported_error(exc)
                                ):
                                    use_reasoning_effort = False
                                    changed = True
                                    print("当前模型不支持 reasoning effort，正在移除该参数")
                                if changed:
                                    continue

                                if (
                                    use_streaming
                                    and stream_mode == AUTO_STREAM_MODE
                                    and is_streaming_unsupported_error(exc)
                                ):
                                    stream_unsupported = True
                                    last_route_error = exc
                                    print("当前 endpoint 不支持 SSE，正在回退普通响应")
                                    break

                                unsupported = (
                                    api_mode == CHAT_COMPLETIONS_API_MODE
                                    and is_chat_completions_api_unsupported_error(exc)
                                ) or (
                                    api_mode == RESPONSES_API_MODE
                                    and is_responses_api_unsupported_error(exc)
                                )
                                if unsupported and auto_detect:
                                    route_unsupported = True
                                    last_route_error = exc
                                    print(
                                        f"endpoint 未实现 {api_mode}，继续智能探测"
                                    )
                                    break
                                raise
                        if route_unsupported:
                            break
                        if stream_unsupported:
                            continue
                    if route_unsupported:
                        continue
            finally:
                if temporary_client is not None:
                    await temporary_client.close()

        if last_route_error is not None:
            raise last_route_error
        raise RuntimeError("AI endpoint 探测完成，但没有可用的 API 路由")

    def _client_for_candidate(
        self,
        base_url: str,
    ) -> tuple[AsyncOpenAI, Optional[AsyncOpenAI]]:
        if self.client is None:
            raise RuntimeError("AI 客户端未初始化")
        if not base_url or str(getattr(self, "_client_base_url", "")).rstrip("/") == base_url.rstrip("/"):
            return self.client, None
        temporary = self._initialize_client(base_url, track_base_url=False)
        if temporary is None:
            raise RuntimeError(f"无法为候选 endpoint 初始化 AI 客户端")
        return temporary, temporary

    @staticmethod
    def _stream_choices(stream_mode: str, cached_streaming) -> list[bool]:
        if stream_mode == NON_STREAM_MODE:
            return [False]
        if stream_mode == SSE_STREAM_MODE:
            return [True]
        if cached_streaming is False:
            return [False]
        return [True, False]

    def _parse_response(self, response_text: str) -> Optional[Dict]:
        """解析 AI 响应"""
        try:
            return parse_ai_response_json(response_text)
        except json.JSONDecodeError:
            print(f"无法解析 AI 响应: {response_text[:100]}")
            return None
