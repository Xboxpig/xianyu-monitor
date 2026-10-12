import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.domain.models.browser import BrowserClient, BrowserItem, BrowserReport
from src.domain.models.task import Task
from src.services.native_browser_service import NativeBrowserService
from src.services.native_result_processor import NativeResultProcessor
from src.services.process_service import TaskStartError


class Processor:
    def __init__(self):
        self.processed = []

    def load_prompt(self, task):
        return "test prompt"

    def filter(self, task, items, *args, **kwargs):
        return items, []

    async def record_market(self, *args):
        pass

    async def process(self, task, item, seller, *args):
        self.processed.append((task.id, item.id, seller))


def make_service(tmp_path, monkeypatch, payload, **updates):
    monkeypatch.chdir(tmp_path)
    task = Task(id=1, **{**payload, **updates})

    async def find(task_id):
        return task.model_copy(update={"id": task_id})
    processor = Processor()
    service = NativeBrowserService(repository=SimpleNamespace(find_by_id=find), processor=processor)
    return service, processor


def report_for(command, client_id="chrome-client-1", **fields):
    return BrowserReport(client_id=client_id, run_id=command["run_id"],
                         command_id=command["command_id"], **fields)


def test_collection_lifecycle_and_duplicate_delivery(tmp_path, monkeypatch, sample_task_payload):
    service, processor = make_service(tmp_path, monkeypatch, sample_task_payload)
    events = []
    service.set_lifecycle_hooks(on_started=lambda task_id: events.append(("start", task_id)),
                                on_stopped=lambda task_id: events.append(("stop", task_id)))

    async def scenario():
        client = BrowserClient(client_id="chrome-client-1")
        await service.poll(client)
        assert await service.start_task(1, "test")
        search = (await service.poll(client))["command"]
        item = BrowserItem(id="123", title="Sony", price="9000")
        result = report_for(search, items=[item], has_more=False)
        await service.report(result)
        detail = (await service.poll(client))["command"]
        assert detail["action"] == "detail"
        assert (await service.report(result))["accepted"] is True
        assert (await service.poll(client))["command"] == detail
        await service.report(report_for(detail, item=item.model_copy(update={"seller_id": "789"})))
        seller = (await service.poll(client))["command"]
        assert seller["action"] == "seller"
        await service.report(report_for(seller, seller={"卖家昵称": "seller"}))
        for _ in range(20):
            if not service.is_running(1):
                break
            await asyncio.sleep(0.01)
        assert not service.is_running(1)
        assert processor.processed == [(1, "123", {"卖家昵称": "seller"})]
        assert events == [("start", 1), ("stop", 1)]
        assert (await service.report(report_for(seller)))["accepted"]
        await service.stop_all()
    asyncio.run(scenario())


def test_login_pause_requires_explicit_resume_and_rejects_foreign_client(tmp_path, monkeypatch, sample_task_payload):
    from src.services import native_browser_service as module
    notices = []
    async def notify(*args):
        notices.append(args)
    monkeypatch.setattr(module.windows_desktop_service, 'notify_attention', notify)
    service, _ = make_service(tmp_path, monkeypatch, sample_task_payload)

    async def scenario():
        client = BrowserClient(client_id="chrome-client-1")
        await service.poll(client)
        await service.start_task(1, "test")
        command = (await service.poll(client))["command"]
        with pytest.raises(PermissionError):
            await service.report(report_for(command, client_id="other-client-1"))
        await service.report(report_for(command, outcome="attention", message="login required"))
        await service.report(report_for(command, outcome="attention", message="login required"))
        assert len(notices) == 1
        assert (await service.poll(client))["command"] is None
        assert service.is_running(1)
        await service.resume(client.client_id, command["run_id"])
        next_command = (await service.poll(client))["command"]
        assert next_command["command_id"] != command["command_id"]
        with pytest.raises(ValueError):
            await service.report(report_for(command))
        assert await service.stop_task(1)
        with pytest.raises(ValueError):
            await service.report(report_for(next_command))
        await service.stop_all()
    asyncio.run(scenario())


def test_offline_and_account_selection(tmp_path, monkeypatch, sample_task_payload):
    service, _ = make_service(tmp_path, monkeypatch, sample_task_payload,
                              account_strategy="fixed", account_state_file="state/selected.json")

    async def scenario():
        with pytest.raises(TaskStartError) as error:
            await service.start_task(1, "test", raise_on_failure=True)
        assert error.value.code == "CHROME_EXTENSION_OFFLINE"
        await service.poll(BrowserClient(client_id="chrome-client-1", account_state_file="state/other.json"))
        assert not await service.start_task(1, "test")
        await service.poll(BrowserClient(client_id="chrome-client-2", account_state_file="state/selected.json"))
        assert await service.start_task(1, "test")
        assert service.processes[1].client_id == "chrome-client-2"
        assert (await service.poll(BrowserClient(client_id="chrome-client-1")))["command"] is None
        await service.stop_all()
    asyncio.run(scenario())


