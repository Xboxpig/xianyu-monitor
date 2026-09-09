import time

from src.services.llm_request_queue import (
    GlobalLLMRequestQueue,
    LLM_PRIORITY_CRITERIA,
    LLM_PRIORITY_NORMAL,
)


def _queue(tmp_path, concurrency=1):
    database_path = str(tmp_path / "llm-queue.sqlite3")
    return GlobalLLMRequestQueue(
        database_path_provider=lambda: database_path,
        concurrency_provider=lambda: concurrency,
    )


def test_criteria_overtakes_normal_waiters_and_remains_fifo(tmp_path):
    queue = _queue(tmp_path)
    now = time.time()
    queue._enqueue("normal", LLM_PRIORITY_NORMAL, "normal", "商品 A", now)
    queue._enqueue(
        "criteria-1", LLM_PRIORITY_CRITERIA, "criteria", "任务 A", now + 1
    )
    queue._enqueue(
        "criteria-2", LLM_PRIORITY_CRITERIA, "criteria", "任务 B", now + 2
    )

    assert queue._try_activate("normal") is False
    assert queue._try_activate("criteria-2") is False
    assert queue._try_activate("criteria-1") is True

    queue._release("criteria-1")
    assert queue._try_activate("normal") is False
    assert queue._try_activate("criteria-2") is True

    queue._release("criteria-2")
    assert queue._try_activate("normal") is True


def test_queue_enforces_shared_concurrency_across_instances(tmp_path):
    database_path = str(tmp_path / "llm-queue.sqlite3")
    first = GlobalLLMRequestQueue(
        database_path_provider=lambda: database_path,
        concurrency_provider=lambda: 2,
    )
    second = GlobalLLMRequestQueue(
        database_path_provider=lambda: database_path,
        concurrency_provider=lambda: 2,
    )
    now = time.time()
    first._enqueue("first", LLM_PRIORITY_NORMAL, "normal", "商品 A", now)
    second._enqueue("second", LLM_PRIORITY_NORMAL, "normal", "商品 B", now + 1)
    second._enqueue("third", LLM_PRIORITY_NORMAL, "normal", "商品 C", now + 2)

    assert first._try_activate("first") is True
    assert second._try_activate("second") is True
    assert second._try_activate("third") is False

    first._release("first")
    assert second._try_activate("third") is True


def test_snapshot_includes_stream_content_and_wait_estimate(tmp_path):
    queue = _queue(tmp_path)
    now = time.time()
    queue._enqueue(
        "active",
        LLM_PRIORITY_NORMAL,
        "analysis",
        "ps5-bxl - PS5 Slim 白色主机",
        now,
    )
    queue._enqueue(
        "waiting",
        LLM_PRIORITY_NORMAL,
        "analysis",
        "ps5-bxl - PS5 Slim 光驱版",
        now + 1,
    )
    assert queue._try_activate("active") is True
    queue._update_content("active", "正在检查卖家画像")

    snapshot = queue._snapshot()

    assert snapshot["active_count"] == 1
    assert snapshot["retrying_count"] == 0
    assert snapshot["waiting_count"] == 1
    assert snapshot["items"][0]["status"] == "running"
    assert snapshot["items"][0]["stream_content"] == "正在检查卖家画像"
    assert snapshot["items"][1]["status"] == "waiting"
    assert snapshot["items"][1]["estimated_wait_seconds"] > 0


def test_snapshot_exposes_retry_attempt_and_error(tmp_path):
    queue = _queue(tmp_path)
    now = time.time()
    queue._enqueue(
        "retrying",
        LLM_PRIORITY_CRITERIA,
        "criteria",
        "ps5-bxl",
        now,
        3,
        10,
        "Our servers are currently overloaded.",
        "retrying",
    )

    snapshot = queue._snapshot()

    assert snapshot["active_count"] == 0
    assert snapshot["retrying_count"] == 1
    assert snapshot["waiting_count"] == 0
    assert snapshot["items"][0]["status"] == "retrying"
    assert snapshot["items"][0]["retry_attempt"] == 3
    assert snapshot["items"][0]["retry_max_attempts"] == 10
    assert snapshot["items"][0]["retry_error"] == (
        "Our servers are currently overloaded."
    )


def test_snapshot_links_criteria_request_to_generation_pipeline(tmp_path):
    queue = _queue(tmp_path)
    queue._enqueue(
        "criteria-job",
        LLM_PRIORITY_CRITERIA,
        "criteria",
        "ps5-bxl",
        time.time(),
        task_id=12,
        generation_job_id="job-abc",
        generation_mode="regenerate",
    )

    item = queue._snapshot()["items"][0]

    assert item["task_id"] == 12
    assert item["generation_job_id"] == "job-abc"
    assert item["generation_mode"] == "regenerate"
