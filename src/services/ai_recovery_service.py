"""Background re-analysis of failed or missing AI result records."""

from __future__ import annotations

import asyncio
import contextlib
import os
from datetime import datetime
from typing import Any

from src.ai_handler import download_all_images, get_ai_analysis
from src.domain.models.task import Task
from src.infrastructure.config.settings import AISettings
from src.services.ai_retry_control import (
    read_ai_retry_status,
    request_ai_retry_cancel,
)
from src.services.result_storage_service import (
    load_ai_recovery_records,
    update_result_ai_analysis,
)
from src.utils import resolve_task_log_path


class AIRecoveryService:
    def __init__(self) -> None:
        self._jobs: dict[int, asyncio.Task] = {}
        self._progress: dict[int, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def start(self, task_id: int, task: Task) -> dict[str, Any]:
        if task.decision_mode != "ai":
            raise ValueError("当前任务不是 AI 判断模式。")
        prompt_text = self._load_prompt(task)
        records = await load_ai_recovery_records(task.task_name)
        async with self._lock:
            current = self._jobs.get(task_id)
            if current is not None and not current.done():
                return self.status(task_id)
            self._progress[task_id] = {
                "mode": "recovery",
                "total": len(records),
                "completed": 0,
                "failed": 0,
                "cancelled": False,
            }
            if not records:
                return self.status(task_id)
            job = asyncio.create_task(
                self._run(task_id, task, prompt_text, records),
                name=f"ai-recovery-{task_id}",
            )
            self._jobs[task_id] = job
        self._append_task_log(
            task_id,
            task.task_name,
            f"[AI补分析] 已启动，共 {len(records)} 个失败、异常或未分析商品。",
        )
        return self.status(task_id)

    async def cancel(self, task_id: int, task_name: str) -> dict[str, Any]:
        request_ai_retry_cancel(task_id)
        job = self._jobs.get(task_id)
        if job is not None and not job.done():
            job.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await job
        progress = self._progress.get(task_id)
        if progress is not None:
            progress["cancelled"] = True
        self._append_task_log(
            task_id,
            task_name,
            "[AI补分析] 用户已停止当前 AI 分析与后续重试。",
        )
        return self.status(task_id)

    def status(self, task_id: int) -> dict[str, Any]:
        retry_status = read_ai_retry_status(task_id)
        job = self._jobs.get(task_id)
        job_running = job is not None and not job.done()
        progress = dict(self._progress.get(task_id) or {})
        return {
            **retry_status,
            "active": bool(retry_status["active"] or job_running),
            "mode": "recovery" if job_running else ("live" if retry_status["active"] else None),
            "progress": progress,
        }

    async def stop_all(self) -> None:
        for task_id, job in tuple(self._jobs.items()):
            if job.done():
                continue
            request_ai_retry_cancel(task_id)
            job.cancel()
        await asyncio.gather(*self._jobs.values(), return_exceptions=True)

    async def _run(
        self,
        task_id: int,
        task: Task,
        prompt_text: str,
        records: list[dict],
    ) -> None:
        queue: asyncio.Queue[dict] = asyncio.Queue()
        for record in records:
            queue.put_nowait(record)
        concurrency = min(AISettings().analysis_concurrency, len(records))
        workers = [
            asyncio.create_task(
                self._worker(task_id, task, prompt_text, queue),
                name=f"ai-recovery-{task_id}-worker-{index + 1}",
            )
            for index in range(max(1, concurrency))
        ]
        try:
            await queue.join()
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
        progress = self._progress[task_id]
        self._append_task_log(
            task_id,
            task.task_name,
            "[AI补分析] 已结束："
            f"成功 {progress['completed']}，失败 {progress['failed']}，"
            f"总计 {progress['total']}。",
        )

    async def _worker(
        self,
        task_id: int,
        task: Task,
        prompt_text: str,
        queue: asyncio.Queue[dict],
    ) -> None:
        while True:
            entry = await queue.get()
            try:
                await self._reanalyze_record(task_id, task, prompt_text, entry)
                self._progress[task_id]["completed"] += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._progress[task_id]["failed"] += 1
                self._append_task_log(
                    task_id,
                    task.task_name,
                    f"[AI补分析] 商品处理失败: {exc}",
                )
            finally:
                queue.task_done()

    async def _reanalyze_record(
        self,
        task_id: int,
        task: Task,
        prompt_text: str,
        entry: dict,
    ) -> None:
        record = entry["record"]
        item = record.get("商品信息", {}) or {}
        image_paths: list[str] = []
        try:
            if task.analyze_images:
                image_paths = await download_all_images(
                    str(item.get("商品ID") or entry["row_id"]),
                    list(item.get("商品图片列表") or []),
                    task.task_name,
                )
            analysis = await get_ai_analysis(
                record,
                image_paths=image_paths,
                prompt_text=prompt_text,
                task_id=task_id,
            )
            if not analysis:
                raise RuntimeError("AI analysis returned no result.")
            analysis.setdefault("analysis_source", "ai")
            analysis.setdefault("keyword_hit_count", 0)
            if not await update_result_ai_analysis(entry["row_id"], analysis):
                raise RuntimeError("结果记录已不存在。")
        finally:
            for path in image_paths:
                with contextlib.suppress(OSError):
                    if os.path.exists(path):
                        os.remove(path)

    @staticmethod
    def _load_prompt(task: Task) -> str:
        try:
            with open(task.ai_prompt_base_file, "r", encoding="utf-8") as base_file:
                base_prompt = base_file.read()
            with open(task.ai_prompt_criteria_file, "r", encoding="utf-8") as criteria_file:
                criteria = criteria_file.read()
        except OSError as exc:
            raise ValueError(f"无法读取任务 Prompt: {exc}") from exc
        prompt = base_prompt.replace("{{CRITERIA_SECTION}}", criteria)
        if not prompt.strip():
            raise ValueError("任务 Prompt 为空。")
        return prompt

    @staticmethod
    def _append_task_log(task_id: int, task_name: str, message: str) -> None:
        try:
            path = resolve_task_log_path(task_id, task_name)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            timestamp = datetime.now().strftime(" %Y-%m-%d %H:%M:%S")
            with open(path, "a", encoding="utf-8") as log_file:
                log_file.write(f"[{timestamp}] {message}\n")
        except OSError:
            pass


ai_recovery_service = AIRecoveryService()
