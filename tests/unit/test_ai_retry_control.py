import asyncio

import pytest

from src.services.ai_retry_control import (
    AIAnalysisRetryCancelled,
    AIAnalysisRetryControl,
    read_ai_retry_status,
    request_ai_retry_cancel,
)


def test_retry_control_reports_status_and_interrupts_backoff(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_RETRY_CONTROL_DIR", str(tmp_path / "control"))
    control = AIAnalysisRetryControl(7, product_id="item-1", max_attempts=10)
    control.update(phase="backoff", attempt=3, next_delay_seconds=4)

    status = read_ai_retry_status(7)
    assert status == {
        "active": True,
        "active_count": 1,
        "phase": "backoff",
        "attempt": 3,
        "max_attempts": 10,
        "next_delay_seconds": 4,
    }

    async def scenario():
        waiter = asyncio.create_task(control.wait(60))
        await asyncio.sleep(0)
        request_ai_retry_cancel(7)
        with pytest.raises(AIAnalysisRetryCancelled):
            await asyncio.wait_for(waiter, timeout=1)

    asyncio.run(scenario())
    control.close()
    assert read_ai_retry_status(7)["active"] is False


def test_new_analysis_uses_latest_cancel_generation(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_RETRY_CONTROL_DIR", str(tmp_path / "control"))
    request_ai_retry_cancel(8)
    control = AIAnalysisRetryControl(8, product_id="item-2", max_attempts=10)

    control.raise_if_cancelled()
    control.close()
