from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api.dependencies import get_process_service
from src.api.routes.browser import router
from src.services.native_browser_service import NativeBrowserService


def build_client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    service = NativeBrowserService()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_process_service] = lambda: service
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    return client, service


def test_token_pairing_and_loopback_boundary(tmp_path, monkeypatch):
    client, service = build_client(tmp_path, monkeypatch)
    payload = {"client_id": "chrome-client-1"}
    assert client.post("/api/browser/poll", json=payload).status_code == 401
    response = client.post("/api/browser/setup", json={})
    assert response.status_code == 200
    token = response.json()["token"]
    assert token == service.token()
    assert NativeBrowserService().token() == token
    headers = {"X-Xianyu-Token": token}
    assert client.post("/api/browser/poll", json=payload, headers=headers).status_code == 200
    assert client.get("/api/browser/status").json()["connected"]
    assert "token" not in client.get("/api/browser/status").text
    assert client.post("/api/browser/poll", json=payload, headers={**headers, "Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/browser/poll", json=payload, headers={**headers, "Host": "evil.example"}).status_code == 403
    assert client.post("/api/browser/poll", json=payload, headers={**headers, "Origin": "chrome-extension://" + "a" * 32}).status_code == 200
    remote = TestClient(client.app, base_url="http://127.0.0.1", client=("10.0.0.2", 12345))
    assert remote.post("/api/browser/poll", json=payload, headers=headers).status_code == 403
    assert remote.post("/api/browser/setup", json={}).status_code == 403
    assert client.post("/api/browser/setup", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/browser/setup", json={}, headers={"Host": "evil.example"}).status_code == 403
    assert remote.get("/api/browser/profiles").status_code == 403


def test_stale_reports_and_resume_return_conflict(tmp_path, monkeypatch):
    client, service = build_client(tmp_path, monkeypatch)
    headers = {"X-Xianyu-Token": service.token()}
    payload = {"client_id": "chrome-client-1", "run_id": "stopped", "command_id": "old"}
    assert client.post("/api/browser/report", json=payload, headers=headers).status_code == 409
    assert client.post("/api/browser/resume", json=payload, headers=headers).status_code == 409


def test_profile_selection_binds_extension_and_launches_selected_account(tmp_path, monkeypatch):
    import json
    from src.api.routes import browser
    client, service = build_client(tmp_path, monkeypatch)
    monkeypatch.setenv('CHROME_USER_DATA_DIR', str(tmp_path))
    monkeypatch.setattr('src.infrastructure.desktop.chrome_profiles.sys.platform', 'win32')
    (tmp_path / 'Profile 1').mkdir()
    (tmp_path / 'Local State').write_text(json.dumps({'profile': {'info_cache': {'Profile 1': {'name': 'Work'}}}}))
    assert client.get('/api/browser/profiles').json()['profiles'][0]['name'] == 'Work'
    headers = {'X-Xianyu-Token': service.token()}
    response = client.post('/api/browser/poll', headers=headers, json={'client_id': 'chrome-client-1', 'profile_directory': 'Profile 1', 'account_state_file': 'wrong'})
    assert response.status_code == 200
    assert service.status()['clients'][0]['account_state_file'] == 'chrome-profile:Profile 1'
    assert client.post('/api/browser/poll', headers=headers, json={'client_id': 'chrome-client-1', 'profile_directory': '../outside'}).status_code == 400
    launched = []
    monkeypatch.setattr(browser, 'launch_chrome', lambda url, **kwargs: launched.append(kwargs))
    assert client.post('/api/browser/setup', json={'launch': True, 'profile_directory': 'Profile 1'}).status_code == 200
    assert launched == [{'profile_directory': 'Profile 1'}]


def test_legacy_backend_status_is_available_from_lan(tmp_path, monkeypatch):
    client, service = build_client(tmp_path, monkeypatch)
    client.app.dependency_overrides[get_process_service] = lambda: object()
    remote = TestClient(client.app, base_url="http://10.0.0.1", client=("10.0.0.2", 12345))
    response = remote.get("/api/browser/status")
    assert response.status_code == 200
    assert response.json()["backend"] == "playwright"
    assert remote.post("/api/browser/setup", json={"username": "admin", "password": "test"}).status_code == 403
