from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api.dependencies import get_process_service
from src.api.routes.browser import router
from src.services.native_browser_service import NativeBrowserService
from src.infrastructure.config.settings import settings


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
    assert client.post("/api/browser/setup", json={"username": "bad", "password": "bad"}).status_code == 401
    response = client.post("/api/browser/setup", json={"username": settings.web_username, "password": settings.web_password})
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


def test_stale_reports_and_resume_return_conflict(tmp_path, monkeypatch):
    client, service = build_client(tmp_path, monkeypatch)
    headers = {"X-Xianyu-Token": service.token()}
    payload = {"client_id": "chrome-client-1", "run_id": "stopped", "command_id": "old"}
    assert client.post("/api/browser/report", json=payload, headers=headers).status_code == 409
    assert client.post("/api/browser/resume", json=payload, headers=headers).status_code == 409


def test_legacy_backend_status_is_available_from_lan(tmp_path, monkeypatch):
    client, service = build_client(tmp_path, monkeypatch)
    client.app.dependency_overrides[get_process_service] = lambda: object()
    remote = TestClient(client.app, base_url="http://10.0.0.1", client=("10.0.0.2", 12345))
    response = remote.get("/api/browser/status")
    assert response.status_code == 200
    assert response.json()["backend"] == "playwright"
    assert remote.post("/api/browser/setup", json={"username": "admin", "password": "test"}).status_code == 403
