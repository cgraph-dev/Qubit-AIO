"""W2 — Brain4 proof + honest failure surfaces.

The first test runs a REAL inference against the configured Qubit brain
(``qubit-brain`` via the local Ollama runner) through the production
``chat_transport.turn`` path and asserts a non-empty reply is persisted. It
skips only when Ollama is genuinely unreachable on this host.

The remaining tests prove failures surface their real cause and never render a
fabricated success.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _ollama_up() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags",
                                    timeout=2.0) as resp:
            data = json.loads(resp.read().decode())
        return isinstance(data, dict)
    except Exception:
        return False


def test_configured_brain_is_qubit_brain4(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    assert CT.configured_model(cfg) == "qubit-brain"
    assert CT.resolve_model(cfg, None)["model"] == "qubit-brain"


def test_live_brain4_inference_non_empty(tmp_path, monkeypatch):
    """A real, non-empty completion from the configured model over the real
    transport, persisted to the session store (raw Brain4 evidence)."""
    from voicebridge import chat_transport as CT
    from voicebridge import local_llm

    if not _ollama_up():
        pytest.skip("Ollama runner not reachable on 127.0.0.1:11434")

    cfg = _cfg(tmp_path, monkeypatch)
    raw = local_llm.chat_local("Reply with exactly: QUBIT_BRAIN4_OK",
                               model=CT.configured_model(cfg),
                               timeout_s=60.0, max_tokens=32)
    assert isinstance(raw, str) and raw.strip(), "real model returned no text"

    res = CT.turn(cfg, "Reply with exactly: QUBIT_BRAIN4_OK",
                  session="brain4-live")
    assert res["ok"] is True, res
    assert isinstance(res["reply"], str) and res["reply"].strip()
    assert res["receipt"]["model"] == "qubit-brain"
    assert res["receipt"]["path"] == "voicebridge.agent.turn"

    shown = CT.store(cfg).show("brain4-live")
    assert shown["messages"][-1]["content"] == res["reply"]
    print(f"\nBRAIN4_RAW={raw!r}\nBRAIN4_TURN={res['reply']!r}\n"
          f"ELAPSED_MS={res['elapsed_ms']}")


def test_live_brain4_route_reaches_real_handler(tmp_path, monkeypatch):
    """The webui-facing dispatcher route executes the real turn (not a mock)."""
    from voicebridge import qubit_compat as HC
    from voicebridge import local_llm

    if not _ollama_up():
        pytest.skip("Ollama runner not reachable on 127.0.0.1:11434")
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/chat", {},
                            {"text": "Reply with exactly: QUBIT_BRAIN4_OK",
                             "session": "brain4-route"})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["reply"].strip()
    assert out["receipt"]["model"] == "qubit-brain"
    print(f"\nROUTE_BRAIN4={out['reply']!r} ms={out['elapsed_ms']}")


def test_model_failure_surfaces_real_cause(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    import voicebridge.local_llm as _llm

    cfg = _cfg(tmp_path, monkeypatch)

    def _boom(*a, **k):
        raise OSError("dial tcp 127.0.0.1:11434: connect: connection refused")

    monkeypatch.setattr(_llm, "chat_local", _boom)
    res = CT.turn(cfg, "hi", session="live-boom")
    assert res["ok"] is False and res["code"] == "MODEL_ERROR"
    assert "connection refused" in res["detail"]
    assert "reply" not in res or not res.get("reply")


def test_model_empty_is_unavailable_never_fabricated(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    import voicebridge.local_llm as _llm

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(_llm, "chat_local",
                        lambda *a, **k: "")
    res = CT.turn(cfg, "hi", session="live-empty")
    assert res["ok"] is False and res["code"] == "MODEL_UNAVAILABLE"
    assert "nothing was fabricated" in res["detail"]
    assert not res.get("reply")
    # an empty model reply must not create an assistant message
    msgs = CT.store(cfg).show("live-empty")["messages"]
    assert [m["role"] for m in msgs] == ["user"]
