import asyncio

import pytest

import src.prompt_utils as prompt_utils
from src.services.ai_request_compat import AIStreamingError
from src.services.ai_response_parser import EmptyAIResponseError

# 一段结构完整、长度达标（>=400字符）的分析标准
VALID_CRITERIA = (
    "### **第一部分：核心分析原则 (不可违背)**\n"
    "1. **画像优先原则 (PERSONA-FIRST PRINCIPLE)**: 评估卖家行为画像是否自洽。\n"
    "2. **一票否决硬性原则 (HARD DEAL-BREAKER RULES)**: 任何一项不满足立即否决。\n"
    "### **第二部分：详细分析指南**\n"
    "【危险信号清单 (Red Flag List)】与豁免条款：维修史缺失、交易异常、文案矛盾等。\n"
    + "补充分析要点。" * 60
)


def test_validate_generated_criteria_accepts_complete():
    assert prompt_utils.validate_generated_criteria(VALID_CRITERIA) == []


def test_validate_generated_criteria_rejects_truncated():
    problems = prompt_utils.validate_generated_criteria(
        "### **第一部分：核心分析原则 (不可违背)**\n1. 画像优先原则: 这是"
    )
    assert problems
    joined = "; ".join(problems)
    assert "长度过短" in joined
    assert "一票否决" in joined
    assert "第二部分" in joined
    assert "危险信号" in joined


def test_generate_criteria_closes_ai_client_after_success(monkeypatch, tmp_path):
    close_state = {"closed": False}
    request_kwargs = {}
    reference_file = tmp_path / "reference.txt"
    reference_file.write_text("reference", encoding="utf-8")

    class FakeAIClient:
        def is_available(self):
            return True

        def refresh(self):
            raise AssertionError("refresh should not be called")

        async def _call_ai(self, *_args, **kwargs):
            request_kwargs.update(kwargs)
            return VALID_CRITERIA

        async def close(self):
            close_state["closed"] = True

    monkeypatch.setattr(prompt_utils, "AIClient", FakeAIClient)

    result = asyncio.run(
        prompt_utils.generate_criteria(
            "need a gpu",
            str(reference_file),
            queue_task_id=12,
            queue_generation_job_id="job-abc",
            queue_generation_mode="regenerate",
        )
    )

    assert result == VALID_CRITERIA
    assert close_state["closed"] is True
    assert request_kwargs["request_priority"] == prompt_utils.LLM_PRIORITY_CRITERIA
    assert request_kwargs["request_label"] == "criteria"
    assert request_kwargs["request_task_id"] == 12
    assert request_kwargs["request_generation_job_id"] == "job-abc"
    assert request_kwargs["request_generation_mode"] == "regenerate"


def test_generate_criteria_reports_final_character_count(monkeypatch, tmp_path):
    reference_file = tmp_path / "reference.txt"
    reference_file.write_text("reference", encoding="utf-8")
    progress_events = []

    class FakeAIClient:
        def is_available(self):
            return True

        async def _call_ai(self, *_args, **kwargs):
            await kwargs["on_text_delta"](VALID_CRITERIA)
            return VALID_CRITERIA

        async def close(self):
            pass

    async def report_progress(step_key, message, generated_characters):
        progress_events.append((step_key, message, generated_characters))

    monkeypatch.setattr(prompt_utils, "AIClient", FakeAIClient)

    result = asyncio.run(
        prompt_utils.generate_criteria(
            "need a gpu",
            str(reference_file),
            progress_callback=report_progress,
        )
    )

    assert result == VALID_CRITERIA
    assert progress_events[-1] == (
        "llm",
        f"AI 输出接收完成，共生成 {len(VALID_CRITERIA)} 字符。",
        len(VALID_CRITERIA),
    )


def test_generate_criteria_throttles_stream_progress_to_one_second(monkeypatch, tmp_path):
    reference_file = tmp_path / "reference.txt"
    reference_file.write_text("reference", encoding="utf-8")
    progress_events = []
    monotonic_values = iter((0.0, 0.4, 1.0, 1.4))

    class FakeAIClient:
        def is_available(self):
            return True

        async def _call_ai(self, *_args, **kwargs):
            callback = kwargs["on_text_delta"]
            await callback("a")
            await callback("b")
            await callback("c")
            return VALID_CRITERIA

        async def close(self):
            pass

    async def report_progress(step_key, message, generated_characters):
        progress_events.append((step_key, message, generated_characters))

    class FakeTime:
        @staticmethod
        def monotonic():
            return next(monotonic_values)

    monkeypatch.setattr(prompt_utils, "AIClient", FakeAIClient)
    monkeypatch.setattr(prompt_utils, "time", FakeTime)

    asyncio.run(
        prompt_utils.generate_criteria(
            "need a gpu",
            str(reference_file),
            progress_callback=report_progress,
        )
    )

    streamed_counts = [
        count
        for step, message, count in progress_events
        if step == "llm" and message.startswith("正在接收 SSE 输出")
    ]
    assert streamed_counts == [2]


