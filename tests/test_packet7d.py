"""Packet 7D tests: provider/profile registry, dashboard/CLI parity, and the
Qubit branding migration.

Hermetic: profile operations run against a tmp config (state under tmp),
dashboard endpoints run against a WebUI with a fake orchestrator, and no live
service/browser/model is touched. Branding is prepared as a dry-run/backup
plan and verified read-only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from voicebridge.config import Config  # noqa: E402
from voicebridge import profiles as P  # noqa: E402


@pytest.fixture(autouse=True)
def _fast_latency(monkeypatch):
    """Hermetic tests must not perform a real Piper synthesis for latency
    measurement (slow + audio); recorded/default latency is used and the gate
    reports the unmeasured state truthfully."""
    monkeypatch.setenv("VOICE_BRIDGE_LATENCY_FAST", "1")


def _cfg(tmp_path, model="qubit-brain", stt="sherpa", tts="piper",
         voice="en_US-amy-medium"):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(
        "logging:\n  jsonl_path: '" + str(state) + "'\n"
        "intent:\n  local_llm:\n    model: '" + model + "'\n"
        "asr:\n  provider: '" + stt + "'\n"
        "tts:\n  provider: '" + tts + "'\n  voice: '" + voice + "'\n"
        "mic:\n  device_name: ''\n"
        "audio:\n  output_device_name: ''\n")
    return Config.load(p)


# ========================================================================
# Registry + safe defaults
# ========================================================================
def test_registry_has_verified_safe_defaults():
    reg = P.registry()
    kinds = {m.kind for m in reg}
    assert {"brain", "stt", "tts", "voice", "audio"} <= kinds
    by = {m.kind: m for m in reg if m.default}
    assert by["brain"].id == "qubit-brain"
    assert by["stt"].id == "sherpa"
    assert by["tts"].id == "piper"
    assert by["voice"].id == "en_US-amy-medium"
    assert by["audio"].id == "os-default"
    for m in reg:
        assert m.source and m.revision and m.license
        assert m.rollback_to
        gate = m.validate()
        assert gate["state"] == "OK", f"{m.id}: {gate['detail']}"


def test_every_profile_records_all_required_metadata():
    for m in P.registry():
        for field in ("source", "revision", "license", "checksum",
                      "hardware", "languages", "vram_mib", "ram_mib",
                      "latency_ms", "quality_evidence", "safety_evidence",
                      "rollback_to"):
            assert field in m.__dict__, f"{m.id} missing {field}"


def test_profile_list_resolves_active(tmp_path):
    cfg = _cfg(tmp_path)
    out = P.profiles_list(cfg)
    assert out["active"]["brain"] == "qubit-brain"
    assert out["active"]["stt"] == "sherpa"
    assert out["active"]["tts"] == "piper"
    assert out["active"]["voice"] == "en_US-amy-medium"
    assert out["active"]["audio"] == "os-default"
    for p in out["profiles"]:
        assert p["gate"] == "OK"


# ========================================================================
# Manifest validation / gates
# ========================================================================
def test_invalid_manifest_rejected():
    bad = P.ProviderManifest(id="x", kind="brain", source="", revision="",
                             license="", rollback_to="")
    assert bad.validate()["state"] == "FAILED"


def test_checksum_mismatch_rejected():
    m = P.ProviderManifest(id="x", kind="voice", source="s", revision="r",
                           license="MIT", checksum="not-a-sha256",
                           rollback_to="default")
    assert m.validate()["state"] == "FAILED"


def test_vram_overflow_rejected():
    m = P.ProviderManifest(id="big", kind="brain", source="s", revision="r",
                           license="Apache-2.0", vram_mib=8 * 1024,
                           rollback_to="qubit-brain")
    assert m.validate()["state"] == "FAILED"
    assert "ceiling" in m.validate()["detail"]


def test_unsupported_hardware_rejected():
    m = P.ProviderManifest(id="x", kind="brain", source="s", revision="r",
                           license="Apache-2.0", hardware="quantum",
                           rollback_to="qubit-brain")
    assert m.validate()["state"] == "FAILED"


def test_profile_check_gates_typed(tmp_path, monkeypatch):
    from voicebridge import distribution as D
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(D, "gpu_present", lambda: True)
    out = P.profile_check(cfg, "brain", "qubit-brain")
    assert out["state"] in ("OK", "PARTIAL")
    assert "gates" in out and "manifest" in out["gates"]
    # unknown profile -> MISSING
    assert P.profile_check(cfg, "brain", "nope")["state"] == "MISSING"


# ========================================================================
# Apply / reset / rollback (dry-run + explicit)
# ========================================================================
def test_apply_dry_run_does_not_touch_config(tmp_path):
    cfg = _cfg(tmp_path)
    before = Path(tmp_path / "voice.yaml").read_text()
    out = P.apply_profile(cfg, "stt", "sherpa", dry_run=True)
    assert out["dry_run"] is True
    assert Path(tmp_path / "voice.yaml").read_text() == before


def test_apply_requires_confirmation(tmp_path):
    cfg = _cfg(tmp_path)
    out = P.apply_profile(cfg, "stt", "sherpa", dry_run=False,
                          require_confirmation=True)
    assert out["state"] == "DENIED"
    assert "confirmation required" in out["detail"]


def test_apply_unknown_profile_missing(tmp_path):
    cfg = _cfg(tmp_path)
    out = P.apply_profile(cfg, "stt", "nonexistent", dry_run=False,
                          require_confirmation=False)
    assert out["state"] == "MISSING"


def test_apply_same_profile_is_ok_and_backed_up(tmp_path, monkeypatch):
    from voicebridge import distribution as D
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(D, "gpu_present", lambda: True)
    out = P.apply_profile(cfg, "stt", "sherpa", dry_run=False,
                          require_confirmation=False)
    assert out["state"] == "OK"
    assert out["backup"] and Path(out["backup"]).exists()
    # config unchanged (same value) but a backup exists
    assert Config.load(tmp_path / "voice.yaml").get("asr", "provider") == "sherpa"


def test_reset_to_default(tmp_path):
    cfg = _cfg(tmp_path)
    P.apply_profile(cfg, "stt", "sherpa", dry_run=False,
                    require_confirmation=False)
    out = P.reset_profile(cfg, "stt", dry_run=False)
    assert out["state"] == "OK"
    assert Config.load(tmp_path / "voice.yaml").get("asr", "provider") == "sherpa"


def test_rollback_no_backup_resets_to_default(tmp_path):
    cfg = _cfg(tmp_path)
    out = P.rollback_profile(cfg, "stt", dry_run=False)
    assert out["state"] == "OK"


def test_rollback_from_backup_restores_file(tmp_path):
    cfg = _cfg(tmp_path)
    cfg_path = Path(tmp_path / "voice.yaml")
    out = P.apply_profile(cfg, "tts", "piper", dry_run=False,
                          require_confirmation=False)
    bak = Path(out["backup"])
    # modify config, then roll back from the backup
    cfg_path.write_text(cfg_path.read_text().replace("provider: piper",
                                                     "provider: edge"))
    r = P.rollback_profile(cfg, "tts", dry_run=False, backup=bak)
    assert r["state"] == "OK"
    assert "provider: 'piper'" in cfg_path.read_text()


def test_remove_default_denied(tmp_path):
    cfg = _cfg(tmp_path)
    out = P.remove_profile(cfg, "tts", "piper", dry_run=False)
    assert out["state"] == "DENIED"


def test_remove_active_denied(tmp_path):
    cfg = _cfg(tmp_path)
    out = P.remove_profile(cfg, "stt", "sherpa", dry_run=False)
    assert out["state"] == "DENIED"


def test_restart_persistence(tmp_path):
    cfg = _cfg(tmp_path)
    # apply then reload = restart
    P.apply_profile(cfg, "voice", "en_US-amy-medium", dry_run=False,
                    require_confirmation=False)
    cfg2 = Config.load(tmp_path / "voice.yaml")
    assert cfg2.get("tts", "voice") == "en_US-amy-medium"


def test_provider_change_does_not_alter_other_components(tmp_path):
    cfg = _cfg(tmp_path)
    before = P.current_profiles(cfg)
    P.apply_profile(cfg, "voice", "en_US-amy-medium", dry_run=False,
                    require_confirmation=False)
    after = P.current_profiles(cfg)
    # only voice changed; brain/stt/tts/audio untouched
    assert after["brain"]["id"] == before["brain"]["id"]
    assert after["stt"]["id"] == before["stt"]["id"]
    assert after["tts"]["id"] == before["tts"]["id"]
    assert after["audio"]["id"] == before["audio"]["id"]


# ========================================================================
# Branding migration (prepared, reversible)
# ========================================================================
def test_branding_plan_dry_run_is_read_only(tmp_path):
    cfg = _cfg(tmp_path)
    plan = P.branding_plan(cfg, dry_run=True)
    assert plan["dry_run"] is True
    assert plan["new_name"] == "qubit-voicebridge"
    assert any("pyproject.toml" in s for s in plan["file_steps"])
    assert any("systemd" in s for s in plan["file_steps"])
    assert "memory" in plan["preserves"]
    # no backup created on dry-run
    assert plan["backup"] is None
    # dry-run must NOT touch the repo
    assert (REPO / "pyproject.toml").read_text().startswith("[project]")


def _staged_repo(tmp_path: Path) -> Path:
    """Copy only the branding-relevant repository files into tmp so branding
    can be applied + rolled back hermetically without touching the real tree
    or the developer's browser state. The staged copy is normalized to the
    PRE-branding identity (openclaw-voice-bridge) so the apply/verify/rollback
    transition is always exercised regardless of the live repo's state."""
    import shutil
    dest = tmp_path / "staged-repo"
    dest.mkdir()
    # top-level files
    for rel in ("pyproject.toml", "README.md"):
        shutil.copy2(REPO / rel, dest / rel)
    # directories: only the files referenced by BRANDING_FILE_EDITS
    for rel in ("config", "systemd", "scripts", "docs"):
        src = REPO / rel
        dst = dest / rel
        dst.mkdir(parents=True, exist_ok=True)
        for p in sorted(src.rglob("*")):
            if not p.is_file():
                continue
            relp = p.relative_to(src)
            out = dst / relp
            out.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(p, out)
            except OSError:
                continue
    # normalize to the pre-branding identity so the transition is observable
    # (Packet 8S canonical is qubit-voicebridge; the 7D intermediate
    # qubit-voice-agent is also normalized back to the original name)
    for p in dest.rglob("*"):
        if p.is_file() and p.suffix in (".toml", ".md", ".yaml", ".sh", ".service"):
            try:
                text = p.read_text()
            except OSError:
                continue
            text = text.replace("qubit-voicebridge", "openclaw-voice-bridge")
            text = text.replace("qubit-voice-agent", "openclaw-voice-bridge")
            text = text.replace("qubit_voice_agent", "openclaw_voice_bridge")
            text = text.replace("Qubit VoiceBridge", "OpenClaw Voice Bridge")
            text = text.replace("Qubit Voice Agent", "OpenClaw Voice Bridge")
            p.write_text(text)
    # restore the pre-branding systemd unit FILENAME (the edit rules target the
    # old-named template; apply renames it to qubit-voicebridge.service)
    new_unit = dest / "systemd" / "qubit-voicebridge.service"
    mid_unit = dest / "systemd" / "qubit-voice-agent.service"
    old_unit = dest / "systemd" / "openclaw-voice-bridge.service"
    for cand in (new_unit, mid_unit):
        if cand.exists() and not old_unit.exists():
            cand.rename(old_unit)
            break
    return dest


