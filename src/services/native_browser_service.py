"""Task runtime driven by a paired, ordinary Chrome extension."""
import asyncio
import contextlib
import inspect
import random
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
from src.infrastructure.config.settings import AISettings, ScraperSettings
from src.infrastructure.persistence.sqlite_task_repository import SqliteTaskRepository
from src.services.native_result_processor import NativeResultProcessor
from src.services.process_service import TaskStartError
from src.services.result_storage_service import load_processed_link_keys
from src.utils import build_task_log_path
from src.services.windows_desktop_service import windows_desktop_service

CLIENT_TIMEOUT = 90
COMMAND_TIMEOUT = 180
WINDOW_RESERVATION_MB = 256


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
    connection_waiting: str = ""
    page: int = 1
    has_more: bool = False
    personal_applied: bool = False
    region_applied: bool = False
    collecting: bool = True
    items: deque = field(default_factory=deque)
    market: list = field(default_factory=list)
    commands: dict = field(default_factory=dict)
    search_done: bool = False
    discovered: set = field(default_factory=set)
    memory: dict = field(default_factory=dict)
    seen: set = field(default_factory=set)
    receipts: set = field(default_factory=set)
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=32))
    workers: list = field(default_factory=list)
    finishing: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    finished: asyncio.Event = field(default_factory=asyncio.Event)


