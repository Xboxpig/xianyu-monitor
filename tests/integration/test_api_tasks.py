import asyncio
import time

from src.api.routes import tasks as tasks_routes
from src.services.process_service import TaskStartError


def test_create_list_update_delete_task(api_client, api_context, sample_task_payload):
    response = api_client.post("/api/tasks/", json=sample_task_payload)
    assert response.status_code == 200
    created = response.json()["task"]
    assert created["task_name"] == sample_task_payload["task_name"]
    assert created["analyze_images"] is True
    assert created["next_run_at"] == "2026-03-19T08:15:00+08:00"

    response = api_client.get("/api/tasks")
    assert response.status_code == 200
    tasks = response.json()
    assert len(tasks) == 1
    assert tasks[0]["keyword"] == sample_task_payload["keyword"]
    assert tasks[0]["analyze_images"] is True
    assert tasks[0]["next_run_at"] == "2026-03-19T08:15:00+08:00"

    response = api_client.patch("/api/tasks/0", json={"enabled": False, "analyze_images": False})
    assert response.status_code == 200
    updated = response.json()["task"]
    assert updated["enabled"] is False
    assert updated["analyze_images"] is False
    assert updated["next_run_at"] is None

    response = api_client.delete("/api/tasks/0")
    assert response.status_code == 200

    response = api_client.get("/api/tasks")
    assert response.status_code == 200
    assert response.json() == []


def test_start_stop_task_updates_status(api_client, api_context, sample_task_payload):
    response = api_client.post("/api/tasks/", json=sample_task_payload)
    assert response.status_code == 200

    response = api_client.post("/api/tasks/start/0")
    assert response.status_code == 200

    response = api_client.get("/api/tasks/0")
    assert response.status_code == 200
    assert response.json()["is_running"] is True

    response = api_client.post("/api/tasks/stop/0")
    assert response.status_code == 200

    response = api_client.get("/api/tasks/0")
    assert response.status_code == 200
    assert response.json()["is_running"] is False

    process_service = api_context["process_service"]
    assert process_service.started == [(0, sample_task_payload["task_name"])]
    assert process_service.stopped == [0]


def test_start_status_update_does_not_clear_fixed_account(
    api_client,
    sample_task_payload,
):
    payload = dict(sample_task_payload)
    payload["account_strategy"] = "fixed"
    payload["account_state_file"] = "state/123.json"
    assert api_client.post("/api/tasks/", json=payload).status_code == 200

    assert api_client.post("/api/tasks/start/0").status_code == 200
    running_task = api_client.get("/api/tasks/0").json()

    assert running_task["is_running"] is True
    assert running_task["account_strategy"] == "fixed"
    assert running_task["account_state_file"] == "state/123.json"


def test_start_task_exposes_failure_guard_code_and_context(
    api_client,
    api_context,
    sample_task_payload,
    monkeypatch,
):
    assert api_client.post("/api/tasks/", json=sample_task_payload).status_code == 200

    async def blocked_start(_task_id, _task_name, *, raise_on_failure=False):
        assert raise_on_failure is True
        raise TaskStartError(
            code="TASK_PAUSED_BY_FAILURE_GUARD",
            message=(
                "任务“Sony A7M4”已被 FailureGuard 暂停至 2026-09-10 10:47:12 +0800；"
                "连续失败 3/3；最近错误：TimeoutError: waiting for response"
            ),
            status_code=409,
            context={
                "paused_until": "2026-09-10T10:47:12+08:00",
                "consecutive_failures": 3,
                "failure_threshold": 3,
                "last_error": "TimeoutError: waiting for response",
            },
        )

    monkeypatch.setattr(api_context["process_service"], "start_task", blocked_start)

    response = api_client.post("/api/tasks/start/0")

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "TASK_PAUSED_BY_FAILURE_GUARD"
    assert "错误代码：TASK_PAUSED_BY_FAILURE_GUARD" in body["detail"]
    assert "连续失败 3/3" in body["detail"]
    assert "TimeoutError: waiting for response" in body["detail"]
    assert body["context"]["paused_until"] == "2026-09-10T10:47:12+08:00"


def test_start_task_exposes_generic_process_failure_code(
    api_client,
    api_context,
    sample_task_payload,
    monkeypatch,
):
    assert api_client.post("/api/tasks/", json=sample_task_payload).status_code == 200

    async def failed_start(_task_id, _task_name, *, raise_on_failure=False):
        assert raise_on_failure is True
        return False

    monkeypatch.setattr(api_context["process_service"], "start_task", failed_start)

    response = api_client.post("/api/tasks/start/0")

    assert response.status_code == 500
    assert response.json()["code"] == "TASK_PROCESS_START_FAILED"
    assert "错误代码：TASK_PROCESS_START_FAILED" in response.json()["detail"]


