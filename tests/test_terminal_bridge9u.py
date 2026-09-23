"""Terminal bridge (`/chat`) — MCP registry merge + one-conversation mapping.

Hermetic: no PTY, no MCP connection, no vendored runtime spawn. Covers the
runtime-home ``config.yaml`` ``mcp_servers`` merge (reference-only, additive,
atomic, backed up, idempotent) and the canonical conversation mapping the
vendored TUI resumes.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

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


def _seed_registry(cfg):
    from voicebridge import mcp_servers as MS

    MS.upsert(cfg, "alpha", {"transport": "stdio", "command": "alpha-mcp",
                             "args": ["--x"], "env": {"TOKEN": "env:ALPHA_TOKEN"}})
    MS.set_enabled(cfg, "alpha", True)
    MS.upsert(cfg, "beta", {"transport": "http",
                            "url": "https://beta.test/mcp", "auth": "header",
                            "env": {"TOKEN": "vault:beta-token"}})
    MS.upsert(cfg, "gamma", {"transport": "http",
                             "url": "https://gamma.test/mcp", "auth": "header",
                             "env": {"MCP_AUTH": "env:GAMMA_TOKEN"}})
    MS.set_enabled(cfg, "gamma", True)


def _yaml(path: Path):
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# MCP registry -> runtime home config.yaml
# ---------------------------------------------------------------------------
def test_merge_adds_registry_servers_reference_only(tmp_path, monkeypatch):
    from voicebridge import terminal_bridge as TB

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_registry(cfg)
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "model:\n  provider: ollama\n  default: brain4\n"
        "mcp_servers:\n  keepme:\n    command: keep\n")

    out = TB.merge_mcp_servers(home, cfg)
    assert out["changed"] is True, out
    assert out["added"] == ["alpha", "beta", "gamma"], out
    assert out["preserved"] == ["keepme"], out
    assert out["backup"].endswith("config.yaml.qubit-bak")
    assert Path(out["backup"]).is_file()

    data = _yaml(home / "config.yaml")
    # Unrelated top-level keys and the operator's own entry survive.
    assert data["model"] == {"provider": "ollama", "default": "brain4"}
    servers = data["mcp_servers"]
    assert servers["keepme"] == {"command": "keep"}
    # stdio: command/args carried, env is a *reference* placeholder.
    assert servers["alpha"]["command"] == "alpha-mcp"
    assert servers["alpha"]["args"] == ["--x"]
    assert servers["alpha"]["env"] == {"TOKEN": "${env:ALPHA_TOKEN}"}
    assert servers["alpha"]["enabled"] is True
    # http: url carried, vault reference is verbatim (names a location only).
    assert servers["beta"]["url"] == "https://beta.test/mcp"
    assert servers["beta"]["env"] == {"TOKEN": "vault:beta-token"}
    # header auth ref becomes a Bearer template, never a resolved token.
    assert servers["gamma"]["headers"] == {
        "Authorization": "Bearer ${env:GAMMA_TOKEN}"}
    assert servers["gamma"]["enabled"] is True

    text = (home / "config.yaml").read_text(encoding="utf-8")
    assert "supersecret" not in text
    assert "Bearer ${env:GAMMA_TOKEN}" in text


def test_merge_is_idempotent_and_backs_up_original(tmp_path, monkeypatch):
    from voicebridge import terminal_bridge as TB

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_registry(cfg)
    home = tmp_path / "home"
    home.mkdir()
    original = "model:\n  default: brain4\nmcp_servers:\n  keepme:\n    command: keep\n"
    (home / "config.yaml").write_text(original)

    first = TB.merge_mcp_servers(home, cfg)
    assert first["changed"] is True
    backup_text = (home / "config.yaml.qubit-bak").read_text(encoding="utf-8")
    assert backup_text == original
    after_first = (home / "config.yaml").read_text(encoding="utf-8")

    second = TB.merge_mcp_servers(home, cfg)
    assert second["changed"] is False, second
    assert second["backup"] == ""
    assert (home / "config.yaml").read_text(encoding="utf-8") == after_first
    assert (home / "config.yaml.qubit-bak").read_text(encoding="utf-8") == original


def test_merge_preserves_existing_entry_over_registry(tmp_path, monkeypatch):
    from voicebridge import terminal_bridge as TB

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_registry(cfg)
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "mcp_servers:\n  alpha:\n    command: operator-alpha\n")

    TB.merge_mcp_servers(home, cfg)
    servers = _yaml(home / "config.yaml")["mcp_servers"]
    assert servers["alpha"] == {"command": "operator-alpha"}
    assert {"beta", "gamma"} <= set(servers)


def test_merge_never_writes_a_raw_value(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge import terminal_bridge as TB

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(MS, "list_servers", lambda _cfg: {
        "schema": MS.SCHEMA,
        "servers": [
            {"name": "raw", "transport": "stdio", "command": "raw-mcp",
             "args": [], "auth": "none", "env": {"TOKEN": "supersecret"},
             "enabled": True},
            {"name": "rawhttp", "transport": "http", "url": "https://r.test/mcp",
             "auth": "header", "env": {"MCP_AUTH": "sk-raw-token"},
             "enabled": True},
        ],
        "total": 2,
    })
    home = tmp_path / "home"
    home.mkdir()
    out = TB.merge_mcp_servers(home, cfg)
    assert out["added"] == ["raw", "rawhttp"], out
    text = (home / "config.yaml").read_text(encoding="utf-8")
    assert "supersecret" not in text
    assert "sk-raw-token" not in text
    servers = _yaml(home / "config.yaml")["mcp_servers"]
    assert "env" not in servers["raw"]
    assert "headers" not in servers["rawhttp"]


def test_merge_corrupt_config_is_degraded_and_untouched(tmp_path, monkeypatch):
    from voicebridge import terminal_bridge as TB

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_registry(cfg)
    home = tmp_path / "home"
    home.mkdir()
    # A YAML list is not a mapping: refuse rather than clobber.
    (home / "config.yaml").write_text("- one\n- two\n")

    out = TB.merge_mcp_servers(home, cfg)
    assert out["state"] == "DEGRADED"
    assert out["code"] == "TERMINAL_HOME_CONFIG_UNREADABLE"
    assert out["changed"] is False
    assert (home / "config.yaml").read_text(encoding="utf-8") == "- one\n- two\n"


def test_merge_unreadable_registry_is_degraded(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge import terminal_bridge as TB

    cfg = _cfg(tmp_path, monkeypatch)

    def _boom(_cfg):
        raise RuntimeError("registry store corrupt")

    monkeypatch.setattr(MS, "list_servers", _boom)
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("model:\n  default: brain4\n")

    out = TB.merge_mcp_servers(home, cfg)
    assert out["state"] == "DEGRADED"
    assert out["code"] == "MCP_REGISTRY_UNREADABLE"
    assert out["changed"] is False
    assert _yaml(home / "config.yaml") == {"model": {"default": "brain4"}}


def test_merge_absent_config_and_empty_registry_is_noop(tmp_path, monkeypatch):
    from voicebridge import terminal_bridge as TB

    cfg = _cfg(tmp_path, monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    out = TB.merge_mcp_servers(home, cfg)
    assert out["state"] == "OK" and out["changed"] is False, out
    assert not (home / "config.yaml").exists()


def test_merge_write_failure_is_degraded(tmp_path, monkeypatch):
    from voicebridge import terminal_bridge as TB

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_registry(cfg)
    home = tmp_path / "home"
    home.mkdir()
    original = "model:\n  default: brain4\n"
    (home / "config.yaml").write_text(original)

    def _boom(_path, _data):
        raise OSError("disk full")

    monkeypatch.setattr(TB, "_atomic_write", _boom)
    out = TB.merge_mcp_servers(home, cfg)
    assert out["state"] == "DEGRADED"
    assert out["code"] == "TERMINAL_HOME_CONFIG_WRITE_FAILED"
    assert out["changed"] is False
    assert (home / "config.yaml").read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# One canonical conversation
# ---------------------------------------------------------------------------
def test_session_defaults_to_canonical_conversation(tmp_path, monkeypatch):
    from voicebridge import agent
    from voicebridge import chat_transport as CT
    from voicebridge import terminal_bridge as TB

    cfg = _cfg(tmp_path, monkeypatch)
    canonical = agent.canonical_session_id(cfg)
    assert canonical == CT.DEFAULT_SESSION

    out = TB.canonical_terminal_session(cfg)
    assert out["session_id"] == canonical
    assert out["ensured"] is True, out
    shown = CT.store(cfg).show(canonical)
    assert shown["state"] == "OK", shown


def test_session_explicit_id_is_kept_and_ensured(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import terminal_bridge as TB

    cfg = _cfg(tmp_path, monkeypatch)
    out = TB.canonical_terminal_session(cfg, "terminal-abc")
    assert out["session_id"] == "terminal-abc"
    assert out["ensured"] is True, out
    assert CT.store(cfg).show("terminal-abc")["state"] == "OK"


def test_session_voice_alias_converges(tmp_path, monkeypatch):
    from voicebridge import agent
    from voicebridge import terminal_bridge as TB

    cfg = _cfg(tmp_path, monkeypatch)
    out = TB.canonical_terminal_session(cfg, "voice")
    assert out["session_id"] == agent.canonical_session_id(cfg)


def test_terminal_resume_env_sets_both_vendored_names():
    from voicebridge import terminal_bridge as TB

    assert TB.terminal_resume_env("") == {}
    assert TB.terminal_resume_env("sid-1") == {
        "QUBIT_TUI_RESUME": "sid-1", "HERMES_TUI_RESUME": "sid-1"}


# ---------------------------------------------------------------------------
# _default_spawn wiring (stubbed vendored spawn; no real PTY)
# ---------------------------------------------------------------------------
def test_default_spawn_wires_bridge_and_resume(tmp_path, monkeypatch):
    from voicebridge import pty_ws
    from voicebridge import qubit_runtime
    from voicebridge import terminal_bridge as TB

    cfg = _cfg(tmp_path, monkeypatch)
    home = tmp_path / "runtime-home"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    qubit_runtime.ensure_path()

    wsc = pytest.importorskip("qubit_cli.web_server_chat")
    pb = pytest.importorskip("qubit_cli.pty_bridge")

    calls = {"merge": 0, "session": 0, "resume": None, "spawn_env": None}

    def fake_merge(qubit_home, _cfg):
        calls["merge"] += 1
        assert Path(qubit_home) == home
        return {"state": "OK", "changed": False}

    def fake_session(_cfg, requested):
        calls["session"] += 1
        return {"session_id": "canon-session", "ensured": True, "detail": ""}

    def fake_argv(resume=None, sidecar_url=None, profile=None,
                  active_session_file=None):
        calls["resume"] = resume
        return ["qubit", "--tui"], str(home), {"BASE": "1"}

    class _FakePty:
        @staticmethod
        def spawn(argv, cwd=None, env=None, cols=80, rows=24):
            calls["spawn_env"] = dict(env or {})
            return SimpleNamespace(pid=4242)

    monkeypatch.setattr(TB, "merge_mcp_servers", fake_merge)
    monkeypatch.setattr(TB, "canonical_terminal_session", fake_session)
    monkeypatch.setattr(wsc, "_resolve_chat_argv", fake_argv)
    monkeypatch.setattr(pb, "PtyBridge", _FakePty)

    bridge, meta = pty_ws._default_spawn(80, 24, "", "", cfg=cfg)
    assert calls["merge"] == 1 and calls["session"] == 1
    assert calls["resume"] == "canon-session"
    assert calls["spawn_env"]["QUBIT_TUI_RESUME"] == "canon-session"
    assert calls["spawn_env"]["HERMES_TUI_RESUME"] == "canon-session"
    assert calls["spawn_env"]["QUBIT_HOME"] == str(home)
    assert meta["session_id"] == "canon-session"
    assert meta["bridge"]["mcp"]["state"] == "OK"
    assert bridge.pid == 4242


def test_default_spawn_never_blocks_on_bridge_fault(tmp_path, monkeypatch):
    from voicebridge import pty_ws
    from voicebridge import qubit_runtime
    from voicebridge import terminal_bridge as TB

    cfg = _cfg(tmp_path, monkeypatch)
    home = tmp_path / "runtime-home"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    qubit_runtime.ensure_path()

    wsc = pytest.importorskip("qubit_cli.web_server_chat")
    pb = pytest.importorskip("qubit_cli.pty_bridge")

    def _boom(*_a, **_k):
        raise RuntimeError("bridge fault")

    def fake_argv(resume=None, sidecar_url=None, profile=None,
                  active_session_file=None):
        return ["qubit", "--tui"], str(home), {}

    class _FakePty:
        @staticmethod
        def spawn(argv, cwd=None, env=None, cols=80, rows=24):
            return SimpleNamespace(pid=7)

    monkeypatch.setattr(TB, "merge_mcp_servers", _boom)
    monkeypatch.setattr(TB, "canonical_terminal_session", _boom)
    monkeypatch.setattr(wsc, "_resolve_chat_argv", fake_argv)
    monkeypatch.setattr(pb, "PtyBridge", _FakePty)

    bridge, meta = pty_ws._default_spawn(80, 24, "requested-id", "", cfg=cfg)
    # A bridge fault degrades the meta but never the spawn; the raw requested
    # id is preserved.
    assert meta["bridge"]["mcp"]["state"] == "DEGRADED"
    assert meta["bridge"]["session"]["session_id"] == "requested-id"
    assert meta["session_id"] == "requested-id"
    assert bridge.pid == 7
