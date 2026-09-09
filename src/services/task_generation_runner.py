"""
任务生成作业执行器
"""
import os
from typing import Optional

import aiofiles

from src.domain.models.task import TaskCreate, TaskGenerateRequest, TaskUpdate
from src.prompt_utils import extract_search_params, generate_criteria
from src.services.scheduler_service import SchedulerService
from src.services.task_generation_service import TaskGenerationService
from src.services.task_service import TaskService


CRITERIA_REGENERATION_STEPS: tuple[tuple[str, str], ...] = (
    ("prepare", "接收更新请求"),
    ("reference", "读取参考文件"),
    ("prompt", "构建提示词"),
    ("llm", "调用 AI 生成标准"),
    ("persist", "保存分析标准"),
    ("task", "更新任务记录"),
)

def build_criteria_filename(keyword: str) -> str:
    safe_keyword = "".join(
        char for char in keyword.lower().replace(" ", "_")
        if char.isalnum() or char in "_-"
    ).rstrip()
    return f"prompts/{safe_keyword}_criteria.txt"


def _apply_extracted_params(req: TaskGenerateRequest, extracted: dict) -> dict:
    """把 AI 从需求中提取的结构化参数回流到任务字段。

    仅在用户未显式填写时生效：价格区间取提取值，排除词按用户值优先合并。
    """
    applied: dict = {}

    for field in ("min_price", "max_price"):
        user_value = getattr(req, field)
        extracted_value = extracted.get(field)
        if user_value in (None, "") and extracted_value is not None:
            applied[field] = (
                str(int(extracted_value))
                if isinstance(extracted_value, float) and extracted_value.is_integer()
                else str(extracted_value)
            )

    user_excludes = [str(k).strip() for k in (req.exclude_keywords or []) if str(k).strip()]
    if not user_excludes:
        extracted_excludes = [
            str(k).strip() for k in (extracted.get("exclude_keywords") or []) if str(k).strip()
        ]
        if extracted_excludes:
            applied["exclude_keywords"] = extracted_excludes

    return applied


def build_task_create(
    req: TaskGenerateRequest,
    criteria_file: str,
    extracted_params: Optional[dict] = None,
) -> TaskCreate:
    applied = _apply_extracted_params(req, extracted_params or {})
    return TaskCreate(
        task_name=req.task_name,
        enabled=True,
        keyword=req.keyword,
        description=req.description or "",
        analyze_images=req.analyze_images,
        max_pages=req.max_pages,
        personal_only=req.personal_only,
        min_price=applied.get("min_price", req.min_price),
        max_price=applied.get("max_price", req.max_price),
        cron=req.cron,
        ai_prompt_base_file="prompts/base_prompt.txt",
        ai_prompt_criteria_file=criteria_file,
        account_state_file=req.account_state_file,
        account_strategy=req.account_strategy,
        free_shipping=req.free_shipping,
        new_publish_option=req.new_publish_option,
        region=req.region,
        decision_mode=req.decision_mode or "ai",
        keyword_rules=req.keyword_rules,
        keyword_rule_mode=req.keyword_rule_mode,
        exclude_keywords=applied.get("exclude_keywords", req.exclude_keywords),
    )


async def save_generated_criteria(output_filename: str, generated_criteria: str) -> None:
    if not generated_criteria or not generated_criteria.strip():
        raise RuntimeError("AI 未能生成分析标准，返回内容为空。")

    os.makedirs("prompts", exist_ok=True)
    async with aiofiles.open(output_filename, "w", encoding="utf-8") as file:
        await file.write(generated_criteria)


async def reload_scheduler(
    task_service: TaskService,
    scheduler_service: SchedulerService,
) -> None:
    tasks = await task_service.get_all_tasks()
    await scheduler_service.reload_jobs(tasks)


async def advance_job(
    generation_service: TaskGenerationService,
    job_id: str,
    step_key: str,
    message: str,
    generated_characters: Optional[int] = None,
) -> None:
    await generation_service.advance(
        job_id,
        step_key,
        message,
        generated_characters=generated_characters,
    )


