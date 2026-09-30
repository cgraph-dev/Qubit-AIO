"""9U — /chat terminal profile picker + persisted default.

The terminal UI offers model profiles with their context-guard verdicts and
persists the selection to the same ``terminal.profile`` key the spawn guard
reads (one authority, never a shadow default). The list surface is
non-secret: no ``key_ref`` values, base URLs, or tokens may leak.
"""
from __future__ import annotations

import json


def _cfg(tmp_path, monkeypatch, *, overrides: bool = False):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    p = tmp_path / "voice.yaml"
    body = "logging:\n  jsonl: false\n"
    if overrides:
        body += ("intent:\n  frontier:\n    profile_overrides:\n"
                 "      big:\n        policy_enabled: true\n"
                 "        allowlisted: true\n")
    p.write_text(body)
    return C.Config.load(p)


def _make_api_profile(cfg, pid: str = "big"):
    from voicebridge import model_profiles_ops as MP

    return MP.create_model_profile(
        cfg, confirm=True, profile_id=pid, kind="api",
        provider="opencode-zen", model="deepseek-v4-flash-free",
        base_url="https://opencode.ai/zen/v1",
        key_ref="env:TEST_ONLY_REF",
        locality="external", egress_consent="allow",
        context_tokens=200000, roles=["chat"])


def test_terminal_profiles_are_non_secret_with_verdicts(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch, overrides=True)
    _make_api_profile(cfg)

    code, rows = HC.h_terminal_profiles(cfg, {}, {}, {})
    assert code == 200 and isinstance(rows, list), rows
    by_id = {r["id"]: r for r in rows}
    assert "big" in by_id, sorted(by_id)
    row = by_id["big"]
    assert row["provider"] == "opencode-zen"
    assert row["context_tokens"] == 200000
    assert row["has_credential"] is True
    assert row["meets_floor"] is True and row["code"] == "OK", row
    # No secret or routing material on the picker surface: no banned keys
    # and no credential/base-url values anywhere in the payload.
    blob = json.dumps(rows)
    assert "TEST_ONLY_REF" not in blob and "opencode.ai" not in blob
    for entry in rows:
        for banned in ("key_ref", "base_url", "token", "secret", "password"):
            assert banned not in entry, (entry["id"], banned)


def test_production_spawn_threads_selected_profile(tmp_path, monkeypatch):
    """The requested Qubit model profile id reaches the real spawn path:
    guard selects it (never a silent substitution) and the resolved
    model/provider ride into the child env + home config."""
    from voicebridge import pty_ws as PTY

    cfg = _cfg(tmp_path, monkeypatch, overrides=True)
    _make_api_profile(cfg)

    server = PTY.PtyWsServer(cfg=cfg)
    guard = server._resolve_guard("big")
    assert guard is not None and guard.get("ok") is True, guard
    assert guard.get("profile_id") == "big", guard
    assert guard.get("model") == "deepseek-v4-flash-free", guard

    seen = {}

    def _capture(cols, rows, resume, profile, **kw):
        seen.update({"cols": cols, "rows": rows, "resume": resume,
                     "profile": profile, **kw})
        raise RuntimeError("stop-before-exec")

    monkeypatch.setattr(PTY, "_default_spawn", _capture)
    with __import__("pytest").raises(RuntimeError, match="stop-before-exec"):
        server._production_spawn(80, 24, "", "big")
    env = seen.get("env_overrides") or {}
    assert env.get("HERMES_MODEL") == "deepseek-v4-flash-free", env
    assert env.get("HERMES_TUI_PROVIDER") == "opencode-zen", env
    home = seen.get("home_config") or {}
    assert home.get("model") == "deepseek-v4-flash-free", home
    assert home.get("provider") == "opencode-zen", home


def test_every_implemented_route_has_a_handler():
    """A registered route without a HANDLERS entry answers 500 NO_HANDLER
    live (proven 2026-09-22 with /api/terminal/profiles). This closure keeps
    the route table and the handler map in lockstep."""
    from voicebridge import qubit_compat as HC

    missing = [(r["method"], r["pattern"], r["handler"]) for r in HC.ROUTES
               if r.get("disposition") == "implemented"
               and r.get("handler") not in HC.HANDLERS]
    assert missing == [], missing


def test_terminal_profile_set_persists_to_voice_yaml(tmp_path, monkeypatch):
    import yaml

    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _make_api_profile(cfg)

    code, out = HC.terminal_profile_set(cfg, {}, {"profile_id": "nope"}, {})
    assert (code, out["code"]) == (404, "UNKNOWN_PROFILE"), (code, out)
    code, out = HC.terminal_profile_set(cfg, {}, {}, {})
    assert (code, out["code"]) == (400, "MALFORMED"), (code, out)

    code, staged = HC.terminal_profile_set(
        cfg, {}, {"profile_id": "big"}, {})
    assert code == 409 and staged["confirmation_id"], staged

    code, done = HC.terminal_profile_set(
        cfg, {},
        {"profile_id": "big",
         "confirmation_id": staged["confirmation_id"]}, {})
    assert code == 200 and done["profile_id"] == "big", done

    data = yaml.safe_load((tmp_path / "voice.yaml").read_text())
    assert data["terminal"]["profile"] == "big"
    # The guard reads the same key: one authority, no shadow default.
    fresh = type(cfg).load(tmp_path / "voice.yaml")
    assert fresh.get("terminal", "profile", default="") == "big"


def test_terminal_profile_set_refreshes_running_config(tmp_path, monkeypatch):
    """The picker must take effect on the RUNNING service, not only on disk.

    ``terminal_context_guard`` reads ``cfg["terminal"]["profile"]`` from the
    in-memory config; before the fix the PUT persisted voice.yaml but the
    running guard kept the old value (observed live as a stale ``qubit-go`` /
    ``UNKNOWN_PROFILE`` refusal on /chat until a restart).
    """
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _make_api_profile(cfg)
    assert (cfg.get("terminal", "profile", default="") or "") == ""

    code, staged = HC.terminal_profile_set(cfg, {}, {"profile_id": "big"}, {})
    assert code == 409 and staged["confirmation_id"], staged
    code, done = HC.terminal_profile_set(
        cfg, {},
        {"profile_id": "big",
         "confirmation_id": staged["confirmation_id"]}, {})
    assert code == 200 and done["profile_id"] == "big", done
    # Live effect: the same in-memory object the guard reads is updated.
    assert cfg.get("terminal", "profile", default="") == "big"
