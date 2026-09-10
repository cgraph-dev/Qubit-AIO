"""Packet 8T-F — dashboard operations console: CLI/dashboard/voice parity.

Proves every applicable capability is reachable through three surfaces that
share one backend operation:

* CLI: ``voicebridge voice say ...`` (registry) + direct subcommands;
* dashboard: the operations console (``voicebridge.dashboard_ops`` +
  ``/api/ops`` + ``/ops`` page);
* voice: the microphone/``voice say`` registry pipeline.

Same backend function, same typed result/error, same permission +
confirmation behavior, same receipt + audit, same cancellation +
emergency-stop, same redaction + rollback. A status tile or catalog row is
not counted: every op here executes the real backend and returns its real
typed result.
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
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


def _dash(ctx_cfg, op_id, args=None, session="dashboard"):
    from voicebridge import dashboard_ops as OPS

    return OPS.run_op(ctx_cfg, op_id, dict(args or {}), actor="dashboard:user",
                      session=session)


# ------------------------------------------------------- catalog integrity
def test_catalog_mirrors_registry_exactly(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    _cfg(tmp_path, monkeypatch)
    cat = {o["id"]: o for o in OPS.catalog()}
    assert set(VC.REGISTRY) <= set(cat), \
        sorted(set(VC.REGISTRY) - set(cat))
    for entry_id, entry in VC.REGISTRY.items():
        item = cat[entry_id]
        assert item["tier"] == "registry"
        assert item["confirm"] == entry.confirm, entry_id
        assert [(a["name"], a["type"], a["required"])
                for a in item["args"]] == \
            [(a.name, a.type, a.required) for a in entry.args], entry_id
        assert item["interfaces"] == ["cli", "dashboard", "voice"], entry_id
        assert item["help"], entry_id
    for op_id, spec in OPS.EXTRA_OPS.items():
        item = cat[op_id]
        assert item["tier"] == "extra"
        assert item["interfaces"] == list(spec.interfaces), op_id
        if item["interfaces"] != ["cli", "dashboard", "voice"]:
            assert item["interface_reason"], op_id


def test_parity_rows_reachable_via_ops(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    _cfg(tmp_path, monkeypatch)
    par = json.loads((REPO_ROOT / "docs" / "PARITY_HERMES.json").read_text())
    covered = set()
    for item in OPS.catalog():
        covered.update(item.get("parity", ()))
    need = {r["cmd"] for r in par["rows"] if r["verdict"] == "IMPLEMENTED"}
    assert (need - covered) <= set(VC.FALLBACK_ROWS), \
        sorted((need - covered) - set(VC.FALLBACK_ROWS))


# ------------------------------------------------- same backend, all three
READ_OPS = {
    # op_id: dashboard args (registry fixed actions supply the rest)
    "status": {},
    "sessions": {},
    "checkpoints_list": {},
    "auth_status": {},
    "mcp": {},
    "worker": {},
    "memory_search": {"query": "invoices"},
    "skills_list": {},
    "projects_list": {},
    "kanban_list": {},
    "kanban_boards": {},
    "kanban_stats": {},
    "kanban_log": {},
    "kanban_export": {},
    "themes": {},
    "proxy": {},
    "proxy_providers": {},
    "hooks_list": {},
    "hooks_doctor": {},
    "sync_list": {},
    "vision_status": {},
    "pairing_list": {},
    "webhooks": {},
    "msg_accounts": {},
    "msg_inbox": {},
    "msg_list": {},
    "gateway": {},
    "peer_list": {},
    "brain": {},
    "brain_vault_list": {},
    "brain_onboard_plan": {},
    "permissions": {},
    "model": {},
    "device": {},
    "flags": {},
    "logs": {},
    "insights": {},
    "prompt_budget": {},
    "reasoning": {},
    "approvals": {},
    "tools": {},
    "secrets": {},
    "diagnostics": {},
    "doctor": {},
    "setup_check": {},
    "verify_release": {},
    "security": {},
    "migrate_status": {},
    "update_status": {},
    "config_profiles": {},
    "computer_status": {},
    "computer_doctor": {},
    "computer_permissions": {},
    "browser_status": {},
    "acp": {},
    "lsp": {},
    "import_inspect": {"source": "PLACEHOLDER_REPLACED_IN_TEST"},
    "capability": {"name": "chat"},
    "curator": {},
    "lessons_list": {},
    "power_status": {},
    "websites_list": {},
    "tui_help": {},
    "console_help": {},
    "mic_help": {},
    "completions": {},
    "backup_info": {},
    "worktree": {},
    "monitor": {},
}


def _scrub(value):
    """Drop clock/volatile fields so two consecutive backend calls compare."""
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()
                if not (k in ("generated_at", "timestamp", "created_at",
                              "updated_at", "duration_ms", "elapsed_ms",
                              "elapsed_s", "latency_ms")
                        or k.endswith(("_at", "_ms", "_ns", "_elapsed")))}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def test_dashboard_matches_direct_backend_for_every_read_op(tmp_path,
                                                            monkeypatch):
    """Dashboard receipts equal the backend dict the CLI/voice path returns."""
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    read_ops = dict(READ_OPS)
    read_ops["import_inspect"] = {"source": str(tmp_path)}
    for op_id, args in read_ops.items():
        entry = VC.REGISTRY[op_id]
        backend = VC._BACKENDS[entry.backend]
        merged = dict(args)
        for key, value in (entry.fixed or ()):
            merged.setdefault(key, value)
        if entry.confirm:
            continue  # confirm-gated ops are covered below, never here
        expected = backend(VC.make_ctx(cfg, actor="probe", session="probe",
                                       source="cli"), dict(merged))
        got = _dash(cfg, op_id, args)
        assert got["state"] == "executed", (op_id, got)
        assert got["receipt"]["entry"] == op_id
        assert got["receipt"]["session"] == "dashboard"
        if _scrub(got["receipt"]["result"]) != _scrub(expected):
            # Resource gauges (VRAM/load) are volatile between two
            # consecutive backend calls; retry once before failing.
            expected = backend(VC.make_ctx(cfg, actor="probe",
                                           session="probe", source="cli"),
                               dict(merged))
            got = _dash(cfg, op_id, args)
            assert _scrub(got["receipt"]["result"]) == _scrub(expected), op_id


def test_cli_say_matches_dashboard_for_sample(tmp_path, monkeypatch):
    """``voice say --json`` and the dashboard return the same typed result."""
    import subprocess

    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import dashboard_ops as OPS

    env = {"XDG_CONFIG_HOME": str(tmp_path / "c"),
           "XDG_STATE_HOME": str(tmp_path / "s"),
           "XDG_DATA_HOME": str(tmp_path / "d"),
           "PATH": "/usr/bin:/bin"}
    proc = subprocess.run(
        [sys.executable, "-m", "voicebridge.cli", "--config",
         str(tmp_path / "voice.yaml"), "voice", "say", "--json",
         "qubit, list tasks"],
        capture_output=True, text=True, cwd=str(REPO_ROOT), env=env,
        timeout=60)
    assert proc.returncode == 0, proc.stderr[-500:]
    cli_out = json.loads(proc.stdout)
    dash_out = OPS.run_op(cfg, "kanban_list", {}, actor="dashboard:user",
                          session="dashboard")
    assert cli_out["state"] == "executed"
    assert cli_out["receipt"]["result"] == dash_out["receipt"]["result"]


# ------------------------------------------------------- confirmation parity
def test_confirm_lifecycle_matches_voice_semantics(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    staged = _dash(cfg, "kanban_create", {"title": "ops parity"})
    assert staged["state"] == "needs-confirm"
    cid = staged["confirmation_id"]
    # Nothing executed yet.
    listed = _dash(cfg, "kanban_list", {})
    assert all(t.get("title") != "ops parity"
               for t in listed["receipt"]["result"].get("tasks", []))
    # Confirm executes exactly once through the same backend.
    done = OPS.confirm_op(cfg, cid)
    assert done["state"] == "executed", done
    assert done["receipt"]["result"]["task"]["title"] == "ops parity"
    again = OPS.confirm_op(cfg, cid)
    assert again["state"] == "failed", again
    assert again["code"] in ("NO_CONFIRMATION", "ALREADY_DONE",
                             "SETTLE_ERROR"), again


def test_cancel_denies_before_execution(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    staged = _dash(cfg, "kanban_create", {"title": "cancelled task"})
    assert staged["state"] == "needs-confirm"
    out = OPS.cancel_op(cfg, staged["confirmation_id"])
    assert out["state"] == "executed", out
    listed = _dash(cfg, "kanban_list", {})
    assert all(t.get("title") != "cancelled task"
               for t in listed["receipt"]["result"].get("tasks", []))
    after = OPS.confirm_op(cfg, staged["confirmation_id"])
    assert after["state"] == "failed", after


def test_sessions_isolate_confirmations(tmp_path, monkeypatch):
    """A confirmation staged on one session is invisible to another."""
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    staged = _dash(cfg, "kanban_create", {"title": "s1"}, session="s1")
    other = OPS.confirm_op(cfg, staged["confirmation_id"], session="s2")
    assert other["state"] == "failed", other
    mine = OPS.confirm_op(cfg, staged["confirmation_id"], session="s1")
    assert mine["state"] == "executed", mine


# ------------------------------------------------------- emergency stop
def _latch(cfg, on=True):
    from voicebridge.power import power_control_store

    store = power_control_store(cfg)
    store.set(emergency_stop=bool(on), global_disable=False,
              issued_by="cli:user")


def test_estop_refuses_mutations_but_not_reads(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    try:
        _latch(cfg, True)
        refused = _dash(cfg, "kanban_create", {"title": "blocked"})
        assert refused["state"] == "refused", refused
        assert refused["code"] == "ESTOP_LATCHED"
        refused_extra = _dash(cfg, "sync_revoke", {"offer_id": "x"})
        assert refused_extra["state"] == "refused", refused_extra
        read = _dash(cfg, "kanban_list", {})
        assert read["state"] == "executed", read
    finally:
        _latch(cfg, False)


# ------------------------------------------------------- redaction + secrets
def test_secret_values_never_appear_in_outputs(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    probe = "sk-abcdefghijklmnopqrstuvwx"
    out = _dash(cfg, "webhook_show", {"sub_id": probe})
    assert probe not in json.dumps(out), out
    out = _dash(cfg, "capability", {"name": probe})
    assert probe not in json.dumps(out), out


def test_passphrase_never_persisted_in_journal(tmp_path, monkeypatch):
    """Sync/backup passphrases: staged redacted, re-supplied at confirm."""
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    pw = "correct horse battery staple 8tf"
    staged = _dash(cfg, "sync_offer", {"passphrase": pw})
    assert staged["state"] == "needs-confirm", staged
    assert staged.get("sensitive_required") == ["passphrase"]
    journal_path = Path(cfg.state_dir()) / "approvals.jsonl"
    assert pw not in journal_path.read_text(encoding="utf-8")
    # Confirm without the passphrase fails closed (nothing executed).
    needs = OPS.confirm_op(cfg, staged["confirmation_id"])
    assert needs["state"] == "failed"
    assert needs["code"] == "SENSITIVE_REQUIRED"
    assert pw not in journal_path.read_text(encoding="utf-8")
    # Confirm with the passphrase executes the real backend.
    done = OPS.confirm_op(cfg, staged["confirmation_id"],
                          sensitive={"passphrase": pw})
    assert done["state"] == "executed", done
    assert done["receipt"]["result"]["offer"]["offer_id"].startswith("sync_")
    assert pw not in journal_path.read_text(encoding="utf-8")
    assert pw not in json.dumps(done)


def test_backup_roundtrip_stays_encrypted(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    pw = "another test passphrase 9tf"
    staged = _dash(cfg, "backup_create", {"passphrase": pw, "label": "ops"})
    assert staged["state"] == "needs-confirm", staged
    done = OPS.confirm_op(cfg, staged["confirmation_id"],
                          sensitive={"passphrase": pw})
    assert done["state"] == "executed", done
    bundle_path = Path(done["receipt"]["result"]["path"])
    assert bundle_path.exists()
    assert pw.encode() not in bundle_path.read_bytes()
    journal_path = Path(cfg.state_dir()) / "approvals.jsonl"
    assert pw not in journal_path.read_text(encoding="utf-8")


# ------------------------------------------------------- typed refusals
REFUSAL_OPS = {
    "browser_close": ({"profile": "Default"}, "BROWSER_CLOSE_REFUSED"),
    "proxy_start": ({}, "PROXY_NO_SERVER"),
    "acp_serve": ({}, None),
    "acp_accept_hooks": ({}, None),
    "lsp_install": ({"server": "pyright"}, None),
    "computer_install": ({}, None),
    "import_agent_import": ({}, "IMPORT_AGENT_REVIEW_REQUIRED"),
    "gateway_supervision": ({"action": "run"}, "GATEWAY_NO_SUPERVISION"),
    "kanban_delegated": ({"subcommand": "swarm"}, "KANBAN_DELEGATED"),
    "pets_downloads": ({}, "PETS_NO_DOWNLOADS"),
    "plugins_remote": ({"identifier": "github.com/x/y"}, "BLOCKED_REMOTE_PLUGIN"),
}


def test_extra_refusals_are_typed(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    for op_id, (args, code) in REFUSAL_OPS.items():
        out = _dash(cfg, op_id, args)
        assert out["state"] == "executed", (op_id, out)
        result = out["receipt"]["result"]
        state = str(result.get("state", ""))
        assert state in ("REFUSED", "REJECTED", "BLOCKED", "DELEGATED"), \
            (op_id, result)
        if code:
            assert result.get("code") == code, (op_id, result)


def test_unknown_op_and_bad_args_are_typed(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    assert _dash(cfg, "nope", {})["code"] == "UNKNOWN_OP"
    bad = _dash(cfg, "kanban_claim", {})
    assert bad["state"] == "failed" and bad["code"] == "BAD_ARGS", bad
    bad = _dash(cfg, "kanban_claim", {"task_id": "abc"})
    assert bad["state"] == "failed" and bad["code"] == "BAD_ARGS", bad
    bad = _dash(cfg, "kanban_claim", {"task_id": "1", "bogus": "1"})
    assert bad["state"] == "failed" and bad["code"] == "BAD_ARGS", bad
    missing = OPS.confirm_op(cfg, "deadbeef" * 4)
    assert missing["state"] == "failed", missing


# ------------------------------------------------------- HTTP + page surface
def _start_server(tmp_path):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI
    import time as _time

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: test-token\n")
    cfg = Config.load(p)

    class _FakeOrch:
        _route = "gateway"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = _time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    return ui, ui._server.server_address[1]


def _http(port, method, path, body=None, token="test-token"):
    import urllib.request
    import urllib.error

    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 method=method, data=data)
    if token:
        req.add_header("X-Api-Token", token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def test_ops_http_end_to_end(tmp_path):
    ui, port = _start_server(tmp_path)
    try:
        status, ctype, body = _http(port, "GET", "/ops")
        assert status == 200, status
        assert "text/html" in ctype
        html = body.decode()
        assert 'id="main-content"' in html
        assert "/api/ops" in html
        # Catalog requires auth.
        status, _, _ = _http(port, "GET", "/api/ops", token=None)
        assert status == 401, status
        status, _, body = _http(port, "GET", "/api/ops")
        assert status == 200, status
        catalog = json.loads(body)["ops"]
        assert len(catalog) >= 100
        # Read op executes the real backend.
        status, _, body = _http(port, "POST", "/api/ops/run",
                                {"id": "kanban_list", "args": {}})
        assert status == 200, status
        out = json.loads(body)
        assert out["state"] == "executed", out
        assert out["receipt"]["entry"] == "kanban_list"
        # Mutating op stages confirmation; confirm executes; cancel denies.
        status, _, body = _http(port, "POST", "/api/ops/run",
                                {"id": "kanban_create",
                                 "args": {"title": "http task"}})
        out = json.loads(body)
        assert out["state"] == "needs-confirm", out
        cid = out["confirmation_id"]
        status, _, body = _http(port, "POST", "/api/ops/confirm",
                                {"confirmation_id": cid})
        out = json.loads(body)
        assert out["state"] == "executed", out
        status, _, body = _http(port, "POST", "/api/ops/run",
                                {"id": "kanban_create",
                                 "args": {"title": "cancelled http"}})
        cid = json.loads(body)["confirmation_id"]
        status, _, body = _http(port, "POST", "/api/ops/cancel",
                                {"confirmation_id": cid})
        assert json.loads(body)["state"] == "executed"
        # Unknown op is typed, never HTML.
        status, _, body = _http(port, "POST", "/api/ops/run",
                                {"id": "nope", "args": {}})
        assert json.loads(body)["code"] == "UNKNOWN_OP"
        # Unauthenticated execution is refused.
        status, _, _ = _http(port, "POST", "/api/ops/run",
                             {"id": "status", "args": {}}, token="wrong")
        assert status == 401, status
        # Approvals-page respond executes the staged backend (not just a
        # mark): stage, approve via the approvals route, verify the task.
        status, _, body = _http(port, "POST", "/api/ops/run",
                                {"id": "kanban_create",
                                 "args": {"title": "approvals page task"}})
        cid = json.loads(body)["confirmation_id"]
        status, _, body = _http(port, "POST", "/api/approvals/respond",
                                {"id": cid, "decision": "approve"})
        assert status == 200, (status, body[:200])
        out = json.loads(body)
        assert out["state"] == "executed", out
        assert out["receipt"]["result"]["task"]["title"] == \
            "approvals page task"
    finally:
        ui.stop()


def test_ops_page_branding_nav_and_accessibility(tmp_path):
    html = (REPO_ROOT / "frontend" / "ops.html").read_text(encoding="utf-8")
    assert "<title>Qubit VoiceBridge — Operations</title>" in html
    assert 'name="viewport"' in html
    assert "/static/theme.css" in html and "/static/app.css" in html
    assert 'aria-live="polite"' in html
    assert 'class="skip-link"' in html
    assert 'type="password"' in html, "secret args need password inputs"
    for href in ("/", "/activity", "/reports", "/ops", "/settings",
                 "/setup", "/token-setup"):
        assert f'href="{href}"' in html, href
    for product in ("Hermes Agent", "OpenClaw", "hermes-agent"):
        assert product not in html, product
    # Every other page links back to /ops and /setup (uniform nav).
    for page in ("index", "activity", "reports", "settings", "setup",
                 "approvals", "token-setup"):
        text = (REPO_ROOT / "frontend" / f"{page}.html").read_text(
            encoding="utf-8")
        assert 'href="/ops"' in text, page
        assert 'href="/setup"' in text or 'aria-current="page">Setup' in text, page


# ------------------------------------------------------- lessons tri-parity
def test_rollback_ignores_console_records_safely(tmp_path, monkeypatch):
    """Rollback compatibility: restoring the previous checkout (which only
    knows `voice.*` confirmations) leaves console state usable — new
    `dashboard.*` records are ignored by the legacy filter, the journal
    format is unchanged, and backend state files need no migration."""
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC
    from voicebridge.approvals import ApprovalJournal

    cfg = _cfg(tmp_path, monkeypatch)
    staged = OPS.run_op(cfg, "kanban_boards_create", {"board": "rollback"},
                        session="dashboard")
    assert staged["state"] == "needs-confirm"
    journal = ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl",
                              default_ttl_s=120.0)
    # Legacy reader semantics: only voice.* records in the legacy session.
    legacy = [a for a in journal.pending()
              if str(getattr(a, "capability", "")).startswith("voice.")
              and (getattr(a, "meta", {}) or {}).get("session", "voice")
              == "voice"]
    assert all(not str(a.capability).startswith("dashboard.")
               for a in legacy)
    # The new record is intact for the new code after a "rollback + return".
    rec = journal.get(staged["confirmation_id"])
    assert rec is not None and rec.state == "in_progress"
    assert set(asdict_keys(rec)) == {
        "id", "capability", "summary", "created_at", "expires_at",
        "state", "decision", "settled_at", "meta"}, "journal schema unchanged"
    # Voice flow is unaffected by the console record's presence.
    ctx = VC.make_ctx(cfg, session="voice", source="user")
    res = VC.resolve("qubit, list boards")
    assert res.kind == VC.MATCH
    out = VC.execute(ctx, res)
    assert out.kind == VC.EXECUTED
    # Confirming through the console still works after the interlude.
    done = OPS.confirm_op(cfg, staged["confirmation_id"])
    assert done["state"] == "executed", done


def asdict_keys(rec):
    import dataclasses

    return {f.name for f in dataclasses.fields(rec)}


def test_lessons_cli_dashboard_voice_share_one_store(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC
    from voicebridge.learning import LearningLedger

    cfg = _cfg(tmp_path, monkeypatch)
    led = LearningLedger(cfg.state_dir())
    lid = led.stage_model_inference("ops prefers concise reports",
                                    confidence=0.9)
    # Dashboard lists the same review item.
    dash = OPS.run_op(cfg, "lessons_list", {})
    assert dash["state"] == "executed", dash
    assert any(l.get("lesson_id") == lid
               for l in dash["receipt"]["result"]["reviews"])
    # Voice resolves and reads the same store.
    ctx = VC.make_ctx(cfg, actor="voice:user", session="voice",
                       source="user")
    res = VC.resolve("qubit, list lessons")
    assert res.kind == VC.MATCH and res.entry.id == "lessons_list"
    out = VC.execute(ctx, res)
    assert out.kind == VC.EXECUTED
    # CLI lists the same store.
    buf = io.StringIO()
    with redirect_stdout(buf):
        from voicebridge import cli as CLI

        rc = CLI.main(["lessons", "list", "--config",
                       str(tmp_path / "voice.yaml")])
    assert rc == 0
    assert lid in buf.getvalue()
    # Dashboard promotes (confirmed) — same ledger the CLI then removes.
    staged = OPS.run_op(cfg, "lessons_promote", {"lesson_id": lid})
    assert staged["state"] == "needs-confirm", staged
    done = OPS.confirm_op(cfg, staged["confirmation_id"])
    assert done["state"] == "executed", done
    assert done["receipt"]["result"]["lesson_id"] == lid
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI.main(["lessons", "remove", "--confirm", lid, "--config",
                       str(tmp_path / "voice.yaml")])
    assert rc == 0, buf.getvalue()
    assert lid not in json.dumps(OPS.run_op(cfg, "lessons_list", {}))


# ------------------------------------------- structured/passphrase extras
def test_new_extras_declare_voice_reasons(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    _cfg(tmp_path, monkeypatch)
    cat = {o["id"]: o for o in OPS.catalog()}
    for op_id in ("export_state", "import_state", "actions_preview",
                  "actions_prepare", "actions_retry", "actions_recover",
                  "ghost_preview"):
        item = cat[op_id]
        assert item["tier"] == "extra", op_id
        assert item["interfaces"] == ["cli", "dashboard"], op_id
        assert item["interface_reason"], op_id


def test_export_extra_never_persists_passphrase(tmp_path, monkeypatch):
    import json as _json

    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    out = OPS.run_op(cfg, "export_state", {"passphrase": "s3cr3t-hunter2"},
                     actor="dashboard:user", session="dashboard")
    assert out["state"] == "needs-confirm", out
    assert out["sensitive_required"] == ["passphrase"], out
    cid = out["confirmation_id"]
    journal_text = (Path(cfg.state_dir()) / "approvals.jsonl").read_text()
    assert "s3cr3t-hunter2" not in journal_text
    out2 = OPS.confirm_op(cfg, cid, actor="dashboard:user",
                          session="dashboard",
                          sensitive={"passphrase": "s3cr3t-hunter2"})
    assert out2["state"] == "executed", out2
    assert out2["receipt"]["result"]["bundle"].endswith(".vbp")
    journal_text = (Path(cfg.state_dir()) / "approvals.jsonl").read_text()
    assert "s3cr3t-hunter2" not in journal_text


def test_import_extra_is_preview_only(tmp_path, monkeypatch):
    import json as _json

    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    bundle = Path(cfg.state_dir()) / "seed.vbp"
    out = OPS.run_op(cfg, "export_state", {"passphrase": "pw"},
                     actor="dashboard:user", session="dashboard")
    OPS.confirm_op(cfg, out["confirmation_id"], actor="dashboard:user",
                   session="dashboard", sensitive={"passphrase": "pw"})
    (exported,) = sorted(Path(cfg.state_dir()).glob("portable-*.vbp"))
    out2 = OPS.run_op(cfg, "import_state",
                      {"bundle_path": str(exported), "passphrase": "pw"},
                      actor="dashboard:user", session="dashboard")
    assert out2["state"] == "needs-confirm", out2
    out3 = OPS.confirm_op(cfg, out2["confirmation_id"],
                          actor="dashboard:user", session="dashboard",
                          sensitive={"passphrase": "pw"})
    assert out3["state"] == "executed", out3
    assert "preview only" in out3["receipt"]["result"]["note"]
    out4 = OPS.run_op(cfg, "import_state",
                      {"bundle_path": str(tmp_path / "missing.vbp"),
                       "passphrase": "pw"},
                      actor="dashboard:user", session="dashboard")
    assert out4["state"] == "needs-confirm", out4
    out5 = OPS.confirm_op(cfg, out4["confirmation_id"],
                          actor="dashboard:user", session="dashboard",
                          sensitive={"passphrase": "pw"})
    assert out5["state"] == "failed", out5


def test_actions_ghost_preview_extras_execute(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    out = OPS.run_op(cfg, "actions_preview",
                     {"action_id": "nope",
                      "args_json": "{}"},
                     actor="dashboard:user", session="dashboard")
    assert out["state"] == "needs-confirm", out
    out2 = OPS.confirm_op(cfg, out["confirmation_id"],
                          actor="dashboard:user", session="dashboard")
    assert out2["state"] in ("executed", "failed"), out2
    out3 = OPS.run_op(cfg, "ghost_preview",
                      {"action_id": "nope", "args_json": "{}"},
                      actor="dashboard:user", session="dashboard")
    assert out3["state"] == "needs-confirm", out3
    out4 = OPS.confirm_op(cfg, out3["confirmation_id"],
                          actor="dashboard:user", session="dashboard")
    assert out4["state"] in ("executed", "failed"), out4
    bad = OPS.run_op(cfg, "actions_preview",
                     {"action_id": "x", "args_json": "{bad"},
                     actor="dashboard:user", session="dashboard")
    assert bad["state"] == "needs-confirm", bad
    bad2 = OPS.confirm_op(cfg, bad["confirmation_id"],
                          actor="dashboard:user", session="dashboard")
    assert bad2["state"] == "failed", bad2
