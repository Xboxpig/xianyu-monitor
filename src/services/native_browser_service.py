"""Task runtime driven by a paired, ordinary Chrome extension."""
import asyncio
import contextlib
import inspect
import secrets
import os
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from src.domain.models.browser import BrowserClient, BrowserItem, BrowserReport
from src.infrastructure.config.settings import AISettings
from src.infrastructure.persistence.sqlite_task_repository import SqliteTaskRepository
from src.services.native_result_processor import NativeResultProcessor
from src.services.process_service import TaskStartError
from src.services.result_storage_service import load_processed_link_keys
from src.utils import build_task_log_path

CLIENT_TIMEOUT = 90
COMMAND_TIMEOUT = 180


@dataclass
class NativeRun:
    task: object
    client_id: str
    prompt: str
    run_id: str = field(default_factory=lambda: secrets.token_hex(16))
    returncode: int | None = None
    command: dict | None = None
    issued_at: float = 0
    waiting: str = ""
    page: int = 1
    has_more: bool = False
    personal_applied: bool = False
    region_applied: bool = False
    collecting: bool = True
    items: deque = field(default_factory=deque)
    market: list = field(default_factory=list)
    current_item: BrowserItem | None = None
    seen: set = field(default_factory=set)
    receipts: set = field(default_factory=set)
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=32))
    workers: list = field(default_factory=list)
    finishing: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    finished: asyncio.Event = field(default_factory=asyncio.Event)