async def run_ai_generation_job(
    *,
    job_id: str,
    req: TaskGenerateRequest,
    task_service: TaskService,
    scheduler_service: SchedulerService,
    generation_service: TaskGenerationService,
) -> None:
    output_filename = build_criteria_filename(req.keyword)
    try:
        await advance_job(
            generation_service,
            job_id,
            "prepare",
            "已接收请求，开始准备分析标准。",
        )

        async def report_progress(
            step_key: str,
            message: str,
            generated_characters: Optional[int] = None,
        ) -> None:
            await advance_job(
                generation_service,
                job_id,
                step_key,
                message,
                generated_characters,
            )

        generated_criteria = await generate_criteria(
            user_description=req.description or "",
            reference_file_path="prompts/macbook_criteria.txt",
            progress_callback=report_progress,
            queue_summary=req.task_name,
            queue_generation_job_id=job_id,
            queue_generation_mode="create",
        )

        await advance_job(
            generation_service,
            job_id,
            "extract",
            "正在从需求中提取结构化搜索参数。",
        )
        extracted_params = await extract_search_params(
            req.description or "",
            queue_summary=req.task_name,
            queue_generation_job_id=job_id,
            queue_generation_mode="create",
        )
        if (
            extracted_params.get("min_price") is not None
            or extracted_params.get("max_price") is not None
            or extracted_params.get("exclude_keywords")
        ):
            print(f"已从需求提取搜索参数: {extracted_params}")

        await advance_job(
            generation_service,
            job_id,
            "persist",
            f"正在保存分析标准到 {output_filename}。",
        )
        await save_generated_criteria(output_filename, generated_criteria)

        await advance_job(
            generation_service,
            job_id,
            "task",
            "分析标准已生成，正在创建任务记录。",
        )
        task = await task_service.create_task(
            build_task_create(req, output_filename, extracted_params)
        )
        await reload_scheduler(task_service, scheduler_service)
        await generation_service.complete(job_id, task, f"任务“{req.task_name}”创建完成。")
    except Exception as exc:
        if os.path.exists(output_filename):
            os.remove(output_filename)
        await generation_service.fail(job_id, f"AI 任务生成失败: {exc}")


async def run_criteria_regeneration_job(
    *,
    job_id: str,
    task_id: int,
    task_name: str,
    task_update: TaskUpdate,
    description: str,
    output_filename: str,
    task_service: TaskService,
    scheduler_service: SchedulerService,
    generation_service: TaskGenerationService,
) -> None:
    """后台重新生成 criteria，并在成功后一次性保存任务更新。"""
    try:
        await advance_job(
            generation_service,
            job_id,
            "prepare",
            "已接收更新请求，开始准备分析标准。",
        )

        async def report_progress(
            step_key: str,
            message: str,
            generated_characters: Optional[int] = None,
        ) -> None:
            await advance_job(
                generation_service,
                job_id,
                step_key,
                message,
                generated_characters,
            )

        generated_criteria = await generate_criteria(
            user_description=description,
            reference_file_path="prompts/macbook_criteria.txt",
            progress_callback=report_progress,
            queue_summary=task_name,
            queue_task_id=task_id,
            queue_generation_job_id=job_id,
            queue_generation_mode="regenerate",
        )
        await advance_job(
            generation_service,
            job_id,
            "persist",
            f"正在保存分析标准到 {output_filename}。",
        )
        await save_generated_criteria(output_filename, generated_criteria)

        await advance_job(
            generation_service,
            job_id,
            "task",
            "分析标准已生成，正在更新任务记录。",
        )
        task_update.ai_prompt_criteria_file = output_filename
        task = await task_service.update_task(task_id, task_update)
        await reload_scheduler(task_service, scheduler_service)
        await generation_service.complete(
            job_id,
            task,
            f"任务“{task.task_name}”的 AI 标准已更新。",
        )
    except Exception as exc:
        await generation_service.fail(job_id, f"AI 标准重新生成失败: {exc}")
