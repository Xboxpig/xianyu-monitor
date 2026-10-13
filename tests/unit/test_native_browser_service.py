import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.domain.models.browser import BrowserClient, BrowserItem, BrowserReport, BrowserMemory
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


class Clock:
    def __init__(self):
        self.now = 1000

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


async def ready_poll(service, client):
    response = await service.poll(client)
    if response['command'] is None:
        run = next((r for r in service.processes.values()
                    if r.client_id == client.client_id and r.collecting and r.returncode is None), None)
        deadlines = [entry['not_before'] for entry in run.commands.values()
                     if not entry['issued_at']] if run else []
        if deadlines:
            service._clock.advance(max(0, min(deadlines) - service._clock()))
            response = await service.poll(client)
    return response


def make_service(tmp_path, monkeypatch, payload, **updates):
    monkeypatch.chdir(tmp_path)
    task = Task(id=1, **{**payload, **updates})

    async def find(task_id):
        return task.model_copy(update={"id": task_id})
    processor = Processor()
    service = NativeBrowserService(repository=SimpleNamespace(find_by_id=find), processor=processor,
                                   clock=Clock(), delay_sampler=lambda low, high: low)
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
        search = (await ready_poll(service, client))["command"]
        item = BrowserItem(id="123", title="Sony", price="9000")
        result = report_for(search, items=[item], has_more=False)
        await service.report(result)
        detail = (await ready_poll(service, client))["command"]
        assert detail["action"] == "detail"
        assert (await service.report(result))["accepted"] is True
        assert (await ready_poll(service, client))["command"] == detail
        await service.report(report_for(detail, item=item.model_copy(update={"seller_id": "789"})))
        seller = (await ready_poll(service, client))["command"]
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
        command = (await ready_poll(service, client))["command"]
        with pytest.raises(PermissionError):
            await service.report(report_for(command, client_id="other-client-1"))
        await service.report(report_for(command, outcome="attention", message="login required"))
        await service.report(report_for(command, outcome="attention", message="login required"))
        assert len(notices) == 1
        assert (await ready_poll(service, client))["command"] is None
        assert service.is_running(1)
        await service.resume(client.client_id, command["run_id"])
        next_command = (await ready_poll(service, client))["command"]
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
        first = (await ready_poll(service, client))["command"]
        item = BrowserItem(id="123", price="100")
        await service.report(report_for(first, items=[item], has_more=True))
        detail = (await ready_poll(service, client))["command"]
        await service.report(report_for(detail, item=item))
        next_page = (await ready_poll(service, client))["command"]
        assert next_page["action"] == "search_next" and next_page["page"] == 2
        await service.report(report_for(next_page, items=[item], has_more=True))
        assert service.processes[1].collecting is False
        assert (await ready_poll(service, client))["command"]["run_id"] == service.processes[2].run_id
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
            search = (await ready_poll(service, client))["command"]
            await service.report(report_for(search, items=[item]))
            detail = (await ready_poll(service, client))["command"]
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


@pytest.mark.parametrize('upper_bound', [False, True])
def test_random_delays_block_items_and_pagination(tmp_path, monkeypatch, sample_task_payload, upper_bound):
    service, processor = make_service(tmp_path, monkeypatch, sample_task_payload, max_pages=2)
    samples = []

    def sample(low, high):
        samples.append((low, high))
        return high if upper_bound else low

    service._delay_sampler = sample

    async def scenario():
        client = BrowserClient(client_id='chrome-client-1', parallel=True)
        await service.poll(client)
        await service.start_task(1, 'test')
        search = (await service.poll(client))['command']
        items = [BrowserItem(id='1', seller_id='101'), BrowserItem(id='2')]
        await service.report(report_for(search, items=items + [items[0]], has_more=True))
        run = service.processes[1]
        assert len(run.commands) == 1 and len(run.items) == 1 and run.page == 1
        assert samples == [(20, 40)]
        assert '随机等待中' in service.status()['runs'][0]['message']
        for _ in range(3):
            assert (await service.poll(client))['commands'] == []
        assert samples == [(20, 40)]
        service._clock.advance((40 if upper_bound else 20) - 0.1)
        assert (await service.poll(client))['command'] is None
        service._clock.advance(0.1)
        detail = (await service.poll(client))['command']
        assert detail['action'] == 'detail' and detail['url'].endswith('=1')
        service._clock.advance(50)
        assert (await service.poll(client))['commands'] == [detail]
        await service.report(report_for(detail, item=items[0]))
        assert samples == [(20, 40), (5, 15)]
        assert (await service.poll(client))['commands'] == []
        assert run.page == 1 and len(run.items) == 1
        service._clock.advance((15 if upper_bound else 5) - 0.1)
        assert (await service.poll(client))['command'] is None
        service._clock.advance(0.1)
        seller = (await service.poll(client))['command']
        assert seller['action'] == 'seller'
        assert (await service.poll(client))['commands'] == [seller]
        await service.report(report_for(seller, seller={'seller_id': '101'}))
        assert samples == [(20, 40), (5, 15), (20, 40)]
        assert (await service.poll(client))['commands'] == []
        second = (await ready_poll(service, client))['command']
        assert second['url'].endswith('=2') and run.page == 1
        await service.report(report_for(second, item=items[1]))
        page = (await service.poll(client))['command']
        assert page['action'] == 'search_next' and page['page'] == 2
        await service.report(report_for(page, items=items, has_more=False))
        await run.queue.join()
        assert processor.processed == [(1, '1', {'seller_id': '101'}), (1, '2', {})]
        await service.stop_all()

    asyncio.run(scenario())


