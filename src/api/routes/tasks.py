"""
任务管理路由
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from typing import List
import os
from src.api.dependencies import (
    get_process_service,
    get_scheduler_service,
    get_task_generation_service,
    get_task_service,
)
from src.services.task_service import TaskService
from src.services.process_service import ProcessService, TaskStartError
from src.services.ai_recovery_service import ai_recovery_service
from src.services.scheduler_service import SchedulerService
from src.services.task_generation_service import TaskGenerationService
from src.services.task_generation_runner import (
    CRITERIA_REGENERATION_STEPS,
    build_task_create,
    build_criteria_filename,
    run_ai_generation_job,
    run_criteria_regeneration_job,
)
from src.services.task_payloads import serialize_task, serialize_tasks
from src.domain.models.task import TaskCreate, TaskUpdate, TaskGenerateRequest
from src.utils import resolve_task_log_path
from src.services.account_strategy_service import normalize_account_strategy
from src.infrastructure.persistence.storage_names import build_result_filename
from src.services.price_history_service import delete_price_snapshots
from src.services.result_storage_service import delete_result_file_records
router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _task_start_error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    context: dict | None = None,
) -> JSONResponse:
    """Return both a UI-readable detail and a stable machine-readable code."""
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": f"错误代码：{code}\n{message}",
            "code": code,
            "context": context or {},
        },
    )

async def _reload_scheduler_if_needed(
    task_service: TaskService,
    scheduler_service: SchedulerService,
):
    tasks = await task_service.get_all_tasks()
    await scheduler_service.reload_jobs(tasks)


def _has_keyword_rules(rules) -> bool:
    return bool(rules and len(rules) > 0)


def _validate_final_account_strategy(existing_task, task_update: TaskUpdate) -> None:
    account_state_file = (
        task_update.account_state_file
        if task_update.account_state_file is not None
        else existing_task.account_state_file
    )
    account_strategy = normalize_account_strategy(
        task_update.account_strategy,
        account_state_file,
    )
    task_update.account_strategy = account_strategy
    if account_strategy == "fixed" and not account_state_file:
        raise HTTPException(status_code=400, detail="固定账号模式下必须选择账号。")
@router.get("", response_model=List[dict])
async def get_tasks(
    service: TaskService = Depends(get_task_service),
    scheduler_service: SchedulerService = Depends(get_scheduler_service),
):
    """获取所有任务"""
    tasks = await service.get_all_tasks()
    return serialize_tasks(tasks, scheduler_service)
@router.get("/{task_id}", response_model=dict)
async def get_task(
    task_id: int,
    service: TaskService = Depends(get_task_service),
    scheduler_service: SchedulerService = Depends(get_scheduler_service),
):
    """获取单个任务"""
    task = await service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")
    return serialize_task(task, scheduler_service)
@router.post("/", response_model=dict)
async def create_task(
    task_create: TaskCreate,
    service: TaskService = Depends(get_task_service),
    scheduler_service: SchedulerService = Depends(get_scheduler_service),
):
    """创建新任务"""
    task = await service.create_task(task_create)
    await _reload_scheduler_if_needed(service, scheduler_service)
    return {"message": "任务创建成功", "task": serialize_task(task, scheduler_service)}
@router.post("/generate", response_model=dict)
async def generate_task(
    req: TaskGenerateRequest,
    service: TaskService = Depends(get_task_service),
    scheduler_service: SchedulerService = Depends(get_scheduler_service),
    generation_service: TaskGenerationService = Depends(get_task_generation_service),
):
    """创建任务。AI模式会生成分析标准，关键词模式直接保存规则。"""
    print(f"收到任务生成请求: {req.task_name}，模式: {req.decision_mode}")

    try:
        mode = req.decision_mode or "ai"
        if mode == "ai":
            job = await generation_service.create_job(req.task_name)
            generation_service.track(
                run_ai_generation_job(
                    job_id=job.job_id,
                    req=req,
                    task_service=service,
                    scheduler_service=scheduler_service,
                    generation_service=generation_service,
                )
            )
            return JSONResponse(
                status_code=202,
                content={
                    "message": "AI 任务生成已开始。",
                    "job": job.model_dump(mode="json"),
                },
            )

        task = await service.create_task(build_task_create(req, ""))
        await _reload_scheduler_if_needed(service, scheduler_service)
        return {"message": "任务创建成功。", "task": serialize_task(task, scheduler_service)}

    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"AI任务生成API发生未知错误: {str(e)}"
        print(error_msg)
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=error_msg)
@router.get("/generate-jobs/{job_id}", response_model=dict)
async def get_task_generation_job(
    job_id: str,
    generation_service: TaskGenerationService = Depends(get_task_generation_service),
):
    """获取任务生成作业状态"""
    job = await generation_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务生成作业未找到")
    return {"job": job.model_dump(mode="json")}


@router.get("/generation/jobs", response_model=dict)
async def list_task_generation_jobs(
    active_only: bool = True,
    generation_service: TaskGenerationService = Depends(get_task_generation_service),
):
    """列出任务生成流水线，供全局 AI 队列持续展示。"""
    jobs = await generation_service.list_jobs(active_only=active_only)
    return {"jobs": [job.model_dump(mode="json") for job in jobs]}
@router.patch("/{task_id}", response_model=dict)
async def update_task(
    task_id: int,
    task_update: TaskUpdate,
    regenerate_criteria: bool = False,
    service: TaskService = Depends(get_task_service),
    scheduler_service: SchedulerService = Depends(get_scheduler_service),
    generation_service: TaskGenerationService = Depends(get_task_generation_service),
):
    """更新任务"""
    try:
        existing_task = await service.get_task(task_id)
        if not existing_task:
            raise HTTPException(status_code=404, detail="任务未找到")
        _validate_final_account_strategy(existing_task, task_update)

        current_mode = getattr(existing_task, "decision_mode", "ai") or "ai"
        target_mode = task_update.decision_mode or current_mode
        description_changed = (
            task_update.description is not None
            and task_update.description != existing_task.description
        )
        switched_to_ai = current_mode != "ai" and target_mode == "ai"

        if target_mode == "keyword":
            final_rules = (
                task_update.keyword_rules
                if task_update.keyword_rules is not None
                else getattr(existing_task, "keyword_rules", [])
            )
            if not _has_keyword_rules(final_rules):
                raise HTTPException(status_code=400, detail="关键词模式下至少需要一个关键词。")
        needs_criteria_generation = target_mode == "ai" and (
            description_changed or switched_to_ai or regenerate_criteria
        )
        if needs_criteria_generation:
            description_for_ai = (
                task_update.description
                if task_update.description is not None
                else existing_task.description
            )
            if not str(description_for_ai or "").strip():
                raise HTTPException(status_code=400, detail="AI 模式下详细需求不能为空。")
            output_filename = build_criteria_filename(existing_task.keyword)
            job = await generation_service.create_job(
                existing_task.task_name,
                CRITERIA_REGENERATION_STEPS,
            )
            generation_service.track(
                run_criteria_regeneration_job(
                    job_id=job.job_id,
                    task_id=task_id,
                    task_name=existing_task.task_name,
                    task_update=task_update,
                    description=str(description_for_ai),
                    output_filename=output_filename,
                    task_service=service,
                    scheduler_service=scheduler_service,
                    generation_service=generation_service,
                )
            )
            return JSONResponse(
                status_code=202,
                content={
                    "message": "AI 标准重新生成已开始。",
                    "job": job.model_dump(mode="json"),
                },
            )
        task = await service.update_task(task_id, task_update)
        await _reload_scheduler_if_needed(service, scheduler_service)
        return {"message": "任务更新成功", "task": serialize_task(task, scheduler_service)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
@router.delete("/{task_id}", response_model=dict)
async def delete_task(
    task_id: int,
    service: TaskService = Depends(get_task_service),
    process_service: ProcessService = Depends(get_process_service),
    scheduler_service: SchedulerService = Depends(get_scheduler_service),
):
    """删除任务"""
    task = await service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")

    await process_service.stop_task(task_id)
    success = await service.delete_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="任务未找到")
    await _reload_scheduler_if_needed(service, scheduler_service)
    try:
        keyword = (task.keyword or "").strip()
        if keyword:
            remaining_tasks = await service.get_all_tasks()
            keyword_still_in_use = any(
                (remaining_task.keyword or "").strip() == keyword
                for remaining_task in remaining_tasks
            )
            if not keyword_still_in_use:
                await delete_result_file_records(build_result_filename(keyword))
                delete_price_snapshots(keyword)
    except Exception as e:
        print(f"删除任务结果文件时出错: {e}")

    try:
        log_file_path = resolve_task_log_path(task_id, task.task_name)
        if os.path.exists(log_file_path):
            os.remove(log_file_path)
    except Exception as e:
        print(f"删除任务日志文件时出错: {e}")
    return {"message": "任务删除成功"}
@router.post("/start/{task_id}", response_model=dict)
async def start_task(
    task_id: int,
    task_service: TaskService = Depends(get_task_service),
    process_service: ProcessService = Depends(get_process_service),
):
    """启动单个任务"""
    task = await task_service.get_task(task_id)
    if not task:
        return _task_start_error_response(
            status_code=404,
            code="TASK_NOT_FOUND",
            message=f"未找到 ID 为 {task_id} 的任务。",
            context={"task_id": task_id},
        )
    if not task.enabled:
        return _task_start_error_response(
            status_code=409,
            code="TASK_DISABLED",
            message=f"任务“{task.task_name}”已被禁用，无法启动。",
            context={"task_id": task_id, "task_name": task.task_name},
        )
    if task.is_running:
        return _task_start_error_response(
            status_code=409,
            code="TASK_ALREADY_RUNNING",
            message=f"任务“{task.task_name}”已在运行中。",
            context={"task_id": task_id, "task_name": task.task_name},
        )
    try:
        success = await process_service.start_task(
            task_id,
            task.task_name,
            raise_on_failure=True,
        )
    except TaskStartError as exc:
        return _task_start_error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            context=exc.context,
        )
    if not success:
        return _task_start_error_response(
            status_code=500,
            code="TASK_PROCESS_START_FAILED",
            message=f"任务“{task.task_name}”的爬虫进程未能启动，后端未返回更多信息。",
            context={"task_id": task_id, "task_name": task.task_name},
        )
    return {"message": f"任务 '{task.task_name}' 已启动"}
@router.post("/stop/{task_id}", response_model=dict)
async def stop_task(
    task_id: int,
    task_service: TaskService = Depends(get_task_service),
    process_service: ProcessService = Depends(get_process_service),
):
    """停止单个任务"""
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")
    await process_service.stop_task(task_id)
    return {"message": f"任务ID {task_id} 已发送停止信号"}


@router.get("/{task_id}/ai-analysis/status", response_model=dict)
async def get_task_ai_analysis_status(
    task_id: int,
    task_service: TaskService = Depends(get_task_service),
):
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")
    return ai_recovery_service.status(task_id)


@router.post("/{task_id}/ai-analysis/retry", response_model=dict)
async def retry_failed_task_ai_analysis(
    task_id: int,
    task_service: TaskService = Depends(get_task_service),
):
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")
    try:
        return await ai_recovery_service.start(task_id, task)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{task_id}/ai-analysis/cancel", response_model=dict)
async def cancel_task_ai_analysis(
    task_id: int,
    task_service: TaskService = Depends(get_task_service),
):
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")
    return await ai_recovery_service.cancel(task_id, task.task_name)
