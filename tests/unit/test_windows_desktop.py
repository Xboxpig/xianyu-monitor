import asyncio
import base64
import json
import subprocess
import threading
from types import SimpleNamespace

import pytest

from src.infrastructure.desktop import windows_notifications as notifications
from src.infrastructure.external.notification_clients import windows_client
from src.services.windows_desktop_service import WindowsDesktopService
from src.services import notification_config_service as config


def test_payload_preserves_untrusted_text_and_restricts_activation():
    body = '<script>& $($env:USERNAME)\x00'
    payload = notifications.build_payload('title', body, url='https://www.goofish.com/item?id=123')
    assert payload['body'] == body[:-1]
    assert len(notifications.build_payload('x' * 200, 'x' * 1000)['body']) == 600
    for url in ('file:///C:/Windows', 'javascript:alert(1)', 'https://user:pass@example.com'):
        with pytest.raises(ValueError):
            notifications.build_payload('title', body, url=url)


def test_native_helper_transports_only_base64_json(monkeypatch):
    captured = {}
    monkeypatch.setattr(notifications, 'register_identity', lambda: None)
    monkeypatch.setattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)

    def run(args, **kwargs):
        captured.update(args=args, **kwargs)
        return SimpleNamespace(returncode=0, stdout='submitted', stderr='')

    monkeypatch.setattr(subprocess, 'run', run)
    assert notifications.show_toast('title', '$($env:USERNAME) & <xml>')
    decoded = json.loads(base64.b64decode(captured['args'][-1]))
    assert decoded['body'] == '$($env:USERNAME) & <xml>'
    assert '-File' in captured['args'] and '-Command' not in captured['args']
    assert captured['creationflags'] == 0x08000000


@pytest.mark.parametrize('failure', ['exit', 'timeout'])
def test_native_delivery_errors_reach_caller(monkeypatch, failure):
    monkeypatch.setattr(notifications, 'register_identity', lambda: None)
    monkeypatch.setattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)

    def run(*args, **kwargs):
        if failure == 'timeout':
            raise subprocess.TimeoutExpired('powershell', 20)
        return SimpleNamespace(returncode=1, stdout='Windows notifications are disabled', stderr='')

    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises((RuntimeError, subprocess.TimeoutExpired)):
        notifications.show_toast('title', 'body')


def test_recommendation_uses_desktop_link_and_reports_delivery_error(monkeypatch):
    captured = {}

    def send(title, body, **kwargs):
        captured.update(title=title, body=body, **kwargs)
        raise RuntimeError('native error')

    monkeypatch.setattr(windows_client, 'show_toast', send)
    client = windows_client.WindowsToastClient(enabled=True)
    with pytest.raises(RuntimeError, match='native error'):
        asyncio.run(client.send({'商品标题': 'Sony', '当前售价': '999', '商品链接': 'https://www.goofish.com/item?id=123'}, '价格合适'))
    assert captured['url'] == 'https://www.goofish.com/item?id=123'
    assert captured['group'] == 'recommendations'


def test_channel_tests_do_not_send_other_notifications():
    settings = config._build_notification_settings_model({'windows_toast_enabled': True, 'ntfy_topic_url': 'https://ntfy.sh/topic'})
    windows = config.prepare_notification_test_settings({}, settings, channel='windows')
    assert windows.windows_toast_enabled and windows.ntfy_topic_url is None
    ntfy = config.prepare_notification_test_settings({}, settings, channel='ntfy')
    assert not ntfy.windows_toast_enabled


def test_tray_quit_is_deduplicated_and_cleanup_stops_icon():
    service = WindowsDesktopService()
    actions = []
    service.configure('http://127.0.0.1:8001', lambda: actions.append('quit'))
    service.icon = SimpleNamespace(stop=lambda: actions.append('stop'))
    service.thread = threading.Thread(target=lambda: None)
    service.thread.start()
    service.request_quit()
    service.request_quit()
    service.stop()
    service.stop()
    assert actions == ['quit', 'stop']
    assert service.icon is None and service.thread is None
