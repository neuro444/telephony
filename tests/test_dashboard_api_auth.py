from fastapi.testclient import TestClient

import app as gateway
import config


def test_operational_feeds_require_dashboard_key(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_API_KEY", "dashboard-secret")
    client = TestClient(gateway.app)

    for path in ("/orders/recent", "/handoffs/recent", "/cost/calls"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"X-API-Key": "wrong"}).status_code == 401
        assert (
            client.get(path, headers={"X-API-Key": "dashboard-secret"}).status_code
            == 200
        )


def test_health_does_not_require_dashboard_key(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_API_KEY", "dashboard-secret")
    assert TestClient(gateway.app).get("/health").status_code == 200


def test_operational_feeds_fail_closed_when_key_is_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_API_KEY", "")
    assert TestClient(gateway.app).get("/orders/recent").status_code == 503
