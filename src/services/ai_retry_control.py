"""Cross-process state and cancellation for task-scoped AI analysis retries."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, TypeVar


DEFAULT_CONTROL_DIR = "data/ai_retry_control"
CONTROL_POLL_SECONDS = 0.25
STALE_STATUS_SECONDS = 6 * 60 * 60
_T = TypeVar("_T")


class AIAnalysisRetryCancelled(RuntimeError):
    """Raised when the user stops task-scoped AI retries from the UI."""


def _control_root() -> Path:
    return Path(os.getenv("AI_RETRY_CONTROL_DIR", DEFAULT_CONTROL_DIR))


def _task_dir(task_id: int) -> Path:
    return _control_root() / f"task-{int(task_id)}"


def _cancel_path(task_id: int) -> Path:
    return _task_dir(task_id) / "cancel.token"


def _read_cancel_token(task_id: int) -> str:
    try:
        return _cancel_path(task_id).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def request_ai_retry_cancel(task_id: int) -> str:
    """Publish a new cancellation generation for analyses already in flight."""
    token = f"{time.time_ns()}-{uuid.uuid4().hex}"
    path = _cancel_path(task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    return token


def _pid_is_alive(pid: Any) -> bool:
    try:
        resolved_pid = int(pid)
    except (OSError, TypeError, ValueError):
        return False

    if os.name == "nt":
        # Unlike POSIX, os.kill(pid, 0) calls TerminateProcess on Windows and
        # can kill the process whose liveness we are trying to inspect.
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            resolved_pid,
        )
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(resolved_pid, 0)
        return True
    except OSError:
        return False


def read_ai_retry_status(task_id: int) -> dict[str, Any]:
    """Aggregate active analysis status files for one crawler task."""
    active_dir = _task_dir(task_id) / "active"
    statuses: list[dict[str, Any]] = []
    now = time.time()
    try:
        paths = list(active_dir.glob("*.json"))
    except OSError:
        paths = []

    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            updated_at = float(payload.get("updated_at") or 0)
            if (
                not isinstance(payload, dict)
                or now - updated_at > STALE_STATUS_SECONDS
                or not _pid_is_alive(payload.get("pid"))
            ):
                path.unlink(missing_ok=True)
                continue
            statuses.append(payload)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue

    statuses.sort(key=lambda item: float(item.get("updated_at") or 0), reverse=True)
    latest = statuses[0] if statuses else {}
    return {
        "active": bool(statuses),
        "active_count": len(statuses),
        "phase": latest.get("phase"),
        "attempt": latest.get("attempt"),
        "max_attempts": latest.get("max_attempts"),
        "next_delay_seconds": latest.get("next_delay_seconds"),
    }


class AIAnalysisRetryControl:
    """Register one analysis and make its request/backoff waits cancellable."""

    def __init__(
        self,
        task_id: int | None,
        *,
        product_id: str,
        max_attempts: int,
    ) -> None:
        self.task_id = int(task_id) if task_id is not None else None
        self.product_id = str(product_id)
        self.max_attempts = int(max_attempts)
        self._baseline_token = ""
        self._status_path: Path | None = None
        if self.task_id is not None:
            self._baseline_token = _read_cancel_token(self.task_id)
            active_dir = _task_dir(self.task_id) / "active"
            active_dir.mkdir(parents=True, exist_ok=True)
            self._status_path = active_dir / f"{os.getpid()}-{uuid.uuid4().hex}.json"
            self.update(phase="request", attempt=1)

    @classmethod
    def from_runtime(
        cls,
        *,
        product_id: str,
        max_attempts: int,
        task_id: int | None = None,
    ) -> "AIAnalysisRetryControl":
        resolved_task_id = task_id
        if resolved_task_id is None:
            raw_task_id = os.getenv("XIANYU_TASK_ID", "").strip()
            if raw_task_id.isdigit():
                resolved_task_id = int(raw_task_id)
        return cls(
            resolved_task_id,
            product_id=product_id,
            max_attempts=max_attempts,
        )

    @property
    def enabled(self) -> bool:
        return self.task_id is not None and self._status_path is not None

    def update(
        self,
        *,
        phase: str,
        attempt: int,
        next_delay_seconds: int | None = None,
    ) -> None:
        if self._status_path is None or self.task_id is None:
            return
        payload = {
            "task_id": self.task_id,
            "product_id": self.product_id,
            "pid": os.getpid(),
            "phase": phase,
            "attempt": int(attempt),
            "max_attempts": self.max_attempts,
            "next_delay_seconds": next_delay_seconds,
            "updated_at": time.time(),
        }
        self._status_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

    def is_cancelled(self) -> bool:
        return bool(
            self.task_id is not None
            and _read_cancel_token(self.task_id) != self._baseline_token
        )

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise AIAnalysisRetryCancelled("用户已停止当前任务的 AI 分析重试。")

    async def wait(self, delay_seconds: float) -> None:
        if not self.enabled:
            await asyncio.sleep(delay_seconds)
            return
        deadline = asyncio.get_running_loop().time() + max(0.0, delay_seconds)
        while True:
            self.raise_if_cancelled()
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(CONTROL_POLL_SECONDS, remaining))

    async def run(self, awaitable: Awaitable[_T]) -> _T:
        if not self.enabled:
            return await awaitable
        operation = asyncio.ensure_future(awaitable)
        try:
            while not operation.done():
                self.raise_if_cancelled()
                done, _ = await asyncio.wait(
                    {operation},
                    timeout=CONTROL_POLL_SECONDS,
                )
                if done:
                    break
            self.raise_if_cancelled()
            return await operation
        except BaseException:
            if not operation.done():
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
            raise

    def close(self) -> None:
        if self._status_path is None:
            return
        try:
            self._status_path.unlink(missing_ok=True)
        except OSError:
            pass
        self._status_path = None