def test_branding_apply_migrates_repo_and_verify_returns_verified(tmp_path):
    cfg = _cfg(tmp_path)
    repo = _staged_repo(tmp_path)
    bdir = tmp_path / "brand-backup"
    plan = P.branding_plan(cfg, dry_run=False, backup_dir=bdir, repo=repo)
    assert plan["backup"] == str(bdir)
    assert Path(bdir).exists()
    assert plan["backup_files"]
    # repo files were actually rewritten to qubit-voicebridge (8S canonical)
    assert 'name = "qubit-voicebridge"' in (repo / "pyproject.toml").read_text()
    # the systemd unit was renamed to the new identifier
    new_unit = repo / "systemd" / "qubit-voicebridge.service"
    assert new_unit.exists()
    assert "Qubit VoiceBridge" in new_unit.read_text()
    assert not (repo / "systemd" / "openclaw-voice-bridge.service").exists()
    # verify returns VERIFIED (not PLANNED) after the migration
    v = P.branding_verify(cfg, repo=repo)
    assert v["state"] == "VERIFIED", v


def test_branding_verify_reports_partial_and_planned(tmp_path):
    cfg = _cfg(tmp_path)
    repo = _staged_repo(tmp_path)
    # untouched repo -> PLANNED (no references migrated)
    v = P.branding_verify(cfg, repo=repo)
    assert v["state"] in ("PLANNED", "PARTIAL")
    assert v["files_remaining"]
    # migrate only pyproject -> PARTIAL
    (repo / "pyproject.toml").write_text(
        (repo / "pyproject.toml").read_text().replace(
            'name = "openclaw-voice-bridge"', 'name = "qubit-voicebridge"'))
    v2 = P.branding_verify(cfg, repo=repo)
    assert v2["state"] == "PARTIAL"
    assert "pyproject.toml" not in v2["files_remaining"]


