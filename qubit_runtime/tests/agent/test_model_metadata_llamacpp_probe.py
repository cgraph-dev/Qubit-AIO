"""llama.cpp's typed context probe reads /props and is the probe-table entry."""

import agent.model_metadata as mm


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


def test_llamacpp_context_asks_props_for_the_model_then_bare():
    calls = []

    class Client:
        def get(self, url):
            calls.append(url)
            if "?model=" in url:
                return _Resp(404)
            return _Resp(200, {"default_generation_settings": {"n_ctx": 32768}})

    assert mm._llamacpp_context(Client(), "http://127.0.0.1:8080", "qwen") == 32768
    assert calls == ["http://127.0.0.1:8080/props?model=qwen", "http://127.0.0.1:8080/props"]


def test_llamacpp_context_returns_none_when_props_never_answers():
    class Client:
        def get(self, url):
            return _Resp(500)

    assert mm._llamacpp_context(Client(), "http://127.0.0.1:8080", "qwen") is None


def test_probe_table_dispatches_llamacpp_to_the_typed_probe(monkeypatch):
    seen = []

    def typed(client, server_url, model):
        seen.append((server_url, model))
        return 4096

    monkeypatch.setattr(mm, "_llamacpp_context", typed)
    monkeypatch.setattr(mm, "detect_local_server_type", lambda *a, **k: "llamacpp")
    monkeypatch.setattr(mm, "_endpoint_blackholed", lambda *a, **k: False)

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("httpx.Client", lambda **kwargs: Client())

    assert mm._query_local_context_length_uncached("qwen", "http://127.0.0.1:8080") == 4096
    assert seen and seen[0][1] == "qwen"
