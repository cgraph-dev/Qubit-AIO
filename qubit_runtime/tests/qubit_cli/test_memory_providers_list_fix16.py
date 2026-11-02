"""Fix lane 16 — ``GET /api/memory/providers`` (provider list).

The reference selector reads a provider list; before this route the vendored
runtime only exposed ``/api/memory/providers/{name}/config``, so the bare list
404'd. The route must answer with the same discovered rows ``/api/memory``
embeds, off the event loop.
"""
import pytest


def _client():
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")
    from qubit_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return client


class TestMemoryProvidersList:
    @pytest.fixture(autouse=True)
    def _setup(self, _isolate_qubit_home):
        self.client = _client()

    def test_list_returns_discovered_rows(self, monkeypatch):
        import qubit_cli.web_routers.memory_providers as mp

        rows = [{"name": "alpha", "description": "A", "available": True,
                 "configured": True, "status": "ready", "setup": {}}]
        monkeypatch.setattr(mp, "_discover_memory_provider_statuses",
                            lambda: rows)
        resp = self.client.get("/api/memory/providers")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [row["name"] for row in body["providers"]] == ["alpha"]
        assert "active" in body

    def test_list_is_registered_before_the_parameterized_route(self):
        from qubit_cli.web_server import app

        paths = {route.path for route in app.routes}
        assert "/api/memory/providers" in paths
        assert "/api/memory/providers/{name}/config" in paths
