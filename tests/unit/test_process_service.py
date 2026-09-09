import asyncio
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.services.process_service import ProcessService, TaskStartError


class FakeProcess:
    def __init__(self, pid: int):
        self.pid = pid
        self.returncode = None
        self._done = asyncio.Event()

    async def wait(self):
        await self._done.wait()
        return self.returncode

    def finish(self, returncode: int = 0):
        self.returncode = returncode
        self._done.set()

    def terminate(self):
        self.finish(-15)

    def kill(self):
        self.finish(-9)


def test_process_service_marks_task_stopped_when_process_exits(monkeypatch, tmp_path):
    fake_process = FakeProcess(pid=4321)
    events = []

    async def run_scenario():
        service = ProcessService()
        service.failure_guard.should_skip_start = lambda *args, **kwargs: SimpleNamespace(
            skip=False,
            should_notify=False,
            reason="",
            consecutive_failures=0,
            paused_until=None,
        )

        stopped = asyncio.Event()

        async def on_started(task_id: int):
            events.append(("started", task_id))

        async def on_stopped(task_id: int):
            events.append(("stopped", task_id))
            stopped.set()

        service.set_lifecycle_hooks(on_started=on_started, on_stopped=on_stopped)

        async def fake_create_subprocess_exec(*_args, **_kwargs):
            return fake_process

        monkeypatch.setattr(
            "src.services.process_service.build_task_log_path",
            lambda task_id, _task_name: str(tmp_path / f"task-{task_id}.log"),
        )
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

        started = await service.start_task(0, "task-a")
        assert started is True
        assert events == [("started", 0)]
        assert service.is_running(0) is True

        fake_process.finish(0)
        await asyncio.wait_for(stopped.wait(), timeout=1)

        assert ("stopped", 0) in events
        assert service.is_running(0) is False

    asyncio.run(run_scenario())


def test_process_service_reindexes_runtime_maps_after_delete():
    service = ProcessService()
    proc_a = object()
    proc_c = object()
    watcher_a = object()
    watcher_c = object()

    service.processes = {0: proc_a, 2: proc_c}
    service.log_paths = {0: "a.log", 2: "c.log"}
    service.task_names = {0: "A", 2: "C"}
    service.exit_watchers = {0: watcher_a, 2: watcher_c}

    service.reindex_after_delete(1)

    assert service.processes == {0: proc_a, 1: proc_c}
    assert service.log_paths == {0: "a.log", 1: "c.log"}
    assert service.task_names == {0: "A", 1: "C"}
    assert service.exit_watchers == {0: watcher_a, 1: watcher_c}


def test_process_service_adds_debug_limit_arg_when_env_enabled(monkeypatch):
    monkeypatch.setenv("SPIDER_DEBUG_LIMIT", "1")
    service = ProcessService()

    command = service._build_spawn_command("task-a")

    assert command == [
        sys.executable,
        "-u",
        "spider_v2.py",
        "--task-name",
        "task-a",
        "--debug-limit",
        "1",
    ]


def test_process_service_passes_task_id_to_child_environment(monkeypatch, tmp_path):
    captured = {}

    async def run_scenario():
        service = ProcessService()

        async def fake_create_subprocess_exec(*args, **kwargs):
            captured["args"] = args
            captured["env"] = kwargs["env"]
            return FakeProcess(pid=4322)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
        log_path = tmp_path / "task.log"
        with log_path.open("a", encoding="utf-8") as log_handle:
            process = await service._spawn_process(7, "task-a", log_handle)
        process.finish()

    asyncio.run(run_scenario())

    assert captured["env"]["XIANYU_TASK_ID"] == "7"


def test_process_service_surfaces_failure_guard_details_for_manual_start(monkeypatch):
    async def run_scenario():
        service = ProcessService()
        service.failure_guard.threshold = 3
        service.failure_guard.should_skip_start = lambda *args, **kwargs: SimpleNamespace(
            skip=True,
            should_notify=False,
            reason='TimeoutError: waiting for locator("text=新发布")',
            consecutive_failures=3,
            paused_until=datetime(2026, 9, 10, 15, 16, 57, tzinfo=timezone.utc),
        )

        async def ignore_notification(*_args, **_kwargs):
            return None

        monkeypatch.setattr(service, "_notify_skip", ignore_notification)

        with pytest.raises(TaskStartError) as captured:
            await service.start_task(3, "NAS", raise_on_failure=True)

        error = captured.value
        assert error.code == "TASK_PAUSED_BY_FAILURE_GUARD"
        assert error.status_code == 409
        assert error.context["consecutive_failures"] == 3
        assert error.context["failure_threshold"] == 3
        assert error.context["last_error"].startswith("TimeoutError")

    asyncio.run(run_scenario())


def test_process_service_surfaces_spawn_exception_for_manual_start(monkeypatch):
    async def run_scenario():
        service = ProcessService()
        service.failure_guard.should_skip_start = lambda *args, **kwargs: SimpleNamespace(
            skip=False,
            should_notify=False,
            reason="",
            consecutive_failures=0,
            paused_until=None,
        )

        def fail_to_open_log(*_args, **_kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(service, "_open_log_file", fail_to_open_log)

        with pytest.raises(TaskStartError) as captured:
            await service.start_task(3, "NAS", raise_on_failure=True)

        error = captured.value
        assert error.code == "TASK_PROCESS_START_FAILED"
        assert error.status_code == 500
        assert error.context["reason"] == "OSError: permission denied"

    asyncio.run(run_scenario())