def test_branding_historical_ref_reported_but_does_not_fail(tmp_path):
    """Approved historical migration text in docs/archive/2026-09-21/AGENT_PROMPT_PACK.md (e.g.
    'rename from openclaw-voice-bridge to qubit-voice-agent') is REPORTED by
    the verifier but never fails branding, and apply preserves it."""
    cfg = _cfg(tmp_path)
    repo = _staged_repo(tmp_path)
    hist = repo / "docs/archive/2026-09-21" / "AGENT_PROMPT_PACK.md"
    hist_text = hist.read_text()
    assert "openclaw-voice-bridge" in hist_text   # pre-staged repo has the text
    bdir = tmp_path / "b"
    P.branding_plan(cfg, dry_run=False, backup_dir=bdir, repo=repo)
    v = P.branding_verify(cfg, repo=repo)
    assert v["state"] == "VERIFIED", v
    # the historical reference is REPORTED, not hidden
    assert v["historical_references"].get("docs/archive/2026-09-21/AGENT_PROMPT_PACK.md", 0) > 0
    # and the intentional historical migration text was preserved (not rewritten)
    assert "openclaw-voice-bridge" in hist.read_text()


def test_branding_unapproved_old_name_reference_still_fails(tmp_path):
    """Any old-name reference in an executable package/service/config/launcher
    file still fails branding (PARTIAL), even when historical refs exist."""
    cfg = _cfg(tmp_path)
    repo = _staged_repo(tmp_path)
    bdir = tmp_path / "b"
    P.branding_plan(cfg, dry_run=False, backup_dir=bdir, repo=repo)
    # reintroduce an UNapproved old-name reference in an executable file
    pp = repo / "pyproject.toml"
    pp.write_text(pp.read_text().replace('name = "qubit-voicebridge"',
                                         'name = "openclaw-voice-bridge"'))
    v = P.branding_verify(cfg, repo=repo)
    assert v["state"] == "PARTIAL", v
    assert "pyproject.toml" in v["files_remaining"]