def test_task_ai_analysis_control_endpoints(
    api_client,
    sample_task_payload,
    monkeypatch,
):
    response = api_client.post("/api/tasks/", json=sample_task_payload)
    assert response.status_code == 200

    idle = {"active": False, "active_count": 0, "mode": None, "progress": {}}
    running = {
        "active": True,
        "active_count": 1,
        "mode": "recovery",
        "progress": {"total": 2, "completed": 0, "failed": 0},
    }

    monkeypatch.setattr(tasks_routes.ai_recovery_service, "status", lambda _task_id: idle)

    async def fake_start(_task_id, _task):
        return running

    async def fake_cancel(_task_id, _task_name):
        return idle

    monkeypatch.setattr(tasks_routes.ai_recovery_service, "start", fake_start)
    monkeypatch.setattr(tasks_routes.ai_recovery_service, "cancel", fake_cancel)

    assert api_client.get("/api/tasks/0/ai-analysis/status").json() == idle
    assert api_client.post("/api/tasks/0/ai-analysis/retry").json() == running
    assert api_client.post("/api/tasks/0/ai-analysis/cancel").json() == idle


def test_generate_keyword_mode_task_without_ai_criteria(api_client):
    payload = {
        "task_name": "A7M4 关键词筛选",
        "keyword": "sony a7m4",
        "description": "",
        "decision_mode": "keyword",
        "keyword_rules": ["a7m4", "验货宝"],
        "max_pages": 2,
        "personal_only": True,
    }

    response = api_client.post("/api/tasks/generate", json=payload)
    assert response.status_code == 200
    created = response.json()["task"]
    assert created["decision_mode"] == "keyword"
    assert created["ai_prompt_criteria_file"] == ""
    assert created["keyword_rules"] == ["a7m4", "验货宝"]


def test_list_generation_jobs_keeps_active_pipeline_visible(api_client, api_context):
    service = api_context["task_generation_service"]
    created = asyncio.run(service.create_job("NAS"))

    response = api_client.get("/api/tasks/generation/jobs?active_only=true")

    assert response.status_code == 200
    jobs = response.json()["jobs"]
    assert [job["job_id"] for job in jobs] == [created.job_id]
    assert jobs[0]["task_name"] == "NAS"
    assert jobs[0]["status"] == "queued"


def test_generate_ai_task_returns_job_and_completes_async(api_client, api_context, monkeypatch):
    payload = {
        "task_name": "Apple Watch S10",
        "keyword": "apple watch s10",
        "description": "只看国行蜂窝版，电池健康高于 95%，拒绝维修机。",
        "analyze_images": False,
        "decision_mode": "ai",
        "max_pages": 2,
        "personal_only": True,
    }

    criteria_call = {}

    async def fake_generate_criteria(*_args, **kwargs):
        criteria_call.update(kwargs)
        await asyncio.sleep(0.05)
        return (
            "### **第一部分：核心分析原则 (不可违背)**\n"
            "1. **画像优先原则 (PERSONA-FIRST PRINCIPLE)**: 评估卖家行为画像是否自洽。\n"
            "2. **一票否决硬性原则 (HARD DEAL-BREAKER RULES)**: 任何一项不满足立即否决。\n"
            "### **第二部分：详细分析指南**\n"
            "【危险信号清单 (Red Flag List)】与豁免条款：维修史缺失、交易异常等。\n"
            + "补充分析要点。" * 60
        )

    monkeypatch.setattr(
        "src.services.task_generation_runner.generate_criteria",
        fake_generate_criteria,
    )

    async def fake_extract_search_params(*_args, **_kwargs):
        return {"min_price": None, "max_price": None, "exclude_keywords": []}

    monkeypatch.setattr(
        "src.services.task_generation_runner.extract_search_params",
        fake_extract_search_params,
    )

    response = api_client.post("/api/tasks/generate", json=payload)

    assert response.status_code == 202
    job = response.json()["job"]
    assert isinstance(job["job_id"], str)
    assert job["status"] in {"queued", "running"}
    assert job["task"] is None

    status_response = api_client.get(f"/api/tasks/generate-jobs/{job['job_id']}")
    assert status_response.status_code == 200

    for _ in range(50):
        status_response = api_client.get(f"/api/tasks/generate-jobs/{job['job_id']}")
        latest_job = status_response.json()["job"]
        if latest_job["status"] == "completed":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("任务生成作业未在预期时间内完成")

    assert latest_job["task"]["task_name"] == payload["task_name"]
    assert latest_job["task"]["ai_prompt_criteria_file"].endswith("_criteria.txt")
    assert latest_job["task"]["analyze_images"] is False
    assert criteria_call["queue_generation_job_id"] == job["job_id"]
    assert criteria_call["queue_generation_mode"] == "create"
    assert criteria_call.get("queue_task_id") is None
    assert api_context["scheduler_service"].reload_calls == 1