def test_queued_tasks_and_pagination_do_not_duplicate_items(tmp_path, monkeypatch, sample_task_payload):
    service, processor = make_service(tmp_path, monkeypatch, sample_task_payload, max_pages=2)

    async def scenario():
        client = BrowserClient(client_id="chrome-client-1")
        await service.poll(client)
        await service.start_task(1, "one")
        await service.start_task(2, "two")
        assert service.processes[2].issued_at == 0
        first = (await service.poll(client))["command"]
        item = BrowserItem(id="123", price="100")
        await service.report(report_for(first, items=[item], has_more=True))
        detail = (await service.poll(client))["command"]
        await service.report(report_for(detail, item=item))
        next_page = (await service.poll(client))["command"]
        assert next_page["action"] == "search_next" and next_page["page"] == 2
        await service.report(report_for(next_page, items=[item], has_more=True))
        assert service.processes[1].collecting is False
        assert (await service.poll(client))["command"]["run_id"] == service.processes[2].run_id
        await asyncio.sleep(0.01)
        assert [entry[1] for entry in processor.processed] == ["123"]
        await service.stop_all()
    asyncio.run(scenario())


def test_native_filters_and_image_url_validation(sample_task_payload):
    task = Task(id=1, **sample_task_payload)
    item = BrowserItem(id="1", price="10000", title="camera", area="Shanghai",
                       personal=True, tags=["包邮"])
    processor = NativeResultProcessor()
    assert processor.filter(task, [item])[0] == [item]
    assert processor.filter(task, [item.model_copy(update={"personal": None})])[0] == []
    assert processor.filter(task, [item.model_copy(update={"price": "1"})])[0] == []
    for url in ("http://127.0.0.1/private", "https://example.com/image",
                "https://alicdn.com@localhost/image", "https://img.alicdn.com:444/image"):
        with pytest.raises(ValidationError):
            BrowserItem(id="1", images=[url])
    assert BrowserItem(id="1", images=["https://img.alicdn.com/item.jpg"]).images


def test_keyword_items_reach_existing_result_storage(tmp_path, monkeypatch, sample_task_payload):
    from src.services.result_storage_service import query_result_records
    from src.infrastructure.persistence.storage_names import build_result_filename
    from src.services import native_result_processor as module
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_DATABASE_FILE", str(tmp_path / "data.sqlite3"))
    task = Task(id=1, **{**sample_task_payload, "decision_mode": "keyword", "keyword_rules": ["Sony"],
                        "personal_only": False, "free_shipping": False})
    async def notify(*args):
        pass
    monkeypatch.setattr(module, "send_ntfy_notification", notify)

    async def scenario():
        item = BrowserItem(id="123", title="Sony Camera", price="9000", description="Good condition")
        await NativeResultProcessor().process(task, item, {"卖家昵称": "seller"}, "", [item], lambda message: None)
        total, records = await query_result_records(
            build_result_filename(task.keyword), ai_recommended_only=False,
            keyword_recommended_only=False, sort_by="crawl_time", sort_order="desc", page=1, limit=10,
        )
        assert total == 1
        assert records[0]["商品信息"]["商品ID"] == "123"
        assert records[0]["ai_analysis"]["is_recommended"] is True
    asyncio.run(scenario())


def test_token_generation_is_atomic_and_stable_across_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    service = NativeBrowserService(token_path=tmp_path / "token")
    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens = list(pool.map(lambda _: service.token(), range(20)))
    assert len(set(tokens)) == 1
    assert len(tokens[0]) >= 32


def test_same_keyword_runs_share_inflight_and_recheck_saved_items(tmp_path, monkeypatch, sample_task_payload):
    from src.services import native_browser_service as module
    service, processor = make_service(tmp_path, monkeypatch, sample_task_payload)
    stored = set()
    monkeypatch.setattr(module, "load_processed_link_keys", lambda keyword: set(stored))

    async def scenario():
        release = asyncio.Event()
        started = asyncio.Event()

        async def process(task, item, seller, *args):
            processor.processed.append((task.id, item.id, seller))
            started.set()
            await release.wait()
            stored.add("https://www.goofish.com/item?id=" + item.id)

        processor.process = process
        client = BrowserClient(client_id="chrome-client-1")
        await service.poll(client)
        for task_id in (1, 2, 3):
            await service.start_task(task_id, "test")
        item = BrowserItem(id="123", price="100")

        async def collect():
            search = (await service.poll(client))["command"]
            await service.report(report_for(search, items=[item]))
            detail = (await service.poll(client))["command"]
            await service.report(report_for(detail, item=item))

        await collect()
        await started.wait()
        await collect()
        assert len(service._inflight) == 1
        release.set()
        await service.processes[1].queue.join()
        await collect()
        for run in tuple(service.processes.values()):
            await run.queue.join()
        assert processor.processed == [(1, "123", {})]
        await service.stop_all()
        assert not service._inflight

    asyncio.run(scenario())
