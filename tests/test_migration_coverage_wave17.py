"""Wave 17 — W-MIG safe subset: non-secret user state ports into Qubit's home.

Hermetic: every test builds a *synthetic* source home under ``tmp_path`` and
passes it explicitly as ``home=`` so the operator's real ``~/.hermes`` is never
opened. The contracts proven here:

* the remaining safe user-state items (``memories``, ``checkpoints``,
  ``channel_directory.json``) are ported read-old/write-new into Qubit's own
  home, byte-identical and idempotent;
* the non-secret subset (``safe=True``) never imports the reference
  provider/model profile or the MCP registry (owner decision #9 — no reference
  provider config, no re-introduced dead MCP servers);
* secrets never migrate: ``auth.json``, ``.env``, the provider ``api_key``, the
  CLI history (``.hermes_history``), request dumps, ``pairing`` tokens, and
  ``gateway_state.json`` are absent from the destination, and no secret value
  appears anywhere under it;
* ``inspect()`` reports the remaining items read-only (present + counts).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

SECRET = "sk-wave17-supersecret-must-never-migrate"


def _cfg(tmp_path, monkeypatch, dest_home):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("QUBIT_HOME", str(dest_home))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _make_source(home: Path) -> None:
    """A synthetic source home with the safe subset plus decoy secrets."""
    from voicebridge import qubit_runtime

    qubit_runtime.ensure_path()
    import qubit_state_common as HSC

    home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(home / "state.db")
    conn.executescript(HSC.SCHEMA_SQL)
    conn.execute(
        "INSERT INTO sessions(id, source, display_name, title, started_at, "
        "last_activity_at, archived, message_count) VALUES(?,?,?,?,?,?,0,?)",
        ("mig-w17", "cli", "Migrated", "Migrated", 1000.0, 1000.0, 2))
    conn.execute(
        "INSERT INTO messages(session_id, role, content, timestamp) "
        "VALUES(?,?,?,?)", ("mig-w17", "user", "hello", 1.0))
    conn.commit()
    conn.close()

    # Safe user-state items — the W-MIG wave-17 subset.
    (home / "memories").mkdir()
    (home / "memories" / "MEMORY.md").write_text("# memory\nremember me\n")
    (home / "memories" / "USER.md").write_text("# user\nthe operator\n")
    (home / "checkpoints" / "store").mkdir(parents=True)
    (home / "checkpoints" / "store" / "HEAD").write_text(
        "ref: refs/heads/master\n")
    (home / "channel_directory.json").write_text(
        json.dumps({"updated_at": 1.0, "platforms": {"telegram": "op"}}))

    # Decoys: none of these may reach the destination.
    (home / "auth.json").write_text(json.dumps({"providers": {"zen": SECRET}}))
    (home / ".env").write_text(f"OPENCODE_GO_API_KEY={SECRET}\n")
    (home / "config.yaml").write_text(
        "model:\n  provider: opencode-zen\n  default: laguna-s-2.1-free\n"
        f"  api_key: {SECRET}\n")
    (home / ".hermes_history").write_text(f"qubit agent -z '{SECRET}'\n")
    (home / "gateway_state.json").write_text(
        json.dumps({"kind": "hermes-gateway", "pid": 1}))
    (home / "pairing").mkdir()
    (home / "pairing" / "token").write_text(SECRET)
    (home / "sessions").mkdir()
    (home / "sessions" / "request_dump_1.json").write_text(
        json.dumps({"prompt": SECRET}))


def _dest_files(root: Path) -> dict:
    out: dict = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = _sha(p)
    return out


def test_inspect_reports_remaining_state_items(tmp_path, monkeypatch):
    from voicebridge import qubit_migration as HM

    cfg = _cfg(tmp_path, monkeypatch, tmp_path / "dest-inspect")
    src = tmp_path / "src-inspect"
    _make_source(src)

    inv = HM.inspect(cfg, home=src)
    items = inv["state_items"]
    assert items["memories"] == {"present": True, "files": 2}, items
    assert items["checkpoints"]["present"] is True
    assert items["checkpoints"]["files"] >= 1
    assert items["channel_directory.json"]["present"] is True
    assert items["channel_directory.json"]["bytes"] > 0
    assert items["gateway_state.json"]["present"] is True
    assert items["pairing"]["present"] is True
    assert items["pairing"]["files"] == 1
    assert items[".hermes_history"]["present"] is True
    assert items[".hermes_history"]["lines"] == 1
    # Counts only — no content/secret is surfaced by inspect().
    assert SECRET not in json.dumps(inv)


def test_safe_apply_ports_state_and_never_migrates_secrets(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_migration as HM

    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path, monkeypatch, dest)
    src = tmp_path / "src"
    _make_source(src)
    source_before = _dest_files(src)

    out = HM.apply(cfg, confirm=True, home=src, safe=True)
    assert out["state"] in ("OK", "PARTIAL"), out
    assert out["safe"] is True, out
    assert out["imported_sessions"] == 1, out
    # Safe mode: provider/model profile and MCP registry are skipped.
    assert out["model_profile"]["state"] == "SKIPPED", out["model_profile"]
    assert out["model_profile"]["code"] == "SAFE_MODE"
    assert out["mcp"]["state"] == "SKIPPED", out["mcp"]
    assert out["mcp"]["imported"] == []

    # (1) Safe state ported into Qubit's OWN home, byte-identical.
    assert (dest / "memories" / "MEMORY.md").is_file()
    assert _sha(dest / "memories" / "MEMORY.md") == _sha(
        src / "memories" / "MEMORY.md")
    assert _sha(dest / "memories" / "USER.md") == _sha(
        src / "memories" / "USER.md")
    assert (dest / "checkpoints" / "store" / "HEAD").is_file()
    assert (dest / "channel_directory.json").is_file()

    # (2) No secret-bearing item was copied.
    assert not (dest / "auth.json").exists()
    assert not (dest / ".env").exists()
    assert not (dest / ".hermes_history").exists()
    assert not (dest / "gateway_state.json").exists()
    assert not (dest / "pairing" / "token").exists()
    assert not (dest / "sessions" / "request_dump_1.json").exists()
    # Provider/model never lands in the destination config or profiles registry.
    assert "opencode-zen" not in json.dumps(_dest_files(dest))
    dest_cfg = dest / "config.yaml"
    if dest_cfg.is_file():
        text = dest_cfg.read_text()
        assert "opencode-zen" not in text and "laguna-s-2.1-free" not in text
    profiles = cfg.state_dir() / "profiles.json"
    if profiles.is_file():
        assert "opencode-zen" not in profiles.read_text()

    # (3) The secret value appears NOWHERE under the destination home.
    for p in dest.rglob("*"):
        if p.is_file():
            assert SECRET not in p.read_text(errors="replace"), p

    # (4) The synthetic source is untouched (destination-only contract).
    assert _dest_files(src) == source_before

    # (5) Idempotent: a second safe apply adds nothing and changes nothing.
    before = _dest_files(dest)
    second = HM.apply(cfg, confirm=True, home=src, safe=True)
    assert second["state"] in ("OK", "PARTIAL"), second
    assert second["imported_sessions"] == 0, second
    assert second["skipped_sessions"] == 1, second
    assert second["added_files"] == 0, second
    assert _dest_files(dest) == before

    # Imported history is reachable through the canonical store.
    assert CT.store(cfg).show("mig-w17")["state"] == "OK"


def test_safe_apply_is_backed_up_and_journaled(tmp_path, monkeypatch):
    from voicebridge import qubit_migration as HM

    dest = tmp_path / "dest-bk"
    cfg = _cfg(tmp_path, monkeypatch, dest)
    src = tmp_path / "src-bk"
    _make_source(src)

    bdir = tmp_path / "backup"
    out = HM.apply(cfg, confirm=True, home=src, safe=True, backup_dir=bdir)
    assert out["state"] in ("OK", "PARTIAL"), out
    assert Path(out["backup_dir"]) == bdir
    journal = json.loads((bdir / "journal.json").read_text())
    assert journal["safe"] is True
    assert journal["source_home"] == str(src)
    # Backup lives under Qubit-owned storage, never under the read-only source.
    assert (bdir / "source" / "state.db").is_file()
    assert (bdir / "destination").is_dir()
    assert str(bdir).startswith(str(tmp_path))