def test_generate_criteria_closes_ai_client_after_ai_failure(monkeypatch, tmp_path):
    close_state = {"closed": False}
    calls = {"count": 0}
    delays = []
    reference_file = tmp_path / "reference.txt"
    reference_file.write_text("reference", encoding="utf-8")

    class FakeAIClient:
        def is_available(self):
            return True

        def refresh(self):
            raise AssertionError("refresh should not be called")

        async def _call_ai(self, *_args, **_kwargs):
            calls["count"] += 1
            raise EmptyAIResponseError("AI响应内容为空。")

        async def close(self):
            close_state["closed"] = True

    monkeypatch.setattr(prompt_utils, "AIClient", FakeAIClient)

    async def record_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(prompt_utils.asyncio, "sleep", record_sleep)

    with pytest.raises(EmptyAIResponseError, match="AI响应内容为空"):
        asyncio.run(prompt_utils.generate_criteria("need a gpu", str(reference_file)))

    assert close_state["closed"] is True
    assert calls["count"] == 10
    assert delays == [1, 2, 4, 8, 16, 32, 32, 32, 32]


def test_generate_criteria_retries_upstream_errors_with_capped_backoff(
    monkeypatch,
    tmp_path,
):
    reference_file = tmp_path / "reference.txt"
    reference_file.write_text("reference", encoding="utf-8")
    calls = {"count": 0}
    delays = []
    progress_events = []

    class FakeAIClient:
        def is_available(self):
            return True

        async def _call_ai(self, *_args, **_kwargs):
            calls["count"] += 1
            if calls["count"] < prompt_utils.CRITERIA_API_MAX_ATTEMPTS:
                raise AIStreamingError(
                    "Responses SSE event: response.failed: "
                    "Our servers are currently overloaded."
                )
            return VALID_CRITERIA

        async def close(self):
            pass

    async def record_sleep(seconds):
        delays.append(seconds)

    async def report_progress(step_key, message, generated_characters):
        progress_events.append((step_key, message, generated_characters))

    monkeypatch.setattr(prompt_utils, "AIClient", FakeAIClient)
    monkeypatch.setattr(prompt_utils.asyncio, "sleep", record_sleep)

    result = asyncio.run(
        prompt_utils.generate_criteria(
            "need a gpu",
            str(reference_file),
            progress_callback=report_progress,
        )
    )

    assert result == VALID_CRITERIA
    assert calls["count"] == 10
    assert delays == [1, 2, 4, 8, 16, 32, 32, 32, 32]
    assert any("第 9 次调用失败" in message for _, message, _ in progress_events)
    assert any("response.failed" in message for _, message, _ in progress_events)


def test_generate_criteria_retries_until_complete(monkeypatch, tmp_path):
    reference_file = tmp_path / "reference.txt"
    reference_file.write_text("reference", encoding="utf-8")
    calls = {"n": 0}

    class FakeAIClient:
        def is_available(self):
            return True

        async def _call_ai(self, *_args, **_kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                return "### 第一部分 (被截断"
            return VALID_CRITERIA

        async def close(self):
            pass

    monkeypatch.setattr(prompt_utils, "AIClient", FakeAIClient)

    result = asyncio.run(
        prompt_utils.generate_criteria("need a gpu", str(reference_file))
    )

    assert result == VALID_CRITERIA
    assert calls["n"] == 3


def test_generate_criteria_fails_after_max_attempts(monkeypatch, tmp_path):
    reference_file = tmp_path / "reference.txt"
    reference_file.write_text("reference", encoding="utf-8")

    class FakeAIClient:
        def is_available(self):
            return True

        async def _call_ai(self, *_args, **_kwargs):
            return "### 第一部分 (总是被截断)"

        async def close(self):
            pass

    monkeypatch.setattr(prompt_utils, "AIClient", FakeAIClient)

    with pytest.raises(RuntimeError, match="仍不完整"):
        asyncio.run(prompt_utils.generate_criteria("need a gpu", str(reference_file)))


def test_parse_search_params_json_strips_code_fence():
    raw = '```json\n{"min_price": 1000, "max_price": 5000, "exclude_keywords": ["二手"]}\n```'
    assert prompt_utils._parse_search_params_json(raw) == {
        "min_price": 1000,
        "max_price": 5000,
        "exclude_keywords": ["二手"],
    }


def test_parse_search_params_json_recovers_from_prose():
    raw = '提取结果如下：{"min_price": null, "exclude_keywords": []}'
    assert prompt_utils._parse_search_params_json(raw) == {
        "min_price": None,
        "exclude_keywords": [],
    }


def test_extract_search_params_parses_ai_json(monkeypatch):
    request_kwargs = {}

    class FakeAIClient:
        def is_available(self):
            return True

        async def _call_ai(self, *_args, **kwargs):
            request_kwargs.update(kwargs)
            return (
                '{"min_price": 1000, "max_price": null, '
                '"exclude_keywords": ["二手", "矿卡"]}'
            )

        async def close(self):
            pass

    monkeypatch.setattr(prompt_utils, "AIClient", FakeAIClient)

    params = asyncio.run(prompt_utils.extract_search_params("预算1000以上，不要二手矿卡"))
    assert params["min_price"] == 1000
    assert params["max_price"] is None
    assert params["exclude_keywords"] == ["二手", "矿卡"]
    assert request_kwargs["request_timeout_seconds"] == 60.0


def test_extract_search_params_returns_defaults_on_ai_failure(monkeypatch):
    class FakeAIClient:
        def is_available(self):
            return True

        async def _call_ai(self, *_args, **_kwargs):
            raise EmptyAIResponseError("AI响应内容为空。")

        async def close(self):
            pass

    monkeypatch.setattr(prompt_utils, "AIClient", FakeAIClient)

    params = asyncio.run(prompt_utils.extract_search_params("need a gpu"))
    assert params == prompt_utils.DEFAULT_SEARCH_PARAMS