def test_branding_cli_verify_succeeds_on_current_repository(tmp_path, capsys):
    """`voicebridge branding verify` on the CURRENT repository returns success
    (rc 0, state VERIFIED) now that the prompt-pack historical migration text
    is allowlisted, while still reporting the historical reference."""
    from voicebridge.cli import main as cli
    cfg_path = tmp_path / "voice.yaml"
    cfg_path.write_text("logging:\n  jsonl_path: '" +
                        str(tmp_path / "state") + "'\n")
    rc = cli(["branding", "verify", "--config", str(cfg_path)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0, out
    assert out["state"] == "VERIFIED", out
    assert out["historical_references"]   # reported, not hidden


def test_branding_staged_apply_verify_rollback_cycle(tmp_path):
    """Staged apply -> verify (VERIFIED) -> rollback (OK) still works after the
    historical-reference allowlist change."""
    cfg = _cfg(tmp_path)
    repo = _staged_repo(tmp_path)
    bdir = tmp_path / "b"
    before = (repo / "pyproject.toml").read_text()
    P.branding_plan(cfg, dry_run=False, backup_dir=bdir, repo=repo)
    assert (repo / "pyproject.toml").read_text() != before
    v = P.branding_verify(cfg, repo=repo)
    assert v["state"] == "VERIFIED", v
    out = P.branding_rollback(cfg, bdir, repo=repo, home=tmp_path / "home")
    assert out["state"] == "OK"
    assert out["restored"]
    assert (repo / "pyproject.toml").read_text() == before


def test_branding_rollback_restores_full_backup(tmp_path):
    cfg = _cfg(tmp_path)
    repo = _staged_repo(tmp_path)
    bdir = tmp_path / "brand-backup"
    before = (repo / "pyproject.toml").read_text()
    P.branding_plan(cfg, dry_run=False, backup_dir=bdir, repo=repo)
    assert (repo / "pyproject.toml").read_text() != before
    out = P.branding_rollback(cfg, bdir, repo=repo, home=tmp_path / "home")
    assert out["state"] == "OK"
    assert out["restored"]
    assert (repo / "pyproject.toml").read_text() == before


# ========================================================================
# CLI parity
# ========================================================================
def test_cli_profile_list_show_check(tmp_path, capsys):
    from voicebridge.cli import main as cli
    cfg_path = tmp_path / "voice.yaml"
    cfg_path.write_text("logging:\n  jsonl_path: '" +
                        str(tmp_path / "state") + "'\n")
    rc = cli(["profile", "list", "--json", "--config", str(cfg_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["active"]["brain"] == "qubit-brain"
    rc = cli(["profile", "show", "brain", "--config", str(cfg_path)])
    assert rc == 0
    capsys.readouterr()  # drain the show output
    rc = cli(["profile", "check", "tts", "--config", str(cfg_path)])
    assert rc in (0, 1)
    out = json.loads(capsys.readouterr().out)
    assert "gates" in out


def test_cli_profile_select_dry_run_then_apply(tmp_path):
    from voicebridge.cli import main as cli
    cfg_path = tmp_path / "voice.yaml"
    cfg_path.write_text("logging:\n  jsonl_path: '" +
                        str(tmp_path / "state") + "'\n"
                        "tts:\n  provider: piper\n  voice: en_US-amy-medium\n")
    rc = cli(["profile", "select", "voice", "en_US-amy-medium",
              "--config", str(cfg_path)])
    assert rc == 0  # dry-run
    rc = cli(["profile", "select", "voice", "en_US-amy-medium", "--apply",
              "--confirm", "--config", str(cfg_path)])
    assert rc == 0


def test_cli_profile_reset_and_rollback(tmp_path):
    from voicebridge.cli import main as cli
    cfg_path = tmp_path / "voice.yaml"
    cfg_path.write_text("logging:\n  jsonl_path: '" +
                        str(tmp_path / "state") + "'\n"
                        "asr:\n  provider: sherpa\n")
    rc = cli(["profile", "reset", "stt", "--apply", "--config", str(cfg_path)])
    assert rc == 0
    rc = cli(["profile", "rollback", "stt", "--apply", "--config", str(cfg_path)])
    assert rc == 0


def test_cli_profile_install_invalid_manifest(tmp_path):
    from voicebridge.cli import main as cli
    cfg_path = tmp_path / "voice.yaml"
    cfg_path.write_text("logging:\n  jsonl_path: '" +
                        str(tmp_path / "state") + "'\n")
    bad = tmp_path / "bad-manifest.json"
    bad.write_text(json.dumps({"id": "x", "kind": "brain"}))
    rc = cli(["profile", "install", str(bad), "--config", str(cfg_path)])
    assert rc == 2


def test_cli_profile_install_valid_manifest(tmp_path, monkeypatch):
    from voicebridge.cli import main as cli
    cfg_path = tmp_path / "voice.yaml"
    cfg_path.write_text("logging:\n  jsonl_path: '" +
                        str(tmp_path / "state") + "'\n")
    man = {
        "id": "piper-alt", "kind": "voice", "source": "test-fixture",
        "revision": "abc123", "license": "MIT", "checksum": "",
        "hardware": "cpu", "languages": ["en"], "vram_mib": 0, "ram_mib": 0,
        "latency_ms": 0.0, "quality_evidence": "test", "safety_evidence": "test",
        "rollback_to": "en_US-amy-medium",
        "config_path": ["tts", "voice"], "config_value": "piper-alt",
    }
    mf = tmp_path / "manifest.json"
    mf.write_text(json.dumps(man))
    rc = cli(["profile", "install", str(mf), "--config", str(cfg_path)])
    assert rc == 0
    # manifest is now in the registry (state under tmp)
    reg = tmp_path / "state" / "profiles.json"
    assert reg.exists()
    data = json.loads(reg.read_text())
    assert any(p["id"] == "piper-alt" for p in data["profiles"])


def test_cli_branding_commands(tmp_path, capsys):
    from voicebridge.cli import main as cli
    cfg_path = tmp_path / "voice.yaml"
    cfg_path.write_text("logging:\n  jsonl_path: '" +
                        str(tmp_path / "state") + "'\n")
    rc = cli(["branding", "plan", "--config", str(cfg_path)])
    assert rc == 0
    rc = cli(["branding", "verify", "--config", str(cfg_path)])
    # the LIVE repo is branded -> VERIFIED (rc 0). The staged-repo section
    # below exercises the actual pre->post transition hermetically.
    assert rc == 0
    capsys.readouterr()
    # apply + rollback against a staged repo copy (never the real tree)
    repo = _staged_repo(tmp_path)
    bdir = tmp_path / "b"
    rc = cli(["branding", "plan", "--apply", "--backup-dir", str(bdir),
              "--repo", str(repo), "--config", str(cfg_path)])
    assert rc == 0
    rc = cli(["branding", "verify", "--config", str(cfg_path),
              "--repo", str(repo)])
    assert rc == 0  # VERIFIED
    capsys.readouterr()
    rc = cli(["branding", "rollback", "--backup-dir", str(bdir),
              "--repo", str(repo), "--home", str(tmp_path / "cli-home"),
              "--config", str(cfg_path)])
    assert rc == 0


# ========================================================================
# Dashboard / API parity (WebUI with fake orch)
# ========================================================================
class _FakeOrch:
    """Minimal orchestrator with a real Config for the profile endpoints."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.task = type("T", (), {"active": False})()
        self._route = "conversation"
        self._t0 = __import__("time").time()
        self._web_active = 0
        self._tts = None
        self.shared = None
        self.sticky_route = None
        self.learning = None

    def _needs_answer(self):
        return False


def _make_ui(tmp_path, monkeypatch):
    from voicebridge.webui import WebUI
    cfg = _cfg(tmp_path)
    # configure a dashboard token so the auth gate is actually enforced
    cfg.data.setdefault("webui", {})["token"] = "test-token"
    orch = _FakeOrch(cfg)
    ui = WebUI(orch, host="127.0.0.1", port=0, state_dir=tmp_path)
    ui.start()
    return ui, orch, cfg


def _get(ui, path, token="test-token"):
    import urllib.request
    req = urllib.request.Request(
        f"http://127.0.0.1:{ui._server.server_port}{path}",
        headers={"X-Api-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        finally:
            e.close()


def _post(ui, path, body, token="test-token"):
    import urllib.request
    req = urllib.request.Request(
        f"http://127.0.0.1:{ui._server.server_port}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Api-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        finally:
            e.close()


def test_api_profiles_requires_auth(tmp_path, monkeypatch):
    ui, _, _ = _make_ui(tmp_path, monkeypatch)
    try:
        import urllib.error
        import urllib.request
        # wrong token -> 401 (the loopback+token auth gate is enforced)
        req = urllib.request.Request(
            f"http://127.0.0.1:{ui._server.server_port}/api/profiles",
            headers={"X-Api-Token": "WRONG-TOKEN"})
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req, timeout=5)
        assert ei.value.code == 401
        ei.value.close()                               # release the socket
    finally:
        ui.stop()


def test_api_profiles_list_and_show(tmp_path, monkeypatch):
    # `/api/profiles` serves the reference profile registry (one authority:
    # qubit_cli.profiles), not the provider/brain registry -- see
    # tests/test_qubit_api_parity_wave1.py::_seed_profile ("the one registry
    # /api/profiles serves") and the reference web_routers/profiles.py
    # list_profiles_endpoint. `voicebridge.profiles` remains the provider
    # manifest registry (asserted directly by test_profile_list_resolves_active).
    ui, _, cfg = _make_ui(tmp_path, monkeypatch)
    try:
        from voicebridge import qubit_compat as HC
        active = HC._profiles_module(cfg).get_active_profile() or "default"

        st, body = _get(ui, "/api/profiles")
        assert st == 200
        assert isinstance(body["profiles"], list)
        assert body["active"]["id"] == active

        st, body = _get(ui, "/api/profiles?kind=brain")
        assert st == 200
        assert body["active"]["id"] == active
    finally:
        ui.stop()


def test_api_profiles_check(tmp_path, monkeypatch):
    ui, _, _ = _make_ui(tmp_path, monkeypatch)
    try:
        st, body = _get(ui, "/api/profiles/check?kind=tts")
        assert st == 200
        assert "gates" in body
        st, body = _get(ui, "/api/profiles/check?kind=bogus")
        assert st == 400
    finally:
        ui.stop()


def test_api_profiles_apply_requires_confirmation(tmp_path, monkeypatch):
    ui, _, _ = _make_ui(tmp_path, monkeypatch)
    try:
        st, body = _post(ui, "/api/profiles/apply",
                         {"kind": "stt", "profile": "sherpa",
                          "dry_run": False, "confirm": False})
        assert body["state"] == "DENIED"
        # with confirmation (same value) -> OK
        st, body = _post(ui, "/api/profiles/apply",
                         {"kind": "stt", "profile": "sherpa",
                          "dry_run": False, "confirm": True})
        assert body["state"] == "OK"
    finally:
        ui.stop()


def test_api_profiles_reset_and_rollback(tmp_path, monkeypatch):
    ui, _, _ = _make_ui(tmp_path, monkeypatch)
    try:
        st, body = _post(ui, "/api/profiles/reset",
                         {"kind": "voice", "apply": True})
        assert body["state"] == "OK"
        st, body = _post(ui, "/api/profiles/rollback",
                         {"kind": "voice", "apply": True})
        assert body["state"] == "OK"
    finally:
        ui.stop()


def test_api_audio_and_permissions_and_device(tmp_path, monkeypatch):
    from voicebridge.audio import settings as AS
    import sounddevice as sd
    ui, _, _ = _make_ui(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(sd, "query_devices", lambda: [
            {"name": "pulse", "max_input_channels": 32,
             "max_output_channels": 32, "hostapi": 0}])
        monkeypatch.setattr(sd, "query_hostapis", lambda: [{"name": "Pulse"}])
        monkeypatch.setattr(sd, "default", type("D", (), {"device": (0, 0)})())
        st, body = _get(ui, "/api/audio/status")
        assert st == 200 and "mic" in body
        st, body = _get(ui, "/api/audio/list")
        assert st == 200 and "inputs" in body
        st, body = _post(ui, "/api/audio/reset", {})
        assert st == 200 and body["state"] == "OK"
        st, body = _get(ui, "/api/permissions")
        assert st == 200 and "items" in body
        st, body = _get(ui, "/api/device/status")
        assert st == 200
        st, body = _post(ui, "/api/device/enroll", {"confirm": True})
        assert st == 200 and body["enrolled"] is True
    finally:
        ui.stop()


def test_api_branding_and_export_state(tmp_path, monkeypatch):
    ui, _, _ = _make_ui(tmp_path, monkeypatch)
    try:
        st, body = _get(ui, "/api/branding")
        assert st == 200
        assert body["new_name"] == "qubit-voicebridge"
        assert body["state"] in ("VERIFIED", "PARTIAL", "PLANNED")
        # export-state requires enrollment + passphrase; missing passphrase
        # (after enroll) -> DENIED
        _post(ui, "/api/device/enroll", {"confirm": True})
        st, body = _post(ui, "/api/export-state", {})
        assert body["state"] == "DENIED"
    finally:
        ui.stop()


def test_settings_page_served(tmp_path, monkeypatch):
    ui, _, _ = _make_ui(tmp_path, monkeypatch)
    try:
        # Hermes-first: /settings aliases into the Hermes shell (Config).
        import http.client
        conn = http.client.HTTPConnection(
            "127.0.0.1", ui._server.server_port, timeout=5)
        conn.request("GET", "/settings")
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 302, resp.status
        assert resp.getheader("Location") == "/config"
        # The labelled /classic/ fallback still serves the static page.
        import urllib.request
        with urllib.request.urlopen(
                f"http://127.0.0.1:{ui._server.server_port}/classic/settings",
                timeout=5) as r:
            html = r.read().decode()
        assert "Qubit VoiceBridge" in html
        assert "/api/profiles" in html
    finally:
        ui.stop()


def test_dashboard_never_leaks_secrets_or_paths(tmp_path, monkeypatch):
    ui, _, _ = _make_ui(tmp_path, monkeypatch)
    try:
        for path in ("/api/profiles", "/api/profiles/check?kind=brain",
                     "/api/permissions", "/api/device/status",
                     "/api/audio/status", "/api/branding"):
            st, body = _get(ui, path)
            blob = json.dumps(body)
            assert "/home/" not in blob, f"{path} leaked a filesystem path"
            assert "hunter2" not in blob and "token=" not in blob
    finally:
        ui.stop()


def test_profiles_page_has_no_frontend_only_state(tmp_path, monkeypatch):
    html = (REPO / "frontend" / "settings.html").read_text()
    # every control calls a backend endpoint
    for ep in ("/api/profiles", "/api/audio/", "/api/permissions",
               "/api/device/", "/api/export-state", "/api/import-state",
               "/api/profiles/upgrade", "/api/profiles/remove",
               "/api/branding", "/api/approvals", "/api/reports",
               "/api/status"):
        assert ep in html, f"missing backend endpoint {ep} in settings page"
# no browser storage as a source of truth for provider state — settings.html
        # never reads token/state storage directly; it goes through the shared
        # /static/vb.js helper (Packet 8E serving fix).
        assert "/static/vb.js" in html
        assert "localStorage" not in html
    # all untrusted text is escaped before render
    assert "function esc(s)" in html
    assert "esc(" in html
    # a real confirmation dialog must gate every side-effecting action
    assert "confirmDialog(" in html
    assert "window.confirm(" in html
    # no placeholder/demo controls; real import + per-component upgrade exist
    assert "demo" not in html.lower()
    assert "import-file" in html
    assert "upgradeProfile(" in html
    # failure states are interpreted, never a generic success
    assert "no change made" in html
    assert "UNAVAILABLE" in html


def test_api_import_state_preview_then_apply(tmp_path, monkeypatch):
    """Dashboard import is a real backend round-trip: preview then apply with
    enrollment + passphrase gates."""
    from voicebridge import portable as P
    ui, orch, cfg = _make_ui(tmp_path, monkeypatch)
    try:
        # enroll so the import gate passes
        _post(ui, "/api/device/enroll", {"confirm": True})
        # export a bundle via the real exporter
        pw = "import-pass"
        b64 = __import__("base64").b64encode(P.export_bundle(cfg, pw)).decode()
        # preview (dry-run)
        st, body = _post(ui, "/api/import-state",
                         {"bundle_b64": b64, "passphrase": pw})
        assert st == 200 and body["dry_run"] is True
        assert body["actions"]
        # apply
        st, body = _post(ui, "/api/import-state",
                         {"bundle_b64": b64, "passphrase": pw, "apply": True})
        assert st == 200 and body["state"] == "OK"
        assert body["backup"]
    finally:
        ui.stop()


def test_api_import_state_rejects_wrong_passphrase(tmp_path, monkeypatch):
    from voicebridge import portable as P
    ui, orch, cfg = _make_ui(tmp_path, monkeypatch)
    try:
        _post(ui, "/api/device/enroll", {"confirm": True})
        b64 = __import__("base64").b64encode(
            P.export_bundle(cfg, "right")).decode()
        st, body = _post(ui, "/api/import-state",
                         {"bundle_b64": b64, "passphrase": "WRONG"})
        assert st == 403 and body["state"] in ("FAILED", "DENIED")
    finally:
        ui.stop()


def test_api_profiles_upgrade_performs_real_upgrade(tmp_path, monkeypatch):
    """Upgrade is a real operation: it re-verifies gates and refreshes the
    registry manifest metadata (checksum/revision), not just a check."""
    from voicebridge import profiles as P
    from voicebridge import distribution as D
    ui, orch, cfg = _make_ui(tmp_path, monkeypatch)
    try:
        # register a non-default voice manifest so upgrade has something to do
        man = P.ProviderManifest(
            id="amy-2", kind="voice", source="test", revision="r1",
            license="MIT", checksum="", hardware="cpu", languages=["en"],
            vram_mib=0, ram_mib=0, latency_ms=250.0,
            quality_evidence="docs/archive/2026-09-21/AUDIO_SETUP.md",
            safety_evidence="docs/archive/2026-09-21/OPERATIONS_7A.md",
            rollback_to="en_US-amy-medium",
            config_path=("tts", "voice"), config_value="amy-2")
        P.install_manifest(cfg, man)
        st, body = _post(ui, "/api/profiles/upgrade",
                         {"kind": "voice", "profile": "amy-2", "apply": True})
        assert body["state"] == "OK"
        assert "upgraded" in body["action"]
    finally:
        ui.stop()


def test_api_profiles_remove_non_default_real(tmp_path, monkeypatch):
    """Removal is real (not a demo): a registered non-default profile is
    actually removed via the backend."""
    from voicebridge import profiles as P
    ui, orch, cfg = _make_ui(tmp_path, monkeypatch)
    try:
        man = P.ProviderManifest(
            id="amy-2", kind="voice", source="test", revision="r1",
            license="MIT", checksum="", hardware="cpu", languages=["en"],
            vram_mib=0, ram_mib=0, latency_ms=250.0,
            quality_evidence="docs/archive/2026-09-21/AUDIO_SETUP.md",
            safety_evidence="docs/archive/2026-09-21/OPERATIONS_7A.md",
            rollback_to="en_US-amy-medium",
            config_path=("tts", "voice"), config_value="amy-2")
        P.install_manifest(cfg, man)
        st, body = _post(ui, "/api/profiles/remove",
                         {"kind": "voice", "profile": "amy-2", "apply": True})
        assert st == 200 and body["state"] == "OK"
        assert body["removed"] == "amy-2"
        # registry no longer contains it
        reg = tmp_path / "state" / "profiles.json"
        assert reg.exists()
        data = json.loads(reg.read_text())
        assert all(p["id"] != "amy-2" for p in data["profiles"])
    finally:
        ui.stop()


def test_api_profiles_apply_confirmation_gate(tmp_path, monkeypatch):
    """A side-effecting profile change is DENIED without confirmation; the
    dashboard confirmation dialog maps to the backend confirm flag."""
    ui, orch, cfg = _make_ui(tmp_path, monkeypatch)
    try:
        # a non-default (replacement) profile whose gates are not all OK must
        # never activate — the confirmation gate is separate from gate checks
        st, body = _post(ui, "/api/profiles/apply",
                         {"kind": "voice", "profile": "en_US-amy-medium",
                          "dry_run": False, "confirm": False})
        # confirm:false with the default (already active) -> DENIED because the
        # backend requires confirmation for the side effect
        assert body["state"] in ("DENIED", "FAILED", "PARTIAL")
        assert body["state"] != "OK"
    finally:
        ui.stop()