class NativeBrowserService:
    backend = "native_chrome"

    def __init__(self, *, repository=None, processor=None, token_path="state/native-browser-token"):
        self.repository = repository or SqliteTaskRepository()
        self.processor = processor or NativeResultProcessor()
        self.token_path = Path(token_path)
        self.processes: dict[int, NativeRun] = {}
        self.clients: dict[str, dict] = {}
        self.history: deque = deque(maxlen=100)
        self._on_started = None
        self._on_stopped = None
        self._watchdog = None
        self._rotation = 0
        self._token_lock = threading.Lock()
        self._token = None
        self._inflight = {}

    def token(self):
        with self._token_lock:
            if self._token is not None:
                return self._token
            if self.token_path.exists():
                token = self.token_path.read_text(encoding="utf-8").strip()
                if len(token) < 32:
                    raise RuntimeError("Invalid native browser token file.")
            else:
                self.token_path.parent.mkdir(parents=True, exist_ok=True)
                token = secrets.token_urlsafe(32)
                fd, temporary = tempfile.mkstemp(dir=self.token_path.parent)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as source:
                        source.write(token)
                    os.chmod(temporary, 0o600)
                    os.replace(temporary, self.token_path)
                finally:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(temporary)
            self._token = token
            return token

    def set_lifecycle_hooks(self, *, on_started=None, on_stopped=None):
        self._on_started, self._on_stopped = on_started, on_stopped

    async def _hook(self, hook, task_id):
        if hook:
            result = hook(task_id)
            if inspect.isawaitable(result):
                await result

    def is_running(self, task_id):
        return task_id in self.processes

    def status(self):
        now = time.monotonic()
        return {
            "backend": self.backend, "connected": any(now - c["last_seen"] < CLIENT_TIMEOUT for c in self.clients.values()),
            "clients": [{**c["info"], "online": now - c["last_seen"] < CLIENT_TIMEOUT}
                        for c in self.clients.values()],
            "runs": [{"task_id": r.task.id, "task_name": r.task.task_name,
                      "run_id": r.run_id, "client_id": r.client_id,
                      "state": "attention" if r.waiting else ("collecting" if r.collecting else "analyzing"),
                      "message": r.waiting, "page": r.page, "pending_analysis": r.queue.qsize()}
                     for r in self.processes.values()],
        }

    def _log(self, run, message):
        path = Path(build_task_log_path(run.task.id, run.task.task_name))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as source:
            source.write("[" + datetime.now().isoformat(timespec="seconds") + "] " + message + "\n")

    def _issue(self, run, action, url=""):
        run.command = {
            "run_id": run.run_id, "command_id": secrets.token_hex(16),
            "action": action, "url": url, "page": run.page,
            "task": run.task.model_dump(exclude={"ai_prompt_base_file", "ai_prompt_criteria_file", "description"}),
        }
        run.issued_at = 0

    def _search_url(self, run):
        return "https://www.goofish.com/search?" + urlencode({"q": run.task.keyword})

    async def start_task(self, task_id, task_name, *, raise_on_failure=False):
        try:
            if self.is_running(task_id):
                raise TaskStartError("TASK_ALREADY_RUNNING", "任务已在运行中。", 409)
            task = await self.repository.find_by_id(task_id)
            if not task:
                raise TaskStartError("TASK_NOT_FOUND", "任务未找到。", 404)
            online = [
                c["info"] for c in self.clients.values()
                if time.monotonic() - c["last_seen"] < CLIENT_TIMEOUT
            ]
            if task.account_strategy == "fixed":
                online = [c for c in online if c.get("account_state_file") == task.account_state_file]
            if not online:
                raise TaskStartError("CHROME_EXTENSION_OFFLINE", "请启动 Chrome 并连接采集扩展；固定账号任务需要扩展绑定对应账号。", 409)
            client = online[self._rotation % len(online)] if task.account_strategy == "rotate" else online[0]
            self._rotation += 1
            # Reserve before awaiting disk work, so concurrent starts cannot duplicate a run.
            run = NativeRun(task, client["client_id"], "")
            self.processes[task_id] = run
            try:
                run.prompt = self.processor.load_prompt(task)
                run.seen = await asyncio.to_thread(load_processed_link_keys, task.keyword)
                self._issue(run, "search", self._search_url(run))
                await self._hook(self._on_started, task_id)
            except BaseException:
                self.processes.pop(task_id, None)
                raise
            run.workers = [asyncio.create_task(self._worker(run)) for _ in range(AISettings().analysis_concurrency)]
            if self._watchdog is None:
                self._watchdog = asyncio.create_task(self._watch_connections())
            self._log(run, "[Chrome] 任务已排队，使用浏览器：" + client["name"])
            return True
        except TaskStartError:
            if raise_on_failure:
                raise
            return False
        except Exception as exc:
            if raise_on_failure:
                raise TaskStartError("NATIVE_TASK_START_FAILED", str(exc), 500) from exc
            return False

    async def poll(self, client: BrowserClient):
        self.clients[client.client_id] = {"info": client.model_dump(), "last_seen": time.monotonic()}
        # One run per Chrome profile at a time; other tasks remain queued.
        run = next((r for r in self.processes.values() if r.client_id == client.client_id and r.collecting and r.returncode is None), None)
        if run is None:
            return {"command": None, "attention": None}
        if run.waiting:
            return {"command": None, "attention": {"run_id": run.run_id, "message": run.waiting}}
        if run.queue.full():
            return {"command": None, "attention": None, "active_run_id": run.run_id}
        if run.command and not run.issued_at:
            run.issued_at = time.monotonic()
        return {"command": run.command, "attention": None}

    async def report(self, report: BrowserReport):
        run = next((r for r in self.processes.values() if r.run_id == report.run_id), None)
        if run is None:
            if (report.run_id, report.command_id, report.client_id) in self.history:
                return {"accepted": True}
            raise ValueError("Task was stopped or the server restarted.")
        async with run.lock:
            if self.processes.get(run.task.id) is not run:
                raise ValueError("Task was stopped.")
            if run.client_id != report.client_id:
                raise PermissionError("This run belongs to another Chrome profile.")
            if report.command_id in run.receipts:
                return {"accepted": True}
            if not run.command or run.command["command_id"] != report.command_id:
                raise ValueError("Stale command.")
            if report.outcome == "attention":
                run.waiting = report.message or "请在 Chrome 中完成登录或验证，然后点击扩展中的继续。"
                self._log(run, "[Chrome] 等待人工处理：" + run.waiting)
                return {"accepted": True}
            if report.outcome == "error":
                self._log(run, "[Chrome] 采集失败：" + report.message)
                await self._finish(run, failed=True)
                self.history.append((report.run_id, report.command_id, report.client_id))
                return {"accepted": True}
            action = run.command["action"]
            if action in ("search", "search_next"):
                run.personal_applied = report.personal_applied
                run.region_applied = report.region_applied
                candidates = report.items
                if report.personal_applied:
                    candidates = [i.model_copy(update={"personal": True}) for i in candidates]
                kept, reasons = self.processor.filter(run.task, candidates, report.region_applied, check_personal=False)
                for reason in reasons:
                    self._log(run, "[硬过滤] " + reason)
                run.market = kept
                await self.processor.record_market(run.task, kept, run.run_id)
                run.items = deque(i for i in kept if "https://www.goofish.com/item?id=" + i.id not in run.seen)
                run.has_more = report.has_more
                self._log(run, f"[Chrome] 第 {run.page} 页：{len(candidates)} 件，待采集详情 {len(run.items)} 件")
            elif action == "detail":
                if not report.item or report.item.id != run.current_item.id:
                    raise ValueError("Item ID does not match the requested detail page.")
                # Keep search-only fields when the detail response does not expose them.
                update = {k: v for k, v in report.item.model_dump().items() if v not in ("", [], None)}
                run.current_item = run.current_item.model_copy(update=update)
                kept, reasons = self.processor.filter(run.task, [run.current_item], run.region_applied)
                if not kept:
                    self._log(run, "[硬过滤] " + "；".join(reasons))
                    run.current_item = None
                elif run.current_item.seller_id:
                    run.receipts.add(report.command_id)
                    self._issue(run, "seller", "https://www.goofish.com/personal?" + urlencode({"userId": run.current_item.seller_id}))
                    return {"accepted": True}
                else:
                    await self._submit(run, {})
            elif action == "seller":
                await self._submit(run, report.seller)
            run.receipts.add(report.command_id)
            self.history.append((report.run_id, report.command_id, report.client_id))
            await self._advance(run)
            return {"accepted": True}

    async def _submit(self, run, seller):
        item = run.current_item
        link = "https://www.goofish.com/item?id=" + item.id
        key = (run.task.keyword, link)
        if key not in self._inflight:
            self._inflight[key] = run.run_id
            queued = False
            try:
                stored = await asyncio.to_thread(load_processed_link_keys, run.task.keyword)
                if link not in stored:
                    await run.queue.put((item, seller, list(run.market)))
                    queued = True
            finally:
                if not queued:
                    self._inflight.pop(key, None)
        run.seen.add(link)
        run.current_item = None

    async def _advance(self, run):
        while run.items:
            item = run.items.popleft()
            if "https://www.goofish.com/item?id=" + item.id in run.seen:
                continue
            run.current_item = item
            self._issue(run, "detail", "https://www.goofish.com/item?id=" + item.id)
            return
        if run.has_more and run.page < run.task.max_pages:
            run.page += 1
            self._issue(run, "search_next", self._search_url(run))
        else:
            run.collecting = False
            run.command = None
            run.finishing = asyncio.create_task(self._complete_when_analyzed(run))

    async def _worker(self, run):
        while True:
            item, seller, market = await run.queue.get()
            try:
                await self.processor.process(run.task, item, seller, run.prompt, market,
                                             lambda message: self._log(run, message))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log(run, "[处理失败] " + str(exc))
            finally:
                self._inflight.pop((run.task.keyword, "https://www.goofish.com/item?id=" + item.id), None)
                run.queue.task_done()

    async def _complete_when_analyzed(self, run):
        await run.queue.join()
        await self._finish(run)

    async def _finish(self, run, *, failed=False):
        if self.processes.get(run.task.id) is not run:
            return
        if run.returncode is not None:
            await run.finished.wait()
            return
        run.returncode = 1 if failed else 0
        current = asyncio.current_task()
        jobs = [job for job in [*run.workers, run.finishing] if job and job is not current]
        for job in jobs:
            job.cancel()
        try:
            await asyncio.gather(*jobs, return_exceptions=True)
            self._log(run, "[Chrome] 任务结束" if not failed else "[Chrome] 任务已停止")
            await self._hook(self._on_stopped, run.task.id)
        finally:
            for key, owner in tuple(self._inflight.items()):
                if owner == run.run_id:
                    self._inflight.pop(key, None)
            self.processes.pop(run.task.id, None)
            run.finished.set()

    async def stop_task(self, task_id):
        run = self.processes.get(task_id)
        if not run:
            return False
        async with run.lock:
            await self._finish(run, failed=True)
        return True

    async def resume(self, client_id, run_id):
        run = next((r for r in self.processes.values() if r.run_id == run_id), None)
        if run is None:
            raise ValueError("任务已经结束。")
        async with run.lock:
            if run.client_id != client_id:
                raise PermissionError("This run belongs to another Chrome profile.")
            if run.waiting and run.command:
                old = run.command
                self._issue(run, old["action"], old["url"])
                run.command["resume"] = True
                run.waiting = ""
                self._log(run, "[Chrome] 用户继续采集")
        return {"resumed": True}

    async def _watch_connections(self):
        while True:
            await asyncio.sleep(10)
            now = time.monotonic()
            for run in tuple(self.processes.values()):
                async with run.lock:
                    if (self.processes.get(run.task.id) is not run or run.returncode is not None
                            or not run.collecting or run.waiting or not run.command):
                        continue
                    client = self.clients.get(run.client_id)
                    if not client or now - client["last_seen"] > CLIENT_TIMEOUT:
                        run.waiting = "Chrome 扩展已断开。请重新打开 Chrome，并点击扩展中的继续。"
                    elif run.issued_at and now - run.issued_at > COMMAND_TIMEOUT:
                        run.waiting = "页面采集超时。请检查 Chrome 中的页面，然后点击继续。"
                    if run.waiting:
                        self._log(run, "[Chrome] " + run.waiting)

    async def stop_all(self):
        for task_id in tuple(self.processes):
            await self.stop_task(task_id)
        if self._watchdog:
            self._watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watchdog
            self._watchdog = None