def test_regenerate_criteria_returns_job_with_character_progress(
    api_client,
    api_context,
    sample_task_payload,
    monkeypatch,
):
    assert api_client.post("/api/tasks/", json=sample_task_payload).status_code == 200

    criteria_call = {}

    async def fake_generate_criteria(*_args, **kwargs):
        criteria_call.update(kwargs)
        progress_callback = kwargs["progress_callback"]
        await progress_callback("llm", "正在接收 SSE 输出，已生成 321 字符。", 321)
        return "新的完整分析标准" * 80

    monkeypatch.setattr(
        "src.services.task_generation_runner.generate_criteria",
        fake_generate_criteria,
    )

    response = api_client.patch(
        "/api/tasks/0?regenerate_criteria=true",
        json={"description": "更新后的详细需求"},
    )
    assert response.status_code == 202
    job_id = response.json()["job"]["job_id"]

    for _ in range(50):
        latest_job = api_client.get(
            f"/api/tasks/generate-jobs/{job_id}"
        ).json()["job"]
        if latest_job["status"] == "completed":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("criteria 重新生成作业未在预期时间内完成")

    assert latest_job["generated_characters"] == 321
    assert latest_job["task"]["description"] == "更新后的详细需求"
    assert criteria_call["queue_task_id"] == 0
    assert criteria_call["queue_generation_job_id"] == job_id
    assert criteria_call["queue_generation_mode"] == "regenerate"
    assert api_context["scheduler_service"].reload_calls == 2


def test_create_task_accepts_cron_alias(api_client, sample_task_payload):
    payload = dict(sample_task_payload)
    payload["cron"] = "@daily"

    response = api_client.post("/api/tasks/", json=payload)

    assert response.status_code == 200
    assert response.json()["task"]["cron"] == "0 0 * * *"


def test_create_task_rejects_fixed_account_strategy_without_state_file(api_client, sample_task_payload):
    payload = dict(sample_task_payload)
    payload["account_strategy"] = "fixed"

    response = api_client.post("/api/tasks/", json=payload)

    assert response.status_code == 422


def test_create_task_accepts_rotate_account_strategy(api_client, sample_task_payload):
    payload = dict(sample_task_payload)
    payload["account_strategy"] = "rotate"

    response = api_client.post("/api/tasks/", json=payload)

    assert response.status_code == 200
    task = response.json()["task"]
    assert task["account_strategy"] == "rotate"


def test_update_task_accepts_six_field_cron_expression(api_client, sample_task_payload):
    create_response = api_client.post("/api/tasks/", json=sample_task_payload)
    assert create_response.status_code == 200

    response = api_client.patch("/api/tasks/0", json={"cron": "0 0 8 * * *"})

    assert response.status_code == 200

    task_response = api_client.get("/api/tasks/0")
    assert task_response.status_code == 200
    assert task_response.json()["cron"] == "0 0 8 * * *"


def test_create_task_rejects_invalid_cron_expression(api_client, sample_task_payload):
    payload = dict(sample_task_payload)
    payload["cron"] = "every day at 8"

    response = api_client.post("/api/tasks/", json=payload)

    assert response.status_code == 422


def test_delete_task_stops_runtime_and_reindexes_process_state(
    api_client,
    api_context,
    sample_task_payload,
):
    second_payload = dict(sample_task_payload)
    second_payload["task_name"] = "Sony A7CR"
    second_payload["keyword"] = "sony a7cr"
    second_payload["ai_prompt_criteria_file"] = "prompts/sony_a7cr_criteria.txt"

    assert api_client.post("/api/tasks/", json=sample_task_payload).status_code == 200
    assert api_client.post("/api/tasks/", json=second_payload).status_code == 200
    assert api_client.post("/api/tasks/start/0").status_code == 200

    response = api_client.delete("/api/tasks/0")

    assert response.status_code == 200
    process_service = api_context["process_service"]
    assert process_service.stopped == [0]
    assert process_service.reindexed == []