def test_memory_budget_holds_ready_command_without_bursting_items(tmp_path, monkeypatch, sample_task_payload):
    monkeypatch.setenv('BROWSER_TASK_MEMORY_MB', '512')
    service, _ = make_service(tmp_path, monkeypatch, sample_task_payload)

    async def scenario():
        client = BrowserClient(client_id='chrome-client-1', parallel=True)
        await service.poll(client)
        await service.start_task(1, 'test')
        search = (await service.poll(client))['command']
        await service.report(report_for(search, items=[BrowserItem(id=str(i)) for i in range(1, 31)]))
        client.windows = [BrowserMemory(run_id=search['run_id'], slot_id='search', memory_mb=512)]
        service._clock.advance(20)
        assert (await service.poll(client))['commands'] == []
        entry = next(iter(service.processes[1].commands.values()))
        assert entry['issued_at'] == 0
        client.windows = []
        detail = (await service.poll(client))['command']
        assert detail['command_id'] == entry['command']['command_id']
        monkeypatch.setenv('BROWSER_TASK_MEMORY_MB', '4096')
        assert (await service.poll(client))['commands'] == [detail]
        await service.report(report_for(detail, item=BrowserItem(id='1')))
        assert (await service.poll(client))['commands'] == []
        service._clock.advance(20)
        second = (await service.poll(client))['command']
        assert second['url'].endswith('=2')
        assert len(service.processes[1].items) == 28
        await service.stop_all()

    asyncio.run(scenario())


def test_attention_blocks_next_item_and_resume_preserves_active_page(tmp_path, monkeypatch, sample_task_payload):
    service, processor = make_service(tmp_path, monkeypatch, sample_task_payload)

    async def scenario():
        client = BrowserClient(client_id='chrome-client-1', parallel=True)
        await service.poll(client)
        await service.start_task(1, 'test')
        search = (await service.poll(client))['command']
        await service.report(report_for(search, items=[BrowserItem(id='1'), BrowserItem(id='2')]))
        paused = (await ready_poll(service, client))['command']
        await service.report(report_for(paused, outcome='attention', message='login required'))
        service._clock.advance(60)
        response = await service.poll(client)
        assert response['commands'] == []
        assert response['attentions'][0]['command_id'] == paused['command_id']
        assert len(service.processes[1].items) == 1
        await service.resume(client.client_id, paused['run_id'], paused['command_id'])
        resumed = (await service.poll(client))['command']
        assert resumed['slot_id'] == paused['slot_id'] and resumed['resume']
        with pytest.raises(ValueError):
            await service.report(report_for(paused))
        await service.report(report_for(resumed, item=BrowserItem(id='1')))
        assert (await service.poll(client))['commands'] == []
        second = (await ready_poll(service, client))['command']
        await service.report(report_for(second, item=BrowserItem(id='2')))
        await service.processes[1].queue.join()
        assert [i for _, i, _ in processor.processed] == ['1', '2']
        await service.stop_all()

    asyncio.run(scenario())