class NativeBrowserService:
    backend = "native_chrome"

    def __init__(self, *, repository=None, processor=None, token_path="state/native-browser-token", clock=None, delay_sampler=None):
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
        self._clock = clock or time.monotonic
        self._delay_sampler = delay_sampler or random.uniform

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
        now = self._clock()
        return {
            "backend": self.backend, "connected": any(now - c["last_seen"] < CLIENT_TIMEOUT for c in self.clients.values()),
            "clients": [{**c["info"], "online": now - c["last_seen"] < CLIENT_TIMEOUT}
                        for c in self.clients.values()],
            "runs": [{"task_id": r.task.id, "task_name": r.task.task_name,
                      "run_id": r.run_id, "client_id": r.client_id,
                      "state": "attention" if r.waiting or self._offline_message(r, now) else ("collecting" if r.collecting else "analyzing"),
                      "message": self._offline_message(r, now) or r.waiting or self._delay_message(r, now), "page": r.page, "pending_analysis": r.queue.qsize(),
                      "active_windows": len(r.memory), "memory_mb": sum(r.memory.values()),
                      "memory_limit_mb": ScraperSettings().task_memory_mb}
                     for r in self.processes.values()],
        }

    def _offline_message(self, run, now):
        if not run.collecting:
            return ""
        client = self.clients.get(run.client_id)
        if client and now - client["last_seen"] < CLIENT_TIMEOUT:
            return ""
        info = client["info"] if client else {}
        profile = info.get("profile_directory") or run.task.account_state_file or run.client_id
        return f"Chrome 账户 {profile} 的采集扩展已断开。请打开该账户的 Chrome，连接恢复后将自动继续。"

    def _log(self, run, message):
        path = Path(build_task_log_path(run.task.id, run.task.task_name))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as source:
            source.write("[" + datetime.now().isoformat(timespec="seconds") + "] " + message + "\n")

    def _delay_message(self, run, now):
        pending = next((e for e in run.commands.values() if not e['issued_at'] and e['not_before'] > now), None)
        if not pending:
            return ''
        seconds = max(1, int(pending['not_before'] - now + 0.999))
        target = '商品详情' if pending['command']['action'] == 'detail' else '卖家个人主页'
        return f'随机等待中：{seconds} 秒后打开{target}。'

    def _issue(self, run, action, url="", *, item=None, market=None, region=None, slot_id=None, delay=0):
        command = {
            "run_id": run.run_id, "command_id": secrets.token_hex(16),
            "action": action, "url": url, "page": run.page,
            "slot_id": slot_id or ("search" if action.startswith("search") else secrets.token_hex(16)),
            "task": run.task.model_dump(exclude={"ai_prompt_base_file", "ai_prompt_criteria_file", "description"}),
        }
        run.commands[command["command_id"]] = {
            "command": command, "item": item, "market": market or [],
            "region": region, "issued_at": 0, "waiting": "", "not_before": self._clock() + delay,
        }
        if delay:
            target = '商品详情' if action == 'detail' else '卖家个人主页'
            self._log(run, f'[Chrome] 商品 {item.id}：等待 {delay:.1f} 秒后打开{target}')
        run.command = command
        return command

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
                if self._clock() - c["last_seen"] < CLIENT_TIMEOUT
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
            if self._watchdog is None or self._watchdog.done():
                self._watchdog = asyncio.create_task(self._watch_connections())
            self._log(run, "[Chrome] 任务已排队，使用浏览器：" + client["name"])
            self._log(run, f"[Chrome] 搜索页数上限：{task.max_pages} 页；扩展版本：{client.get('version', 'unknown')}")
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
        self.clients[client.client_id] = {"info": client.model_dump(exclude={"windows"}), "last_seen": self._clock()}
        run = next((r for r in self.processes.values() if r.client_id == client.client_id and r.collecting and r.returncode is None), None)
        if run is None:
            return {"command": None, "commands": [], "attention": None}
        async with run.lock:
            run.connection_waiting = ""
            budget = ScraperSettings().task_memory_mb
            if client.parallel:
                # Stable Chrome exposes heap only. Each owned window reserves native/DOM/image
                # overhead too; this is admission accounting, not an OS process memory limit.
                run.memory = {w.slot_id: max(WINDOW_RESERVATION_MB, w.memory_mb)
                              for w in client.windows if w.run_id == run.run_id}
            reserved = dict(run.memory)
            for entry in run.commands.values():
                if entry["issued_at"]:
                    reserved.setdefault(entry["command"]["slot_id"], WINDOW_RESERVATION_MB)
            dispatched = [e for e in run.commands.values() if e["issued_at"]]
            def admit_pending():
                for entry in sorted(run.commands.values(), key=lambda e: e["command"]["action"].startswith("search_next")):
                    if entry["issued_at"] or entry["waiting"] or run.queue.full() or self._clock() < entry['not_before']:
                        continue
                    if not client.parallel and dispatched:
                        continue
                    slot = entry["command"]["slot_id"]
                    if client.parallel and slot not in reserved and sum(reserved.values()) + WINDOW_RESERVATION_MB > budget:
                        continue
                    entry["issued_at"] = self._clock()
                    reserved.setdefault(slot, WINDOW_RESERVATION_MB)
                    dispatched.append(entry)
                    if entry['command']['action'] in ('detail', 'seller'):
                        target = '商品详情' if entry['command']['action'] == 'detail' else '卖家个人主页'
                        self._log(run, f'[Chrome] 商品 {entry["item"].id}：开始打开{target}')
            admit_pending()
            commands = [e["command"] for e in dispatched if not e["waiting"]]
            attentions = [{"run_id": run.run_id, "command_id": e["command"]["command_id"], "message": e["waiting"],
                           "needs_verification": e.get("needs_verification", False)}
                          for e in dispatched if e["waiting"]]
            run.waiting = attentions[0]["message"] if attentions else ""
            run.command = commands[0] if commands else None
            run.issued_at = dispatched[0]["issued_at"] if dispatched else 0
            return {"command": run.command, "commands": commands, "attention": attentions[0] if attentions else None,
                    "attentions": attentions, "active_run_id": run.run_id, "memory_limit_mb": budget,
                    # Keep this profile open between deferred commands on the final page.
                    # Chrome may unload a profile when its last window is closed.
                    "keep_search": True, "memory_mb": sum(reserved.values())}

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
            entry = run.commands.get(report.command_id)
            if not entry or not entry["issued_at"]:
                raise ValueError("Stale command.")
            if report.outcome == "attention":
                message = report.message or "请在 Chrome 页面中完成登录或验证，页面恢复后将自动继续。"
                changed = entry["waiting"] != message
                entry["waiting"] = run.waiting = message
                entry["needs_verification"] = report.needs_verification
                self._log(run, "[Chrome] 等待人工处理：" + run.waiting)
                if changed:
                    await windows_desktop_service.notify_attention(run.run_id, run.task.task_name, run.waiting)
                return {"accepted": True}
            if report.outcome == "error":
                self._log(run, "[Chrome] 采集失败：" + report.message)
                await windows_desktop_service.notify_attention(run.run_id, run.task.task_name, report.message)
                await self._finish(run, failed=True)
                self.history.append((report.run_id, report.command_id, report.client_id))
                return {"accepted": True}
            action = entry["command"]["action"]
            if action in ("search", "search_next"):
                expected_page = entry['command']['page']
                if report.current_page is not None and report.current_page != expected_page:
                    raise ValueError(f"Search page mismatch: expected {expected_page}, received {report.current_page}.")
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
                fresh = []
                for item in kept:
                    if "https://www.goofish.com/item?id=" + item.id not in run.seen and item.id not in run.discovered:
                        run.discovered.add(item.id)
                        fresh.append(item)
                run.items.extend((i, list(kept), report.region_applied) for i in fresh)
                run.has_more = report.has_more
                self._log(run, f"[Chrome] 第 {run.page} 页：{len(candidates)} 件，待采集详情 {len(fresh)} 件（已处理/重复 {len(kept) - len(fresh)} 件）")
                self._log(run, f"[Chrome] 分页状态：当前 {report.current_page or expected_page} / {report.total_pages or '未知'} 页；下一页控件 {report.next_page_state}；有下一页 {report.has_more}")
                if not run.has_more or run.page >= run.task.max_pages:
                    run.search_done = True
                    reason = f"达到任务页数上限 {run.task.max_pages} 页" if run.page >= run.task.max_pages else f"搜索页面确认第 {run.page} 页为最后一页"
                    self._log(run, "[Chrome] 搜索结束：" + reason)
            elif action == "detail":
                if not report.item or report.item.id != entry["item"].id:
                    raise ValueError("Item ID does not match the requested detail page.")
                # Keep search-only fields when the detail response does not expose them.
                update = {k: v for k, v in report.item.model_dump().items() if v not in ("", [], None)}
                entry["item"] = entry["item"].model_copy(update=update)
                kept, reasons = self.processor.filter(run.task, [entry["item"]], entry["region"])
                if not kept:
                    self._log(run, "[硬过滤] " + "；".join(reasons))
                    entry["item"] = None
                elif entry["item"].seller_id:
                    self._issue(run, "seller", "https://www.goofish.com/personal?" + urlencode({"userId": entry["item"].seller_id}),
                                item=entry["item"], market=entry["market"], region=entry["region"], slot_id=entry["command"]["slot_id"],
                                delay=self._delay_sampler(5, 15))
                else:
                    await self._submit(run, entry, {})
            elif action == "seller":
                await self._submit(run, entry, report.seller)
            run.commands.pop(report.command_id, None)
            run.receipts.add(report.command_id)
            self.history.append((report.run_id, report.command_id, report.client_id))
            await self._advance(run)
            return {"accepted": True}

    async def _submit(self, run, entry, seller):
        item = entry["item"]
        link = "https://www.goofish.com/item?id=" + item.id
        key = (run.task.keyword, link)
        if key not in self._inflight:
            self._inflight[key] = run.run_id
            queued = False
            try:
                stored = await asyncio.to_thread(load_processed_link_keys, run.task.keyword)
                if link not in stored:
                    await run.queue.put((item, seller, list(entry["market"])))
                    queued = True
            finally:
                if not queued:
                    self._inflight.pop(key, None)
        run.seen.add(link)
        entry["item"] = None

    async def _advance(self, run):
        # One browser chain per task: delay -> detail -> delay -> seller -> next item.
        # Unissued commands retain their sampled deadline across heartbeats and retries.
        if run.commands:
            return
        if run.items:
            item, market, region = run.items.popleft()
            self._issue(run, 'detail', 'https://www.goofish.com/item?id=' + item.id,
                        item=item, market=market, region=region, delay=self._delay_sampler(20, 40))
            return
        if not run.search_done and run.has_more and run.page < run.task.max_pages:
            run.page += 1
            self._issue(run, 'search_next', self._search_url(run))
            return
        if run.search_done and not run.items and not run.commands and run.collecting:
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

    async def resume(self, client_id, run_id, command_id=None):
        run = next((r for r in self.processes.values() if r.run_id == run_id), None)
        if run is None:
            raise ValueError("任务已经结束。")
        async with run.lock:
            if run.client_id != client_id:
                raise PermissionError("This run belongs to another Chrome profile.")
            for key, entry in tuple(run.commands.items()):
                if not entry["waiting"] or (command_id and key != command_id):
                    continue
                old = entry["command"]
                run.commands.pop(key)
                command = self._issue(run, old["action"], old["url"], item=entry["item"],
                                      market=entry["market"], region=entry["region"], slot_id=old["slot_id"])
                command["page"] = old["page"]
                command["resume"] = True
                self._log(run, "[Chrome] 页面恢复，继续采集")
            run.waiting = next((e["waiting"] for e in run.commands.values() if e["waiting"]), "")
        return {"resumed": True}

    async def _watch_connections(self):
        while True:
            await asyncio.sleep(10)
            await self._check_connections()

    async def _check_connections(self):
        now = self._clock()
        for run in tuple(self.processes.values()):
            async with run.lock:
                if self.processes.get(run.task.id) is not run or run.returncode is not None or not run.collecting:
                    continue
                offline = self._offline_message(run, now)
                if offline:
                    # Delayed and queued commands have no issued_at, but still depend on
                    # this exact profile. Keep them unissued so reconnect can dispatch them.
                    for entry in run.commands.values():
                        if entry["issued_at"] and not entry["waiting"]:
                            entry["waiting"] = offline
                    if run.connection_waiting != offline:
                        run.connection_waiting = offline
                        await self._notify_waiting(run, offline)
                    continue
                run.connection_waiting = ""
                for entry in run.commands.values():
                    if not entry["issued_at"] or entry["waiting"]:
                        continue
                    if now - entry["issued_at"] > COMMAND_TIMEOUT:
                        entry["waiting"] = run.waiting = "页面采集超时。请检查 Chrome 中的页面，数据恢复后将自动继续。"
                        await self._notify_waiting(run, entry["waiting"])

    async def _notify_waiting(self, run, message):
        self._log(run, "[Chrome] " + message)
        try:
            await asyncio.wait_for(windows_desktop_service.notify_attention(
                run.run_id, run.task.task_name, message), timeout=10)
        except Exception as exc:
            # A failed Windows notification must not stop monitoring every task.
            self._log(run, "[Chrome] Windows 通知失败：" + type(exc).__name__)

    async def stop_all(self):
        for task_id in tuple(self.processes):
            await self.stop_task(task_id)
        if self._watchdog:
            self._watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watchdog
            self._watchdog = None
