"""Packet 8T tests: one-click setup, installation health, lifecycle dashboard.

Hermetic: every test uses an isolated temporary base for owned directories
(never the operator HOME/XDG/state), never enables a live service/timer,
never touches a real browser/audio device/account/model, and never reveals
secrets. Network probes run only in explicitly offline-skipped form.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from voicebridge import lifecycle as L  # noqa: E402
from voicebridge import product as P  # noqa: E402


class _StubCfg:
    """Minimal config stub: get()/section() only, with a fake secret."""

    def __init__(self, values: dict | None = None):
        self._v = values or {}

    def get(self, *keys, default=None):
        node: object = self._v
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    def section(self, name: str):
        v = self._v.get(name, {})
        return v if isinstance(v, dict) else {}


def _paths(tmp_path: Path) -> P.ProductPaths:
    return P.default_paths(base=tmp_path / "root")


def _tree_snapshot(base: Path) -> set[str]:
    if not base.exists():
        return set()
    return {str(p.relative_to(base)) for p in base.rglob("*")}


# ========================================================================
# 1. Checklist: read-only, typed, resumable, offline
# ========================================================================

def test_checklist_is_read_only_and_typed(tmp_path):
    base = tmp_path / "root"
    before = _tree_snapshot(base)
    out = L.checklist(_StubCfg(), _paths(tmp_path))
    assert _tree_snapshot(base) == before  # dry probe created nothing
    assert out["schema"] == L.LIFECYCLE_SCHEMA
    assert out["state"] in L.STATES
    ids = [s["id"] for s in out["steps"]]
    assert ids == [d["id"] for d in L.SETUP_STEPS]
    for s in out["steps"]:
        assert s["state"] in L.STATES
        assert s["next"]  # every step has a safe next action


def test_checklist_offline_reports_unavailable_not_failure(tmp_path):
    out = L.checklist(_StubCfg(), _paths(tmp_path), offline=True)
    assert out["offline"] is True
    states = {s["id"]: s["state"] for s in out["steps"]}
    for st in states.values():
        assert st in L.STATES
    # offline mode is explicit in the platform step, never a silent pass
    assert "offline" in out["steps"][0]["detail"].lower()
    assert out["state"] in L.STATES


# ========================================================================
# 2. Setup run: dry-run purity, confirm gating, cancellation, resume
# ========================================================================

def test_setup_run_dry_run_creates_nothing(tmp_path):
    base = tmp_path / "root"
    before = _tree_snapshot(base)
    out = L.setup_run(_paths(tmp_path), dry_run=True, confirm=False)
    assert _tree_snapshot(base) == before
    assert out["dry_run"] is True
    assert out["state"] in ("PENDING", "BLOCKED")
    assert out["rollback"]  # rollback pointer always present


def test_setup_run_confirm_gated_without_confirmation(tmp_path):
    out = L.setup_run(_paths(tmp_path), dry_run=False, confirm=False)
    assert _tree_snapshot(tmp_path / "root") == set()
    gated = [r for r in out["receipts"]
             if r["step"] in ("components", "model", "desktop")]
    assert gated and all(r["state"] == "DENIED" for r in gated)
    assert out["state"] == "BLOCKED"


def test_setup_run_unknown_steps_fail_closed(tmp_path):
    out = L.setup_run(_paths(tmp_path), steps=["nope"],
                      dry_run=True, confirm=False)
    assert out["state"] == "FAILED"


def test_setup_run_cancel_records_resume_pointer(tmp_path):
    cancel = threading.Event()
    cancel.set()
    out = L.setup_run(_paths(tmp_path), dry_run=False, confirm=True,
                      cancel=cancel)
    assert out["state"] == "CANCELLED"
    assert any(r["state"] == "CANCELLED" for r in out["receipts"])


def test_setup_run_apply_with_confirm_records_journal(tmp_path):
    paths = _paths(tmp_path)
    out = L.setup_run(paths, steps=["platform", "review"],
                      dry_run=False, confirm=True)
    assert out["state"] in L.STATES
    assert out["run_id"]
    # journal holds the run for resume/audit
    entries = P.InstallJournal(paths.journal_dir).read()
    assert any(e.get("run_id") == out["run_id"] for e in entries)


# ========================================================================
# 3. Redaction: secrets never leave the machine
# ========================================================================

SECRET = "s3cr3t-t0k3n-value-xyz"

def test_health_inventory_redacts_secrets(tmp_path):
    cfg = _StubCfg({"webui": {"token": SECRET},
                    "agent": {"api_key": SECRET},
                    "intent": {"local_llm": {"model": "qubit-brain4"}}})
    out = L.health_inventory(cfg, _paths(tmp_path), offline=True)
    blob = json.dumps(out)
    assert SECRET not in blob
    # secret-shaped keys are reference-only, never values
    assert out["credentials"]["webui.token"] == "<redacted>"
    assert out["credentials"]["agent.api_key"] == "<redacted>"
    # inventory still reports typed states
    assert out["schema"] == L.LIFECYCLE_SCHEMA
    assert out["browser_cdp"]["state"] == "UNAVAILABLE"  # offline skip


def test_redaction_scrubs_home_anywhere_in_string():
    home = str(Path.home())
    out = L.redact_snapshot({
        "msg": f"backed up to {home}/.config/qubit-voicebridge/x",
        "nested": [{"err": f"cannot write {home}/state (disk full)"}],
        "plain": "no paths here",
    })
    blob = json.dumps(out)
    assert home not in blob
    assert "~/.config/qubit-voicebridge/x" in blob
    assert out["plain"] == "no paths here"


def test_redaction_scrubs_secret_values_embedded_in_strings(tmp_path):
    cfg = _StubCfg({"webui": {"token": SECRET},
                    "audio": {"mic": f"device using {SECRET} suffix"}})
    snap = L.config_snapshot(cfg)
    blob = json.dumps(snap)
    assert SECRET not in blob
    assert "<redacted>" in blob
    # health + diagnostics + export scrub the same embedded value
    assert SECRET not in json.dumps(
        L.health_inventory(cfg, _paths(tmp_path), offline=True))
    assert SECRET not in json.dumps(
        L.diagnostic_bundle(cfg, _paths(tmp_path)))
    dest = tmp_path / "embedded.json"
    out = L.config_export(cfg, dest, dry_run=False, confirm=True)
    assert out["state"] == "SUCCESS"
    assert SECRET not in dest.read_text()


def test_redaction_scrubs_errors_and_audit_entries(tmp_path, monkeypatch):
    from voicebridge import product as P
    paths = _paths(tmp_path)
    home = str(Path.home())

    def _boom(*a, **k):
        raise RuntimeError(f"cannot write {home}/state/journal (disk full)")
    monkeypatch.setattr(P, "install", _boom)
    out = L.setup_run(paths, steps=["platform"], dry_run=False, confirm=True)
    assert out["state"] == "FAILED"
    assert home not in json.dumps(out)
    assert "~" in json.dumps(out["receipts"])


def test_diagnostic_bundle_has_no_secrets(tmp_path):
    cfg = _StubCfg({"webui": {"token": SECRET}})
    out = L.diagnostic_bundle(cfg, _paths(tmp_path))
    assert SECRET not in json.dumps(out)
    assert out["retention"].startswith("this bundle is generated on demand")


def test_config_export_preview_by_default_creates_nothing(tmp_path):
    cfg = _StubCfg({"webui": {"token": SECRET}, "audio": {"mic": "HyperX"}})
    dest = tmp_path / "profile.json"
    before = _tree_snapshot(tmp_path)
    out = L.config_export(cfg, dest)
    assert out["state"] == "PENDING"
    assert _tree_snapshot(tmp_path) == before  # no file/dir/backup/temp
    assert not dest.exists()


def test_config_export_denied_without_confirm_creates_nothing(tmp_path):
    cfg = _StubCfg({"audio": {"mic": "HyperX"}})
    dest = tmp_path / "profile.json"
    before = _tree_snapshot(tmp_path)
    out = L.config_export(cfg, dest, dry_run=False, confirm=False)
    assert out["state"] == "DENIED"
    assert _tree_snapshot(tmp_path) == before
    assert not dest.exists()


def test_config_export_apply_is_redacted_atomic_and_backed_up(tmp_path):
    cfg = _StubCfg({"webui": {"token": SECRET}, "audio": {"mic": "HyperX"}})
    dest = tmp_path / "profile.json"
    out = L.config_export(cfg, dest, dry_run=False, confirm=True)
    assert out["state"] == "SUCCESS"
    assert out["mode"] == "0600"
    body = dest.read_text()
    assert SECRET not in body
    assert "HyperX" in body
    assert not list(tmp_path.glob("*.tmp"))  # no temp leftovers
    import stat as _stat
    assert _stat.S_IMODE(dest.stat().st_mode) == 0o600
    # second export backs up the first with a validated backup
    out2 = L.config_export(cfg, dest, dry_run=False, confirm=True)
    assert out2["state"] == "SUCCESS"
    baks = list(tmp_path.glob("profile.json.bak-*"))
    assert len(baks) == 1
    assert SECRET not in baks[0].read_text()
    assert json.loads(baks[0].read_text())["profile"]["audio"]["mic"] == "HyperX"


def test_config_export_rejects_symlink_and_missing_parent(tmp_path):
    cfg = _StubCfg({"audio": {"mic": "HyperX"}})
    real = tmp_path / "real.json"
    link = tmp_path / "link.json"
    link.symlink_to(real)
    out = L.config_export(cfg, link, dry_run=False, confirm=True)
    assert out["state"] == "FAILED"
    assert "symlink" in out["detail"]
    assert not real.exists()
    out = L.config_export(cfg, tmp_path / "no-such-dir" / "p.json",
                          dry_run=False, confirm=True)
    assert out["state"] == "FAILED"
    assert "parent" in out["detail"]


def test_config_export_readonly_dir_and_failure_cleanup(tmp_path, monkeypatch):
    cfg = _StubCfg({"audio": {"mic": "HyperX"}})
    ro = tmp_path / "ro"
    ro.mkdir()
    dest = ro / "p.json"
    dest.write_text("{}")  # ensure dir handling: make parent read-only
    ro.chmod(0o500)
    try:
        out = L.config_export(cfg, ro / "new.json", dry_run=False, confirm=True)
        assert out["state"] == "FAILED"
    finally:
        ro.chmod(0o700)
    # injected failure mid-write: no partial output, own temp cleaned
    dest2 = tmp_path / "p2.json"
    monkeypatch.setattr("os.replace", lambda *a, **k: (_ for _ in ()).throw(
        OSError("injected disk-full")))
    out = L.config_export(cfg, dest2, dry_run=False, confirm=True)
    assert out["state"] == "FAILED"
    assert not dest2.exists()
    assert list(tmp_path.glob("p2.json.*.tmp")) == []


def test_config_export_size_limit(tmp_path, monkeypatch):
    cfg = _StubCfg({"audio": {"mic": "HyperX"}})
    monkeypatch.setattr(L, "_EXPORT_SIZE_LIMIT", 10)
    out = L.config_export(cfg, tmp_path / "p.json", dry_run=False, confirm=True)
    assert out["state"] == "FAILED"
    assert "256 KiB" in out["detail"] or "exceeds" in out["detail"]
    assert not (tmp_path / "p.json").exists()


def test_config_diff_reports_changes(tmp_path):
    before = {"audio": {"mic": "A"}, "webui": {"port": 8890}}
    after = {"audio": {"mic": "B"}, "webui": {"port": 8890},
             "extra": {"x": 1}}
    d = L.config_diff(before, after)
    assert d["changed"]["audio.mic"] == {"from": "A", "to": "B"}
    assert d["added"] == {"extra.x": 1}
    assert d["removed"] == {}


# ========================================================================
# 4. Matrix, provenance, wizard, repair, audit, backup, upgrade/rollback
# ========================================================================

def test_capability_matrix_truthful_fallback():
    m = L.capability_matrix(_StubCfg())
    assert "fail closed to local" in m["cloud"]["fallback"]
    assert "never faked" in m["local"]["fallback"]


def test_provenance_view_lists_components():
    pv = L.provenance_view()
    names = [c["component"] for c in pv["components"]]
    assert any("qubit-voicebridge" in n for n in names)
    assert all(c["license"] and c["version"] for c in pv["components"])


def test_audio_wizard_never_records(tmp_path):
    out = L.audio_wizard(_StubCfg())
    assert out["test_tone"]["recorded"] is False
    assert out["state"] in L.STATES
    assert any("never records" in g for g in out["guidance"])


def test_repair_order_never_repeats_finished_work(tmp_path):
    out = L.repair_order(_StubCfg(), _paths(tmp_path), offline=True)
    repair_ids = [r["step"] for r in out["repairs"]]
    for done in out["already_ok"]:
        assert done not in repair_ids


def test_audit_timeline_bounded_and_redacted(tmp_path):
    paths = _paths(tmp_path)
    L.setup_run(paths, steps=["review"], dry_run=False, confirm=True)
    tl = L.audit_timeline(paths, limit=5)
    assert tl["state"] == "READY"
    assert len(tl["entries"]) <= 5
    assert SECRET not in json.dumps(tl)


def test_backup_plan_is_read_only_and_deletion_needs_confirm(tmp_path):
    before = _tree_snapshot(tmp_path / "root")
    out = L.backup_center(_paths(tmp_path), action="plan")
    assert _tree_snapshot(tmp_path / "root") == before
    assert out["state"] == "READY"
    assert "second explicit confirmation" in out["detail"]
    prev = L.backup_center(_paths(tmp_path), action="migrate",
                           dry_run=True, confirm=False)
    assert prev["state"] == "PENDING"
    assert "requires" in prev


def test_upgrade_and_rollback_preview_by_default(tmp_path):
    up = L.upgrade_flow(_paths(tmp_path), dry_run=True, confirm=False)
    assert up["state"] == "PENDING"
    assert up["preserves"]  # user data preserved by contract
    rb = L.rollback_flow(_paths(tmp_path), dry_run=True, confirm=False)
    assert "re-run with --apply --confirm" in rb.get("detail", "")


def test_upgrade_never_reports_false_success(tmp_path):
    up = L.upgrade_flow(_paths(tmp_path), dry_run=False, confirm=True)
    blob = json.dumps(up)
    assert up["state"] != "SUCCESS"
    assert "SUCCESS" not in blob
    assert up["state"] == "BLOCKED"
    assert up["operator_command"]  # exact shell command for the operator
    assert "install.sh" in up["operator_command"]
    assert "nothing was changed" in up["detail"]


def test_unknown_backup_action_fails_closed(tmp_path):
    out = L.backup_center(_paths(tmp_path), action="wipe-everything")
    assert out["state"] == "FAILED"


# ========================================================================
# 5. CLI/dashboard parity: same backend, typed exit codes
# ========================================================================

def test_cli_parser_exposes_setup_surface():
    from voicebridge.cli import build_parser
    args = build_parser().parse_args(["setup", "check"])
    assert args.command == "setup"
    assert args.setup_command == "check"
    args = build_parser().parse_args(
        ["setup", "run", "--apply", "--confirm", "--steps", "platform"])
    assert args.apply is True and args.confirm is True
    assert args.steps == "platform"


def test_cli_matrix_uses_backend_and_exits_zero(capsys):
    from voicebridge.cli import _cmd_setup
    import argparse
    args = argparse.Namespace(setup_command="matrix")
    rc = _cmd_setup(_StubCfg(), args)
    assert rc == 0
    body = json.loads(capsys.readouterr().out)
    assert "fail closed to local" in body["cloud"]["fallback"]


def test_operations_registry_covers_cli_and_dashboard():
    ops = L.operations()
    for name in ("checklist", "setup_run", "health", "diagnostics",
                 "provenance", "matrix", "snapshot", "diff", "export",
                 "wizard", "repair", "backup", "audit", "upgrade", "rollback"):
        assert callable(ops[name]), name


# ========================================================================
# 6. Unknown setup steps fail closed (backend, CLI, dashboard)
# ========================================================================

def test_unknown_steps_rejected_never_silently_dropped(tmp_path):
    paths = _paths(tmp_path)
    before = _tree_snapshot(tmp_path)
    out = L.setup_run(paths, steps=["unknown"], dry_run=True)
    assert out["state"] == "FAILED"
    assert out["unknown"] == ["unknown"]
    assert "known steps" in out["detail"]
    assert _tree_snapshot(tmp_path) == before
    # mixed known/unknown is refused as a whole, not narrowed silently
    out = L.setup_run(paths, steps=["platform", "unknown"], dry_run=True)
    assert out["state"] == "FAILED"
    assert out["unknown"] == ["unknown"]
    assert out["receipts"] == []
    assert _tree_snapshot(tmp_path) == before


def test_cli_unknown_steps_exit_nonzero(capsys):
    from voicebridge.cli import _cmd_setup
    import argparse
    args = argparse.Namespace(setup_command="run", apply=False,
                              confirm=False, steps="platform,nope")
    rc = _cmd_setup(_StubCfg(), args)
    assert rc != 0
    body = json.loads(capsys.readouterr().out)
    assert body["state"] == "FAILED"
    assert body["unknown"] == ["nope"]


# ========================================================================
# 7. Truthful health inventory: provider, engines, services, dependencies
# ========================================================================

def test_provider_local_requires_a_live_backend(tmp_path, monkeypatch):
    import voicebridge.distribution as D
    cfg = _StubCfg({"intent": {"frontier": {"route": "local"}}})
    monkeypatch.setattr(D, "ollama_available", lambda *a, **k: False)
    st = L._provider_state(cfg, offline=False)
    assert st["route"] == "local"
    assert st["state"] == "DEGRADED"
    assert "127.0.0.1:11434" in st["detail"]
    monkeypatch.setattr(D, "ollama_available", lambda *a, **k: True)
    assert L._provider_state(cfg, offline=False)["state"] == "READY"
    assert L._provider_state(cfg, offline=True)["state"] == "UNAVAILABLE"


def test_provider_cloud_without_allowlist_is_blocked():
    cfg = _StubCfg({"intent": {"frontier": {"route": "frontier"}}})
    st = L._provider_state(cfg, offline=False)
    assert st["state"] == "BLOCKED"
    assert "allowlist" in st["detail"]


def test_asr_tts_states_are_probed_not_hardcoded():
    good_asr = L._component_state("sherpa", L._ASR_MODULES, "ASR")
    assert good_asr["state"] == "READY"
    good_tts = L._component_state("piper", L._TTS_MODULES, "TTS")
    assert good_tts["state"] == "READY"
    bad = L._component_state("nonexistent-engine", L._ASR_MODULES, "ASR")
    assert bad["state"] == "MISSING"
    assert "unknown" in bad["detail"]
    # health no longer hardcodes READY
    cfg = _StubCfg({"asr": {"provider": "nonexistent-engine"},
                    "tts": {"provider": "piper"}})
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        from voicebridge import product as P
        paths = P.default_paths(base=Path(td) / "root")
        inv = L.health_inventory(cfg, paths, offline=True)
    assert inv["asr_tts"]["asr"]["state"] == "MISSING"
    assert inv["asr_tts"]["tts"]["state"] == "READY"


def test_optional_deps_are_not_installation_failures():
    deps = L._dep_inventory()
    for name in ("requests", "flask"):
        assert deps[name]["required"] is False, name
        assert deps[name]["state"] in ("READY", "OPTIONAL_MISSING"), name
        assert deps[name]["state"] != "MISSING"
    assert deps["yaml"]["required"] is True


def _write_unit(unit_dir: Path, stem: str):
    (unit_dir / f"{stem}.service").write_text(
        "[Unit]\nDescription=test\n[Service]\nExecStart=test\n")


def test_service_inventory_single_active_listener(tmp_path):
    from voicebridge.config import PRODUCT_NAME
    udir = tmp_path / "units"
    udir.mkdir()
    _write_unit(udir, PRODUCT_NAME)
    inv = L.service_inventory(_paths(tmp_path), unit_dir=udir,
                              active_of=lambda n: "active"
                              if n == f"{PRODUCT_NAME}.service" else "inactive",
                              enabled_of=lambda n: "enabled")
    assert inv["state"] == "READY"
    assert inv["active_listeners"] == 1


def test_disabled_legacy_files_are_not_duplicate_listeners(tmp_path):
    from voicebridge.config import LEGACY_NAME
    udir = tmp_path / "units"
    udir.mkdir()
    _write_unit(udir, LEGACY_NAME)
    inv = L.service_inventory(_paths(tmp_path), unit_dir=udir,
                              active_of=lambda n: "inactive",
                              enabled_of=lambda n: "disabled")
    assert inv["active_listeners"] == 0
    assert inv["state"] == "LEGACY_DISABLED"
    assert inv["state"] != "DUPLICATE"
    assert "not a listener" in inv["detail"]


def test_two_active_units_are_a_real_duplicate(tmp_path):
    from voicebridge.config import LEGACY_NAME, PRODUCT_NAME
    udir = tmp_path / "units"
    udir.mkdir()
    _write_unit(udir, PRODUCT_NAME)
    _write_unit(udir, LEGACY_NAME)
    inv = L.service_inventory(_paths(tmp_path), unit_dir=udir,
                              active_of=lambda n: "active",
                              enabled_of=lambda n: "enabled")
    assert inv["state"] == "DUPLICATE"
    assert inv["active_listeners"] >= 2


def test_no_units_installed_is_unit_missing(tmp_path):
    udir = tmp_path / "units"
    udir.mkdir()
    inv = L.service_inventory(_paths(tmp_path), unit_dir=udir,
                              active_of=lambda n: "inactive",
                              enabled_of=lambda n: "disabled")
    assert inv["state"] == "UNIT_MISSING"
    assert inv["active_listeners"] == 0


# ========================================================================
# 8. Live dashboard routes: hermetic authenticated HTTP tests
# ========================================================================

_DASH_TOKEN = "test-token-8t"
_SETUP_APIS = ["/api/setup/health", "/api/setup/checklist",
               "/api/setup/diagnostics"]


def _dash_server(tmp_path):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI
    import time as _t
    state = tmp_path / "dstate"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\nwebui:\n  token: "
                 + _DASH_TOKEN + "\n")
    cfg = Config.load(p)

    class _FakeOrch:
        _route = "gateway"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = _t.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    return ui, ui._server.server_address[1], state


def _dash_http(port, method, path, body=None, raw=None, token=_DASH_TOKEN):
    import urllib.request
    import urllib.error as _err
    if raw is not None:
        data = raw
    else:
        data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 method=method, data=data)
    if token:
        req.add_header("X-Api-Token", token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except _err.HTTPError as e:
        return e.code, e.read()


def _production_service_state():
    import subprocess
    try:
        r = subprocess.run(["systemctl", "--user", "is-active",
                            "qubit-voicebridge.service"],
                           capture_output=True, text=True, timeout=10)
        return (r.stdout or "").strip()
    except Exception:
        return "unknown"


def test_setup_page_served_without_token_leak(tmp_path):
    ui, port, _ = _dash_server(tmp_path)
    try:
        # Hermes-first: /setup aliases into the Hermes shell (System).
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/setup")
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 302, resp.status
        assert resp.getheader("Location") == "/system"
        # The labelled /classic/ fallback still serves the static setup page
        # and carries no secret.
        status, body = _dash_http(port, "GET", "/classic/setup", token="")
        assert status == 200
        html = body.decode("utf-8", "replace")
        assert 'id="checklist"' in html
        assert _DASH_TOKEN not in html
        assert "?token=" not in html
    finally:
        ui.stop()


def test_setup_apis_require_header_token(tmp_path):
    ui, port, _ = _dash_server(tmp_path)
    try:
        for api in _SETUP_APIS:
            status, _ = _dash_http(port, "GET", api, token="")
            assert status == 401, api
            status, _ = _dash_http(port, "GET", api, token="wrong-token")
            assert status == 401, api
            status, body = _dash_http(
                port, "GET", api + "?token=" + _DASH_TOKEN, token="")
            assert status == 401, api  # query-string tokens always rejected
            assert body == b"unauthorized", api  # no detail/token leakage
    finally:
        ui.stop()


def test_setup_apis_answer_with_valid_header(tmp_path):
    ui, port, _ = _dash_server(tmp_path)
    try:
        for api in _SETUP_APIS:
            status, body = _dash_http(port, "GET", api)
            assert status == 200, api
            data = json.loads(body)
            assert data["schema"] == L.LIFECYCLE_SCHEMA, api
            assert data["state"] in L.STATES, api
            assert _DASH_TOKEN not in body.decode("utf-8", "replace"), api
    finally:
        ui.stop()


def test_dashboard_run_preview_cannot_mutate(tmp_path):
    ui, port, state = _dash_server(tmp_path)
    svc_before = _production_service_state()
    try:
        before = {str(p.relative_to(state)) for p in state.rglob("*")}
        status, body = _dash_http(port, "POST", "/api/setup/run", body={})
        assert status == 200
        data = json.loads(body)
        assert data["state"] == "PENDING"
        assert len(data["receipts"]) == len(L.SETUP_STEPS)
        after = {str(p.relative_to(state)) for p in state.rglob("*")}
        assert after == before  # preview wrote nothing
        assert _production_service_state() == svc_before
    finally:
        ui.stop()


def test_dashboard_run_rejects_unknown_steps(tmp_path):
    ui, port, _ = _dash_server(tmp_path)
    try:
        status, body = _dash_http(port, "POST", "/api/setup/run",
                                  body={"steps": ["platform", "nope"]})
        assert status == 409
        data = json.loads(body)
        assert data["state"] == "FAILED"
        assert data["unknown"] == ["nope"]
    finally:
        ui.stop()


def test_dashboard_run_rejects_malformed_json(tmp_path):
    ui, port, _ = _dash_server(tmp_path)
    try:
        status, body = _dash_http(port, "POST", "/api/setup/run",
                                  raw=b"{not-json")
        assert status == 400
        data = json.loads(body)
        assert data["state"] == "FAILED"
        assert _DASH_TOKEN not in body.decode("utf-8", "replace")
    finally:
        ui.stop()


def test_dashboard_export_preview_cannot_mutate(tmp_path):
    ui, port, state = _dash_server(tmp_path)
    try:
        before = {str(p.relative_to(state)) for p in state.rglob("*")}
        status, body = _dash_http(port, "POST", "/api/setup/export", body={})
        assert status == 200
        data = json.loads(body)
        assert data["state"] == "PENDING"
        after = {str(p.relative_to(state)) for p in state.rglob("*")}
        assert after == before
        # export outside the state directory is denied, not attempted
        status, body = _dash_http(port, "POST", "/api/setup/export",
                                  body={"out": "/tmp/evil.json", "apply": True,
                                        "confirm": True})
        assert status == 403
        assert json.loads(body)["state"] == "DENIED"
        assert not Path("/tmp/evil.json").exists()
    finally:
        ui.stop()


# ========================================================================
# 9. Frontend quality: escaped, backend-authoritative, accessible
# ========================================================================

def _setup_html():
    return (REPO / "frontend" / "setup.html").read_text()


def test_setup_page_accessibility_basics():
    html = _setup_html()
    assert '<meta name="viewport"' in html
    assert 'class="skip-link"' in html
    assert 'id="main-content"' in html
    assert html.count('aria-live="polite"') >= 3
    assert 'role="status"' in html
    assert "min-height:44px" in html  # 44px touch targets
    assert "prefers-reduced-motion" in html
    assert "focus-visible" in html
    assert "max-width: 380px" in html or "max-width:380px" in html  # 320px layout


def test_setup_page_renders_only_escaped_backend_state():
    html = _setup_html()
    assert "function esc(" in html
    assert "document.write" not in html
    assert "eval(" not in html
    # no direct fetch: every request goes through the shared helper
    assert "fetch(" not in html
    assert "/static/vb.js" in html
    # no frontend-only readiness claims anywhere in markup or script
    for claim in ("All systems ready", "Setup complete", "Everything is ready",
                  "100% ready", "ready to go"):
        assert claim not in html
    # every dynamic show() path builds from esc()/tag() output
    assert "show('checklist'" in html and "show('health'" in html


def test_setup_page_covers_all_ui_states():
    html = _setup_html()
    for marker in ("Loading", "UNAVAILABLE", "pair this dashboard",
                   "Offline mode", "window.confirm", "PENDING", "DENIED"):
        assert marker in html, marker