def test_ai_runs_independently_during_browser_delay(tmp_path, monkeypatch, sample_task_payload):
    service, processor = make_service(tmp_path, monkeypatch, sample_task_payload)

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def process(*args):
            started.set()
            await release.wait()

        processor.process = process
        client = BrowserClient(client_id='chrome-client-1', parallel=True)
        await service.poll(client)
        await service.start_task(1, 'test')
        search = (await service.poll(client))['command']
        await service.report(report_for(search, items=[BrowserItem(id='1'), BrowserItem(id='2')]))
        first = (await ready_poll(service, client))['command']
        await service.report(report_for(first, item=BrowserItem(id='1')))
        await asyncio.wait_for(started.wait(), timeout=1)
        assert (await service.poll(client))['command'] is None
        second = (await ready_poll(service, client))['command']
        assert second['url'].endswith('=2') and not release.is_set()
        release.set()
        await service.stop_all()

    asyncio.run(scenario())


def test_stop_cancels_pending_delay(tmp_path, monkeypatch, sample_task_payload):
    service, _ = make_service(tmp_path, monkeypatch, sample_task_payload)

    async def scenario():
        client = BrowserClient(client_id='chrome-client-1', parallel=True)
        await service.poll(client)
        await service.start_task(1, 'test')
        search = (await service.poll(client))['command']
        await service.report(report_for(search, items=[BrowserItem(id='1')]))
        deferred = next(iter(service.processes[1].commands.values()))['command']
        response = await service.poll(client)
        assert response['command'] is None
        assert service.processes[1].search_done and response['keep_search']
        assert await service.stop_task(1)
        service._clock.advance(100)
        assert (await service.poll(client))['commands'] == []
        with pytest.raises(ValueError):
            await service.report(report_for(deferred, item=BrowserItem(id='1')))
        await service.stop_all()

    asyncio.run(scenario())


def test_final_page_keeps_profile_window_until_collection_finishes(tmp_path, monkeypatch, sample_task_payload):
    service, _ = make_service(tmp_path, monkeypatch, sample_task_payload, max_pages=1)

    async def scenario():
        client = BrowserClient(client_id='chrome-client-1', parallel=True)
        await service.poll(client)
        await service.start_task(1, 'test')
        search = (await service.poll(client))['command']
        item = BrowserItem(id='1', seller_id='101')
        await service.report(report_for(search, items=[item], has_more=True))
        run = service.processes[1]
        assert run.search_done
        waiting = await service.poll(client)
        assert waiting['commands'] == [] and waiting['keep_search'] and waiting['active_run_id'] == run.run_id
        detail = (await ready_poll(service, client))['command']
        await service.report(report_for(detail, item=item))
        waiting = await service.poll(client)
        assert waiting['commands'] == [] and waiting['keep_search']
        seller = (await ready_poll(service, client))['command']
        await service.report(report_for(seller, seller={}))
        assert not run.collecting
        finished = await service.poll(client)
        assert not finished.get('keep_search') and not finished.get('active_run_id')
        await service.stop_all()

    asyncio.run(scenario())


def test_search_obeys_eight_page_limit_and_rejects_relabelled_responses(tmp_path, monkeypatch, sample_task_payload):
    service, _ = make_service(tmp_path, monkeypatch, sample_task_payload, max_pages=8)

    async def scenario():
        client = BrowserClient(client_id='chrome-client-1', parallel=True)
        await service.poll(client)
        await service.start_task(1, 'test')
        run = service.processes[1]
        for page in range(1, 9):
            command = next(c for c in (await service.poll(client))['commands'] if c['action'].startswith('search'))
            assert command['page'] == page
            if page == 2:
                with pytest.raises(ValueError, match='Search page mismatch'):
                    await service.report(report_for(command, current_page=1, has_more=True))
                assert command['command_id'] in run.commands
            await service.report(report_for(command, current_page=page, total_pages=50, next_page_state='enabled', has_more=True))
        assert run.search_done and not run.collecting
        assert not run.commands
        await service.stop_all()

    asyncio.run(scenario())


