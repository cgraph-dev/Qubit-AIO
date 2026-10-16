"""Office persistence receipts must reflect real context-store writes."""
import pytest
from test_office_agent_memory import _cfg, _org, _kinds


@pytest.mark.parametrize("receipt", [{"state": "FAILED"}, {}, None])
def test_rejected_store_receipt_never_emits_success(tmp_path, monkeypatch, receipt):
    from voicebridge import context_store as cs
    from voicebridge.office import agent_memory as memory
    cfg = _cfg(tmp_path, monkeypatch)
    aid = _org(cfg)["w1"]
    calls = []
    def reject(*args, **kwargs):
        calls.append(kwargs)
        return receipt
    monkeypatch.setattr(cs, "add_memory", reject)
    out = memory.remember(cfg, aid, kind="tick", text="not persisted")
    assert calls[0]["scope"] == f"agent:{aid}"
    assert calls[0]["confirmed"] is True
    assert out["state"] == "FAILED"
    assert out["stored"] is False
    assert out["event_id"] is None
    assert "memory_written" not in _kinds(cfg, aid)
    assert memory.recall(cfg, aid)["known"] is False


def test_store_exception_never_becomes_recallable_memory(tmp_path, monkeypatch):
    from voicebridge import context_store as cs
    from voicebridge.office import agent_memory as memory
    cfg = _cfg(tmp_path, monkeypatch)
    aid = _org(cfg)["w1"]
    def fail(*args, **kwargs):
        raise OSError("disk unavailable")
    monkeypatch.setattr(cs, "add_memory", fail)
    out = memory.note(cfg, aid, text="not a durable note")
    assert out["state"] == "FAILED"
    assert "disk unavailable" in out["store_error"]
    assert "note_written" not in _kinds(cfg, aid)
    assert not memory.recall(cfg, aid)["memories"]


def test_real_store_is_invoked_and_bytes_exist_on_disk(tmp_path, monkeypatch):
    from voicebridge import context_store as cs
    from voicebridge.office import agent_memory as memory
    cfg = _cfg(tmp_path, monkeypatch)
    aid = _org(cfg)["w1"]
    calls = []
    original = cs.add_memory
    def traced(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        return result
    monkeypatch.setattr(cs, "add_memory", traced)
    out = memory.remember(cfg, aid, kind="tick", text="boundary-proof-unique")
    assert len(calls) == 1
    assert out["stored"] is True
    assert out["authority"] == calls[0]["authority"]
    assert "boundary-proof-unique" in (cs.root(cfg) / calls[0]["path"]).read_text()
    assert out["event_id"] is not None


@pytest.mark.parametrize("ref", ["viking://unrelated.md", "viking://user/memories.md"])
def test_unrelated_or_shared_uri_hit_does_not_promote_memory(tmp_path, monkeypatch, ref):
    from voicebridge import context_store as cs
    from voicebridge.office import agent_memory as memory
    cfg = _cfg(tmp_path, monkeypatch)
    aid = _org(cfg)["w1"]
    memory.remember(cfg, aid, kind="tick", text="unrelated words", ref=ref)
    monkeypatch.setattr(cs, "search", lambda *a, **k: {
        "hits": [{"viking_uri": "viking://user/memories.md"}]})
    assert memory.recall(cfg, aid, query="needle")["memories"][0]["relevance"] == 0

