"""Local Viking context engine: layout, confinement, tiers, commit, find.

Proves the Qubit-owned context store (``voicebridge/context_store.py``):
everything runs locally under ``<state_dir>/context`` (Qubit state home),
seed copies never touch live originals, traversal is refused, tier files
render, session commit appends and reads back, find returns seeded content,
and the ``/api/qubit/viking/*`` bridge rows stage-then-execute through the
one broker path (unknown actions stay typed 404s).

Hermetic: temp XDG/QUBIT_HOME only; no live service, network, or hardware.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.unit


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qubit_home"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_layout_groups_and_tier_files_render(tmp_path, monkeypatch):
    """ensure() creates the four groups with L0/L1 tier files."""
    from voicebridge import context_store as CS

    cfg = _cfg(tmp_path, monkeypatch)
    out = CS.ensure(cfg)
    assert out.get("state") == "OK", out
    assert out.get("groups") == ["user", "agent", "resources", "skills"], out
    base = Path(cfg.state_dir()) / "context"
    for group in ("user", "agent", "resources", "skills"):
        assert (base / group).is_dir()
        tiers = CS.tier_files(cfg, group)
        assert tiers["abstract"].strip(), tiers
        assert tiers["overview"].strip(), tiers
        assert tiers["levels"] == ["L0", "L1", "L2"], tiers


def test_confinement_traversal_refused(tmp_path, monkeypatch):
    """Reads/writes outside the context root are typed refusals."""
    from voicebridge import context_store as CS

    cfg = _cfg(tmp_path, monkeypatch)
    CS.ensure(cfg)
    for bad in ("../voice.yaml", "../../etc/passwd", "/abs/path.md",
                "user/../../x", "bogus/file.md"):
        with pytest.raises(CS.ContextError) as exc:
            CS.read_file(cfg, bad)
        assert exc.value.code in ("VIKING_BAD_PATH", "VIKING_NOT_FOUND"), bad
    # resource-add takes a *name*, not a path: traversal tokens are slugged
    # away and the write still lands confined under resources/.
    out = CS.add_resource(cfg, "../../evil", content="x", confirmed=True)
    assert out["path"] == "resources/evil.md", out
    assert (Path(cfg.state_dir()) / "context" / out["path"]).is_file()


def test_seed_copies_never_move_originals(tmp_path, monkeypatch):
    """Seed copies USER.md/MEMORY.md; the live originals stay byte-identical."""
    from voicebridge import context_store as CS

    cfg = _cfg(tmp_path, monkeypatch)
    mem = Path(cfg.state_dir()) / "memories"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "USER.md").write_text("# seed user\n", encoding="utf-8")
    (mem / "MEMORY.md").write_text("# seed memory\n", encoding="utf-8")
    before = {(mem / n).read_bytes() for n in ("USER.md", "MEMORY.md")}
    out = CS.ensure(cfg)
    assert "USER.md" in out["seeded"]["copied"], out
    assert "MEMORY.md" in out["seeded"]["copied"], out
    after = {(mem / n).read_bytes() for n in ("USER.md", "MEMORY.md")}
    assert before == after
    base = Path(cfg.state_dir()) / "context" / "user"
    assert (base / "USER.md").read_text() == "# seed user\n"
    # second ensure never overwrites user content
    (base / "USER.md").write_text("# edited\n", encoding="utf-8")
    CS.ensure(cfg)
    assert (base / "USER.md").read_text() == "# edited\n"


def test_add_memory_then_read_back_then_find(tmp_path, monkeypatch):
    """add-memory appends; read-back shows it; find returns the seeded term."""
    from voicebridge import context_store as CS

    cfg = _cfg(tmp_path, monkeypatch)
    CS.ensure(cfg)
    with pytest.raises(CS.ContextError) as exc:
        CS.add_memory(cfg, "unconfirmed note")
    assert exc.value.code == "VIKING_CONFIRM_REQUIRED"
    out = CS.add_memory(cfg, "qubit-seed-term-9u remembers the harbor",
                        confirmed=True)
    assert out.get("state") == "OK", out
    assert out.get("viking_uri") == "viking://user/memories.md", out
    back = CS.read_file(cfg, "user/memories.md", mode="source")
    assert "qubit-seed-term-9u" in back["content"], back
    found = CS.search(cfg, "qubit-seed-term-9u")
    assert found.get("count", 0) >= 1, found
    assert any(h["id"] == "user/memories.md"
               for h in found["hits"]), found


def test_commit_appends_and_reads_back(tmp_path, monkeypatch):
    """commit_session appends the transcript slice + note; both read back."""
    from voicebridge import context_store as CS

    cfg = _cfg(tmp_path, monkeypatch)
    CS.ensure(cfg)
    transcript = [{"role": "user", "content": "hello qubit"},
                  {"role": "assistant", "content": "hello back"}]
    with pytest.raises(CS.ContextError) as exc:
        CS.commit_session(cfg, "probe-session-9u", transcript)
    assert exc.value.code == "VIKING_CONFIRM_REQUIRED"
    out = CS.commit_session(cfg, "probe-session-9u", transcript,
                            note="probe experience note", confirmed=True)
    assert out.get("state") == "OK", out
    assert out.get("turns") == 2, out
    body = CS.read_file(cfg, "agent/sessions/probe-session-9u.md",
                        mode="source")["content"]
    assert "hello qubit" in body and "hello back" in body, body
    exp = CS.read_file(cfg, "agent/experience.md",
                       mode="source")["content"]
    assert "probe experience note" in exp, exp


def test_resource_add_confined_and_preview_source(tmp_path, monkeypatch):
    """resource-add writes under resources/; preview truncates, source is full."""
    from voicebridge import context_store as CS

    cfg = _cfg(tmp_path, monkeypatch)
    CS.ensure(cfg)
    out = CS.add_resource(cfg, "viking://probe-doc", title="Probe Doc",
                          notes="n", content="A" * 6000, confirmed=True)
    assert out.get("state") == "OK", out
    assert out["path"] == "resources/Probe-Doc.md", out
    preview = CS.read_file(cfg, out["path"])
    assert preview["truncated"] is True, preview
    source = CS.read_file(cfg, out["path"], mode="source")
    assert source["truncated"] is False and len(source["content"]) > 6000


def test_viking_bridge_reads_execute(tmp_path, monkeypatch):
    """GET viking/status + context-tree execute through the bridge."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/viking/status", {}, {})
    assert code == 200, out
    assert out.get("connection") == "local", out
    code, out = HC.dispatch(cfg, "GET",
                            "/api/qubit/viking/context-tree", {}, {})
    assert code == 200, out
    inner = out.get("result", out)
    assert "groups" in inner, out


def test_viking_bridge_staged_then_executes(tmp_path, monkeypatch):
    """POST viking/memory-add stages 409, then executes once with confirm."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    body = {"text": "bridge-probe-memory-9u"}
    code, staged = HC.dispatch(cfg, "POST", "/api/qubit/viking/memory-add",
                               {}, dict(body))
    assert code == 409, staged
    assert staged.get("code") == "CONFIRM_REQUIRED", staged
    assert staged.get("confirmation_id"), staged
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/viking/memory-add",
                            {}, {**body, "confirm": True})
    assert code == 200, out
    inner = out.get("result", out)
    assert inner.get("state") == "OK", out
    assert inner.get("path") == "user/memories.md", out


def test_viking_unknown_action_is_typed_404(tmp_path, monkeypatch):
    """Unknown viking actions (and wrong methods) never execute."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/viking/bogus", {}, {})
    assert code == 404, out
    assert out.get("code") == "FAMILY_UNKNOWN_ACTION", out
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/viking/status", {}, {})
    assert code == 404, out