@pytest.mark.parametrize('stage', ['queued', 'detail', 'seller', 'memory'])
def test_disconnected_profile_is_reported_before_command_dispatch_and_recovers(
        tmp_path, monkeypatch, sample_task_payload, stage):
    from src.services import native_browser_service as module
    notices = []

    async def notify(*args):
        notices.append(args)

    monkeypatch.setattr(module.windows_desktop_service, 'notify_attention', notify)
    service, _ = make_service(tmp_path, monkeypatch, sample_task_payload,
                              account_strategy='fixed', account_state_file='chrome-profile:Profile 3')

    async def scenario():
        client = BrowserClient(client_id='chrome-client-1', parallel=True,
                               profile_directory='Profile 3', account_state_file='chrome-profile:Profile 3')
        other = BrowserClient(client_id='chrome-client-2', parallel=True, profile_directory='Profile 1')
        await service.poll(client)
        await service.start_task(1, 'one')
        await service.start_task(2, 'two')
        run = service.processes[1]
        if stage != 'queued':
            search = (await service.poll(client))['command']
            item = BrowserItem(id='1', seller_id='101')
            await service.report(report_for(search, items=[item]))
            if stage == 'seller':
                detail = (await ready_poll(service, client))['command']
                await service.report(report_for(detail, item=item))
            if stage == 'memory':
                monkeypatch.setenv('BROWSER_TASK_MEMORY_MB', '512')
                client.windows = [BrowserMemory(run_id=run.run_id, slot_id='search', memory_mb=512)]
                service._clock.advance(20)
                assert (await service.poll(client))['command'] is None
        entry = next(iter(run.commands.values()))
        assert entry['issued_at'] == 0
        service._clock.advance(91)
        await service.poll(other)
        # Status reflects the specific owner even before the periodic watchdog runs.
        status = service.status()
        assert status['connected'] and all(r['state'] == 'attention' for r in status['runs'])
        assert all('Profile 3' in r['message'] and '已断开' in r['message'] for r in status['runs'])
        await service._check_connections()
        await service._check_connections()
        assert len(notices) == 2
        assert entry['issued_at'] == 0 and not entry['waiting']
        assert (await service.poll(other))['commands'] == []
        client.windows = []
        resumed = (await service.poll(client))['command']
        assert resumed['command_id'] == entry['command']['command_id']
        assert resumed['run_id'] == run.run_id
        await service._check_connections()
        assert all(r['state'] == 'collecting' for r in service.status()['runs'])
        assert all(not r.connection_waiting for r in service.processes.values())
        await service.stop_all()

    asyncio.run(scenario())


def test_dispatched_disconnect_keeps_resume_protocol_and_analysis_is_independent(
        tmp_path, monkeypatch, sample_task_payload):
    from src.services import native_browser_service as module
    notices = []

    async def notify(*args):
        notices.append(args)

    monkeypatch.setattr(module.windows_desktop_service, 'notify_attention', notify)
    service, _ = make_service(tmp_path, monkeypatch, sample_task_payload)

    async def scenario():
        client = BrowserClient(client_id='chrome-client-1')
        await service.poll(client)
        await service.start_task(1, 'one')
        command = (await service.poll(client))['command']
        service._clock.advance(91)
        await service._check_connections()
        response = await service.poll(client)
        assert response['command'] is None and response['attention']['command_id'] == command['command_id']
        await service.resume(client.client_id, command['run_id'], command['command_id'])
        resumed = (await service.poll(client))['command']
        assert resumed['resume'] and resumed['slot_id'] == command['slot_id']
        run = service.processes[1]
        await service.report(report_for(resumed, has_more=False))
        assert not run.collecting
        service._clock.advance(91)
        # Losing Chrome after collection must not mislabel AI work as blocked.
        assert service.status()['runs'][0]['state'] == 'analyzing'
        await service._check_connections()
        assert len(notices) == 1
        await service.stop_all()

    asyncio.run(scenario())


def test_notification_failure_does_not_stop_connection_checks(tmp_path, monkeypatch, sample_task_payload):
    from src.services import native_browser_service as module
    notices = []

    async def notify(*args):
        notices.append(args)
        raise RuntimeError('notification unavailable')

    monkeypatch.setattr(module.windows_desktop_service, 'notify_attention', notify)
    service, _ = make_service(tmp_path, monkeypatch, sample_task_payload)

    async def scenario():
        client = BrowserClient(client_id='chrome-client-1')
        await service.poll(client)
        await service.start_task(1, 'one')
        await service.start_task(2, 'two')
        service._clock.advance(91)
        await service._check_connections()
        assert len(notices) == 2
        assert all(r.connection_waiting for r in service.processes.values())
        await service.poll(client)
        await service._check_connections()
        service._clock.advance(91)
        await service._check_connections()
        assert len(notices) == 4
        await service.stop_all()

    asyncio.run(scenario())
