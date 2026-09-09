"""Cross-process priority queue for all upstream LLM requests."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from src.infrastructure.config.env_manager import env_manager


LLM_PRIORITY_CRITERIA = 0
LLM_PRIORITY_NORMAL = 100

_POLL_INTERVAL_SECONDS = 0.2
_HEARTBEAT_INTERVAL_SECONDS = 5.0
_STALE_LEASE_SECONDS = 120.0
_SQLITE_BUSY_TIMEOUT_MS = 30_000
_MAX_STREAM_CONTENT_CHARS = 4_000

_DEFAULT_DURATION_SECONDS = {
    "criteria": 300.0,
    "analysis": 120.0,
    "search-params": 30.0,
    "llm": 60.0,
}


def _configured_concurrency() -> int:
    raw = env_manager.get_value("AI_ANALYSIS_CONCURRENCY", "2")
    try:
        return max(1, min(32, int(str(raw).strip())))
    except (TypeError, ValueError):
        return 2


def _queue_database_path() -> str:
    configured = os.getenv("LLM_REQUEST_QUEUE_DB_FILE", "").strip()
    if configured:
        return configured
    app_database = os.getenv("APP_DATABASE_FILE", "data/app.sqlite3")
    app_path = Path(app_database)
    return str(app_path.with_name("llm_request_queue.sqlite3"))


def _linux_process_start_marker(pid: int) -> str:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields_after_name = stat.rsplit(")", 1)[1].split()
        return fields_after_name[19]
    except (IndexError, OSError):
        return ""


def _process_identity(pid: int | None = None) -> str:
    resolved_pid = int(pid or os.getpid())
    marker = _linux_process_start_marker(resolved_pid)
    return f"{resolved_pid}:{marker}"


def _process_is_alive(identity: str) -> bool:
    pid_text, _, expected_marker = str(identity).partition(":")
    try:
        pid = int(pid_text)
        if os.name == "nt":
            import ctypes

            process_query_limited_information = 0x1000
            still_active = 259
            handle = ctypes.windll.kernel32.OpenProcess(
                process_query_limited_information,
                False,
                pid,
            )
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not ctypes.windll.kernel32.GetExitCodeProcess(
                    handle,
                    ctypes.byref(exit_code),
                ):
                    return False
                return exit_code.value == still_active
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        os.kill(pid, 0)
    except (OSError, TypeError, ValueError):
        return False
    if expected_marker:
        actual_marker = _linux_process_start_marker(pid)
        if actual_marker and actual_marker != expected_marker:
            return False
    return True


@dataclass(frozen=True)
class LLMQueueLease:
    queue: "GlobalLLMRequestQueue"
    ticket_id: str

    async def update_content(self, content: str) -> None:
        await asyncio.to_thread(
            self.queue._update_content,
            self.ticket_id,
            str(content or "")[-_MAX_STREAM_CONTENT_CHARS:],
        )


class GlobalLLMRequestQueue:
    """SQLite-backed queue shared by the API process and scraper subprocesses."""

    def __init__(
        self,
        *,
        database_path_provider: Callable[[], str] = _queue_database_path,
        concurrency_provider: Callable[[], int] = _configured_concurrency,
    ) -> None:
        self._database_path_provider = database_path_provider
        self._concurrency_provider = concurrency_provider
        self._owner_identity = _process_identity()
        self._schema_lock = threading.Lock()
        self._initialized_paths: set[str] = set()

    @asynccontextmanager
    async def slot(
        self,
        *,
        priority: int = LLM_PRIORITY_NORMAL,
        label: str = "llm",
        summary: str = "",
        retry_attempt: int | None = None,
        retry_max_attempts: int | None = None,
        retry_error: str = "",
        task_id: int | None = None,
        generation_job_id: str = "",
        generation_mode: str = "",
    ) -> AsyncIterator[LLMQueueLease]:
        ticket_id = uuid.uuid4().hex
        created_at = time.time()
        await asyncio.to_thread(
            self._enqueue,
            ticket_id,
            int(priority),
            str(label or "llm"),
            str(summary or "")[:500],
            created_at,
            retry_attempt,
            retry_max_attempts,
            str(retry_error or "")[:2_000],
            "queued",
            task_id,
            str(generation_job_id or "")[:100],
            str(generation_mode or "")[:30],
        )
        acquired = False
        heartbeat_task: asyncio.Task | None = None
        wait_started = time.monotonic()
        try:
            while not acquired:
                acquired = await asyncio.to_thread(self._try_activate, ticket_id)
                if not acquired:
                    await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            waited = time.monotonic() - wait_started
            print(
                f"[LLM队列] {label} 已获得请求槽位，"
                f"等待 {waited:.2f}s，优先级 {priority}。"
            )
            heartbeat_task = asyncio.create_task(
                self._heartbeat(ticket_id),
                name=f"llm-queue-heartbeat-{ticket_id[:8]}",
            )
            yield LLMQueueLease(self, ticket_id)
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            with contextlib.suppress(Exception):
                await asyncio.shield(asyncio.to_thread(self._release, ticket_id))

    @asynccontextmanager
    async def retrying(
        self,
        *,
        priority: int = LLM_PRIORITY_NORMAL,
        label: str = "llm",
        summary: str = "",
        retry_attempt: int,
        retry_max_attempts: int,
        retry_error: str,
        task_id: int | None = None,
        generation_job_id: str = "",
        generation_mode: str = "",
    ) -> AsyncIterator[None]:
        """Expose a logical request while it waits for its next retry."""
        ticket_id = uuid.uuid4().hex
        created_at = time.time()
        await asyncio.to_thread(
            self._enqueue,
            ticket_id,
            int(priority),
            str(label or "llm"),
            str(summary or "")[:500],
            created_at,
            retry_attempt,
            retry_max_attempts,
            str(retry_error or "")[:2_000],
            "retrying",
            task_id,
            str(generation_job_id or "")[:100],
            str(generation_mode or "")[:30],
        )
        heartbeat_task = asyncio.create_task(
            self._heartbeat(ticket_id),
            name=f"llm-queue-retry-heartbeat-{ticket_id[:8]}",
        )
        try:
            yield
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            with contextlib.suppress(Exception):
                await asyncio.shield(asyncio.to_thread(self._release, ticket_id))

    async def _heartbeat(self, ticket_id: str) -> None:
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
            await asyncio.to_thread(self._touch, ticket_id)

    def _connect(self) -> sqlite3.Connection:
        path = str(Path(self._database_path_provider()).resolve())
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=_SQLITE_BUSY_TIMEOUT_MS / 1000)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
        with self._schema_lock:
            if path not in self._initialized_paths:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS llm_request_queue (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ticket_id TEXT NOT NULL UNIQUE,
                        priority INTEGER NOT NULL,
                        label TEXT NOT NULL,
                        summary TEXT NOT NULL DEFAULT '',
                        state TEXT NOT NULL,
                        owner_identity TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        heartbeat_at REAL NOT NULL,
                        started_at REAL,
                        content TEXT NOT NULL DEFAULT '',
                        retry_attempt INTEGER,
                        retry_max_attempts INTEGER,
                        retry_error TEXT NOT NULL DEFAULT '',
                        task_id INTEGER,
                        generation_job_id TEXT NOT NULL DEFAULT '',
                        generation_mode TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_llm_request_queue_order
                    ON llm_request_queue(state, priority, id)
                    """
                )
                columns = {
                    row[1]
                    for row in conn.execute(
                        "PRAGMA table_info(llm_request_queue)"
                    ).fetchall()
                }
                migrations = {
                    "summary": "TEXT NOT NULL DEFAULT ''",
                    "started_at": "REAL",
                    "content": "TEXT NOT NULL DEFAULT ''",
                    "retry_attempt": "INTEGER",
                    "retry_max_attempts": "INTEGER",
                    "retry_error": "TEXT NOT NULL DEFAULT ''",
                    "task_id": "INTEGER",
                    "generation_job_id": "TEXT NOT NULL DEFAULT ''",
                    "generation_mode": "TEXT NOT NULL DEFAULT ''",
                }
                for column, definition in migrations.items():
                    if column not in columns:
                        conn.execute(
                            f"ALTER TABLE llm_request_queue "
                            f"ADD COLUMN {column} {definition}"
                        )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS llm_request_metrics (
                        label TEXT PRIMARY KEY,
                        completed_count INTEGER NOT NULL,
                        average_duration_seconds REAL NOT NULL
                    )
                    """
                )
                conn.commit()
                self._initialized_paths.add(path)
        return conn

    def _enqueue(
        self,
        ticket_id: str,
        priority: int,
        label: str,
        summary: str,
        created_at: float,
        retry_attempt: int | None = None,
        retry_max_attempts: int | None = None,
        retry_error: str = "",
        state: str = "queued",
        task_id: int | None = None,
        generation_job_id: str = "",
        generation_mode: str = "",
    ) -> None:
        with contextlib.closing(self._connect()) as conn:
            with conn:
                self._cleanup_stale(conn, created_at)
                conn.execute(
                    """
                    INSERT INTO llm_request_queue(
                        ticket_id, priority, label, summary, state, owner_identity,
                        created_at, heartbeat_at, retry_attempt,
                        retry_max_attempts, retry_error, task_id,
                        generation_job_id, generation_mode
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ticket_id,
                        priority,
                        label,
                        summary,
                        state,
                        self._owner_identity,
                        created_at,
                        created_at,
                        retry_attempt,
                        retry_max_attempts,
                        retry_error,
                        task_id,
                        generation_job_id,
                        generation_mode,
                    ),
                )

    def _try_activate(self, ticket_id: str) -> bool:
        now = time.time()
        concurrency = max(1, min(32, int(self._concurrency_provider())))
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._cleanup_stale(conn, now)
            own = conn.execute(
                "SELECT state FROM llm_request_queue WHERE ticket_id = ?",
                (ticket_id,),
            ).fetchone()
            if own is None:
                raise RuntimeError("全局 LLM 请求队列中的当前票据已丢失。")
            conn.execute(
                "UPDATE llm_request_queue SET heartbeat_at = ? WHERE ticket_id = ?",
                (now, ticket_id),
            )
            if own["state"] == "active":
                conn.commit()
                return True

            active_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM llm_request_queue WHERE state = 'active'"
                ).fetchone()[0]
            )
            available = concurrency - active_count
            if available <= 0:
                conn.commit()
                return False
            next_rows = conn.execute(
                """
                SELECT ticket_id
                FROM llm_request_queue
                WHERE state = 'queued'
                ORDER BY priority ASC, id ASC
                LIMIT ?
                """,
                (available,),
            ).fetchall()
            may_start = any(row["ticket_id"] == ticket_id for row in next_rows)
            if may_start:
                conn.execute(
                    """
                    UPDATE llm_request_queue
                    SET state = 'active', heartbeat_at = ?, started_at = ?
                    WHERE ticket_id = ?
                    """,
                    (now, now, ticket_id),
                )
            conn.commit()
            return may_start
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _touch(self, ticket_id: str) -> None:
        with contextlib.closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "UPDATE llm_request_queue SET heartbeat_at = ? WHERE ticket_id = ?",
                    (time.time(), ticket_id),
                )

    def _release(self, ticket_id: str) -> None:
        with contextlib.closing(self._connect()) as conn:
            with conn:
                row = conn.execute(
                    """
                    SELECT label, state, started_at
                    FROM llm_request_queue
                    WHERE ticket_id = ?
                    """,
                    (ticket_id,),
                ).fetchone()
                if row and row["state"] == "active" and row["started_at"]:
                    duration = max(0.01, time.time() - float(row["started_at"]))
                    conn.execute(
                        """
                        INSERT INTO llm_request_metrics(
                            label, completed_count, average_duration_seconds
                        ) VALUES (?, 1, ?)
                        ON CONFLICT(label) DO UPDATE SET
                            completed_count = completed_count + 1,
                            average_duration_seconds =
                                average_duration_seconds * 0.8 + excluded.average_duration_seconds * 0.2
                        """,
                        (row["label"], min(duration, 3_600.0)),
                    )
                conn.execute(
                    "DELETE FROM llm_request_queue WHERE ticket_id = ?",
                    (ticket_id,),
                )

    def _update_content(self, ticket_id: str, content: str) -> None:
        with contextlib.closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE llm_request_queue
                    SET content = ?, heartbeat_at = ?
                    WHERE ticket_id = ? AND state = 'active'
                    """,
                    (content[-_MAX_STREAM_CONTENT_CHARS:], time.time(), ticket_id),
                )

    async def snapshot(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._snapshot)

    def _snapshot(self) -> dict[str, Any]:
        now = time.time()
        concurrency = max(1, min(32, int(self._concurrency_provider())))
        with contextlib.closing(self._connect()) as conn:
            with conn:
                self._cleanup_stale(conn, now)
            rows = conn.execute(
                """
                SELECT id, ticket_id, priority, label, summary, state,
                       created_at, started_at, content, retry_attempt,
                       retry_max_attempts, retry_error, task_id,
                       generation_job_id, generation_mode
                FROM llm_request_queue
                ORDER BY
                    CASE WHEN state = 'active' THEN 0 ELSE 1 END,
                    priority ASC,
                    id ASC
                """
            ).fetchall()
            metrics = {
                row["label"]: float(row["average_duration_seconds"])
                for row in conn.execute(
                    "SELECT label, average_duration_seconds FROM llm_request_metrics"
                ).fetchall()
            }

        def expected_duration(label: str) -> float:
            return max(
                1.0,
                metrics.get(label, _DEFAULT_DURATION_SECONDS.get(label, 60.0)),
            )

        active_rows = [row for row in rows if row["state"] == "active"]
        retrying_rows = [row for row in rows if row["state"] == "retrying"]
        queued_rows = sorted(
            (row for row in rows if row["state"] == "queued"),
            key=lambda row: (int(row["priority"]), int(row["id"])),
        )
        worker_available = [
            max(
                0.0,
                expected_duration(row["label"])
                - max(0.0, now - float(row["started_at"] or now)),
            )
            for row in active_rows
        ]
        worker_available.extend(
            [0.0] * max(0, concurrency - len(worker_available))
        )
        if not worker_available:
            worker_available = [0.0]

        waiting_estimates: dict[str, float] = {}
        for row in queued_rows:
            worker_index = min(
                range(len(worker_available)),
                key=worker_available.__getitem__,
            )
            wait_seconds = worker_available[worker_index]
            waiting_estimates[row["ticket_id"]] = wait_seconds
            worker_available[worker_index] = (
                wait_seconds + expected_duration(row["label"])
            )

        items = []
        waiting_position = 0
        for row in rows:
            is_waiting = row["state"] == "queued"
            is_retrying = row["state"] == "retrying"
            if is_waiting:
                waiting_position += 1
            items.append(
                {
                    "ticket_id": row["ticket_id"],
                    "kind": row["label"],
                    "status": (
                        "waiting" if is_waiting
                        else "retrying" if is_retrying
                        else "running"
                    ),
                    "summary": row["summary"],
                    "stream_content": row["content"] if not is_waiting else "",
                    "created_at": float(row["created_at"]),
                    "started_at": (
                        float(row["started_at"])
                        if row["started_at"] is not None
                        else None
                    ),
                    "waiting_position": waiting_position if is_waiting else None,
                    "estimated_wait_seconds": (
                        waiting_estimates.get(row["ticket_id"], 0.0)
                        if is_waiting
                        else None
                    ),
                    "retry_attempt": row["retry_attempt"],
                    "retry_max_attempts": row["retry_max_attempts"],
                    "retry_error": row["retry_error"],
                    "task_id": row["task_id"],
                    "generation_job_id": row["generation_job_id"],
                    "generation_mode": row["generation_mode"],
                }
            )
        return {
            "concurrency": concurrency,
            "active_count": len(active_rows),
            "retrying_count": len(retrying_rows),
            "waiting_count": len(queued_rows),
            "items": items,
        }

    @staticmethod
    def _cleanup_stale(conn: sqlite3.Connection, now: float) -> None:
        rows = conn.execute(
            "SELECT ticket_id, owner_identity, heartbeat_at FROM llm_request_queue"
        ).fetchall()
        stale_ids = [
            row["ticket_id"]
            for row in rows
            if now - float(row["heartbeat_at"]) > _STALE_LEASE_SECONDS
            or not _process_is_alive(row["owner_identity"])
        ]
        if stale_ids:
            conn.executemany(
                "DELETE FROM llm_request_queue WHERE ticket_id = ?",
                ((ticket_id,) for ticket_id in stale_ids),
            )


global_llm_request_queue = GlobalLLMRequestQueue()
