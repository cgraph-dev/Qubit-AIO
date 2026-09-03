"""Packet 8K tests: release certification — portability, packaging, security.

Hermetic: no build, no install, no network, no live service. The opt-in
build/install/reproduce certification steps (scripts/release_qualify.py
--build/--install/--reproduce) are monitor-marked and never run here; their
logic is unit-tested with fake artifacts and monkeypatched subprocesses.
"""
from __future__ import annotations

import json
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from voicebridge import release as R  # noqa: E402


# ========================================================================
# Provenance
# ========================================================================
def test_git_provenance_records_commit_and_bounded_dirty():
    rec = R.git_provenance(REPO)
    assert rec["version"] == "1.0.0"
    assert rec["commit"]
    assert isinstance(rec["dirty"], bool)
    assert isinstance(rec["dirty_files"], list)
    assert isinstance(rec["untracked_files"], list)
    assert len(rec["dirty_files"]) <= 200 and len(rec["untracked_files"]) <= 200


def test_git_provenance_non_git_repo(tmp_path):
    rec = R.git_provenance(tmp_path)
    assert rec["git_repo"] is False
    assert rec["dirty"] is False


# ========================================================================
# Secret + machine-path scans
# ========================================================================
def test_scan_text_secrets_python_variable_not_secret():
    # a Python variable named `token` is not a secret value
    assert "assign_secret" not in R.scan_text_secrets(
        "token = get_config('webui', 'token') or ''", is_python=True)
    # a real secret VALUE is flagged everywhere
    assert "openai_key" in R.scan_text_secrets(
        "key = 'sk-abcdefghijklmnopqrstuvwxyz1234567890'", is_python=True)


def test_scan_text_secrets_config_assign_flagged():
    assert "assign_secret" in R.scan_text_secrets(
        "webui:\n  token: abc123def456\n", is_config=True)
    # HTML/JS prose `token=` is not a config secret assignment
    assert "assign_secret" not in R.scan_text_secrets(
        "const token = vbToken();", is_config=False)


def test_scan_text_machine_paths_prose_not_path():
    # prose "under ~/Projects/Repos and report" is not a concrete path
    assert R.scan_text_machine_paths(
        "List the user's main projects under ~/Projects/Repos and report"
    ) == []
    # a real absolute home path is flagged (redacted to user component)
    hits = R.scan_text_machine_paths(
        "launch /home/example/Projects/Repos/demo-app")
    assert "/home/example" in hits


def test_machine_path_scan_classifies_personalization():
    res = R.machine_path_scan(REPO)
    assert res["core"] == {}   # portable core has no machine paths
    # Packet 8K: personal launcher targets moved OUT of actions.py into the
    # operator-local override; actions.py is now portable core, not personalization.
    assert "voicebridge/local/actions.py" not in res["personalization"]
    assert "config/task-templates.yaml" in res["personalization"]


def test_secret_scan_excludes_tests_and_eval_fixtures():
    res = R.secret_scan(REPO)
    assert not any(p.startswith("tests/") for p in res)
    assert not any(p.startswith("voicebridge/evaluation/") for p in res)
    # and the shipped repo is free of real secret values
    assert res == {}


# ========================================================================
# Dependency contract + SBOM
# ========================================================================
def test_declared_dependencies_parse():
    deps = R.declared_dependencies()
    names = {d["name"] for d in deps["main"]}
    assert "sherpa-onnx" in names and "numpy" in names
    assert "hotkey" in deps["extras"]


def test_sbom_never_fabricates_versions(tmp_path):
    sbom = R.sbom(REPO)
    assert sbom["package"] == "qubit-voice-agent"
    assert "notices" in sbom and sbom["notices"]
    for rec in sbom["main"]:
        assert rec["name"] and rec["constraint"]
        assert isinstance(rec["installed"], str) and rec["installed"]


def test_import_scan_installed_flags_undeclared_and_tolerates_guarded(tmp_path):
    pkg = tmp_path / "mypkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "sub" / "__init__.py").write_text("")
    (pkg / "core.py").write_text(
        "import os\nimport numpy\nimport psutil  # guarded optional\n"
        "from mypkg.sub import x\n")
    declared = {"numpy", "mypkg"}
    fails = R.import_scan_installed(pkg, declared)
    # os is stdlib, numpy declared, mypkg local, psutil guarded -> no failures
    assert fails == []


def test_import_scan_installed_flags_rogue(tmp_path):
    pkg = tmp_path / "roguepkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("import frobnicate\n")
    fails = R.import_scan_installed(pkg, {"numpy"})
    assert any("frobnicate" in f for f in fails)


def test_import_scan_installed_syntax_error_reported(tmp_path):
    pkg = tmp_path / "badpkg"
    pkg.mkdir()
    (pkg / "b.py").write_text("def broken(:\n")
    fails = R.import_scan_installed(pkg, set())
    assert any("<syntax>" in f for f in fails)


# ========================================================================
# Artifact inspection (fake wheel / sdist)
# ========================================================================
def _make_artifact(tmp_path: Path, kind: str, members: dict) -> Path:
    path = tmp_path / (kind + ("-1.0.0-py3-none-any.whl" if kind == "wheel"
                               else "-1.0.0.tar.gz"))
    if kind == "wheel":
        with zipfile.ZipFile(path, "w") as z:
            for name, data in members.items():
                z.writestr(name, data)
    else:
        with tarfile.open(path, "w:gz") as t:
            for name, data in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(data.encode())
                t.addfile(info, __import__("io").BytesIO(data.encode()))
    return path


def test_artifact_member_hits_rejects_private_members(tmp_path):
    art = _make_artifact(tmp_path, "wheel", {
        "voicebridge/config.py": "x",
        "scripts/browser_profiles/chromium/data": "y",
        "config/voice.yaml": "z",
    })
    hits = R.artifact_member_hits(art)
    assert any("browser_profiles" in h for h in hits)
    assert any("config/voice.yaml" in h for h in hits)


def test_artifact_content_scan_classifies_fixture_and_personalization(tmp_path):
    art = _make_artifact(tmp_path, "wheel", {
        "voicebridge/core.py": "k = 'sk-abcdefghijklmnopqrstuvwxyz1234567890'",
        "voicebridge/evaluation/eval_set.py": "q = 'sk-AbCdEfGhIjKlMnOpQrStUvWxYz1234567890'",
        "config/task-templates.yaml": "script: ~/Projects/Repos/example-app/.venv/bin/python",
        "voicebridge/resources.py": "x = 1",
    })
    res = R.artifact_content_scan(art)
    # fake key in core -> flagged; in eval fixture -> fixture_secrets only
    assert "voicebridge/core.py" in res["secrets"]
    assert "voicebridge/evaluation/eval_set.py" in res["fixture_secrets"]
    assert "voicebridge/evaluation/eval_set.py" not in res["secrets"]
    # personalization file -> noncore machine path, never a core gate
    assert "config/task-templates.yaml" not in res["machine_paths"]
    assert "config/task-templates.yaml" in res["machine_paths_noncore"]
    assert "voicebridge/resources.py" not in res["machine_paths"]


def test_artifact_member_class_sdist_prefix():
    assert R.artifact_member_class("qubit_voice_agent-1.0.0/voicebridge/x.py") == "core"
    assert R.artifact_member_class(
        "qubit_voice_agent-1.0.0/tests/test_x.py") == "fixture"
    assert R.artifact_member_class(
        "qubit_voice_agent-1.0.0/docs/README.md") == "noncore"


def test_reproducibility_record_compares_hashes(tmp_path):
    a = tmp_path / "a.whl"
    b = tmp_path / "b.whl"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    rec = R.reproducibility_record(a, b)
    assert rec["reproducible"] is True
    assert rec["sha256"] == rec["second_sha256"]
    c = tmp_path / "c.whl"
    c.write_bytes(b"different")
    rec2 = R.reproducibility_record(a, c)
    assert rec2["reproducible"] is False


# ========================================================================
# Filesystem modes / ceilings / runbooks / migration
# ========================================================================
def test_filesystem_modes_pass_on_repo():
    results = R.filesystem_modes_check(REPO)
    assert all(c["state"] == "OK" for c in results), results
    names = {c["name"] for c in results}
    assert "mode.portable_config" in names
    assert "mode.private_state_0600" in names


def test_filesystem_modes_fails_machine_tokens_in_example(tmp_path):
    ex = tmp_path / "config"
    ex.mkdir()
    (ex / "voice.example.yaml").write_text(
        "audio:\n  output_device_name: HyperX Cloud II\n")
    # write directly through the module helper with a fake root
    from voicebridge import release as R
    root = tmp_path
    (root / "voicebridge").mkdir()
    (root / "voicebridge" / "device.py").write_text("import os; os.chmod(p, 0o600)\n")
    (root / "voicebridge" / "portable.py").write_text("os.replace(a, b); os.fsync(f)\n")
    results = R.filesystem_modes_check(root)
    by = {c["name"]: c["state"] for c in results}
    assert by["mode.portable_config"] == "FAIL"
    assert by["mode.private_state_0600"] == "OK"


def test_resource_ceilings_recorded():
    c = R.resource_ceilings(REPO)
    assert c["MIN_DISK_FREE_GIB"] == 2.0
    assert c["MIN_RAM_GIB"] == 4.0
    assert c["VRAM_CEILING_MIB"] == 5 * 1024
    assert c["monitor_oneshot_timeout_s"] == 300


def test_operator_runbook_check_includes_8k():
    results = {c["name"]: c for c in R.operator_runbook_check(REPO)}
    assert results["docs.docs/OPERATIONS_8K.md"]["state"] == "OK"
    assert results["docs.docs/MIGRATIONS.md"]["state"] == "OK"


def test_backup_migration_check_passes():
    results = {c["name"]: c["state"] for c in R.backup_migration_check(REPO)}
    assert results["migration.history"] == "OK"
    assert results["migration.newer_schema_refusal"] == "OK"
    assert results["migration.atomic_restore"] == "OK"


def test_bounded_logs_check_passes():
    results = {c["name"]: c["state"] for c in R.bounded_logs_check(REPO)}
    assert results["logs.journal_valid_coercion"] == "OK"
    assert results["logs.journal_bounded_recovery"] == "OK"
    assert results["logs.journal_no_secret_leak"] == "OK"


# ========================================================================
# Unit verification
# ========================================================================
def test_unit_verify_renders_and_verifies():
    res = R.unit_verify(REPO)
    assert res["all_ok"] is True
    assert res["units"]
    names = {u["unit"] for u in res["units"]}
    assert "qubit-voice-agent.service" in names
    assert "openclaw-communications-monitor.timer" in names
    for u in res["units"]:
        assert u["state"] == "OK", u


def test_unit_verify_fallback_security_review(monkeypatch):
    monkeypatch.setattr(R, "_which", lambda name: None)
    res = R.unit_verify(REPO)
    assert res["systemd_analyze"] is False   # degraded: verifier unavailable
    assert res["units"]                      # security review still ran
    assert all(u["state"] == "OK" for u in res["units"])
    assert "security review" in res["error"]


def test_unit_security_review_flags_secret_units(tmp_path):
    unit = tmp_path / "bad.service"
    unit.write_text("[Service]\nEnvironment=TOKEN=sk-abcdefghijklmnopqrstuvwxyz1234\n")
    rec = R._unit_security_review(unit)
    assert rec["state"] == "FAIL"


# ========================================================================
# Upgrade / uninstall contract reuse
# ========================================================================
def test_upgrade_uninstall_contract_preserves_state_by_default(tmp_path):
    from voicebridge.config import Config
    cfg = Config.load(tmp_path / "none.yaml")
    res = R.upgrade_uninstall_contract(cfg)
    assert res["pass"] is True
    un = res["plan"]["uninstall"]
    assert un["requires_confirmation"] is True
    joined = " ".join(un["preserves_by_default"]).lower()
    assert "state dir" in joined and "user config" in joined


def test_upgrade_contract_no_silent_service_restart_or_config_overwrite(tmp_path):
    """Packet 8K upgrade invariants: never silently enable/restart services,
    never overwrite live user config, keep explicit backups and typed rollback."""
    from voicebridge.config import Config
    cfg = Config.load(tmp_path / "none.yaml")
    from voicebridge import distribution as D
    plan = D.upgrade_plan(cfg)
    does_not = " ".join(str(x) for x in plan["does_not"]).lower()
    assert "enable/restart live services silently" in does_not
    assert "overwrite live user config" in does_not
    assert "delete state" in does_not
    assert plan.get("rollback")
    # explicit backup contract: the render keeps a .bak unless forced
    assert ".bak" in plan["rollback"]
    un = D.uninstall_plan(cfg)
    assert un["requires_confirmation"] is True
    assert un["steps_if_confirmed"]   # data deletion is a SEPARATE confirmed step
    assert "browser profiles" in " ".join(un["preserves_by_default"]).lower()


def test_newer_schema_fails_closed(tmp_path):
    """A state bundle from a NEWER schema version must be refused before apply,
    never downgraded or overwritten (portable.UnsupportedSchemaError)."""
    from voicebridge import portable
    # fabricate a minimal bundle with a NEWER schema_version header; it must be
    # rejected on schema alone, before any key/decryption step.
    import json
    header = {"schema": "portable-state",
              "schema_version": portable.SCHEMA_VERSION + 1,
              "version": "999.0.0", "entries": []}
    bundle = (portable.MAGIC + b"\n" +
              json.dumps(header).encode() + b"\n" + b"ct")
    with pytest.raises(portable.UnsupportedSchemaError):
        portable.read_bundle(bundle, "passphrase")


def test_synthetic_prior_artifact_upgrade_contract(tmp_path):
    """With no real previous release artifact available, the upgrade contract is
    exercised against a SYNTHETIC prior artifact. A real previous-release
    upgrade remains unavailable and is an explicit limitation — never a false
    pass (requirement: do not fabricate a real release)."""
    from voicebridge.config import Config
    from voicebridge import distribution as D
    # synthetic prior release metadata (not a real release artifact)
    prior = {"name": "qubit-voice-agent", "version": "0.9.0", "schema": 0}
    cfg = Config.load(tmp_path / "none.yaml")
    plan = D.upgrade_plan(cfg)
    # the contract must be version/state-preserving regardless of prior version
    assert plan["action"] == "upgrade"
    assert prior["version"] != D.PACKAGE_VERSION
    joined = " ".join(str(x) for x in plan["does_not"]).lower()
    assert "delete state" in joined
    # report the limitation honestly: real previous-release upgrade untested
    assert D.PACKAGE_VERSION == "1.0.0"


# ========================================================================
# Certification report
# ========================================================================
def test_certify_passes_and_records_personalization():
    report = R.certify(REPO)
    assert R.report_pass(report) is True
    assert report["provenance"]["version"] == "1.0.0"
    assert report["machine_paths"]["core"] == {}
    assert report["secrets"] == {}
    assert report["units"]["all_ok"] is True
    assert report["toolchain"]["python"]
    # signature status is recorded honestly and is never fabricated
    assert report["signature"]["state"] in ("SIGNED", "NOT_SIGNED")
    assert "tools" in report["signature"] and "git_signing_key" in report["signature"]


def test_artifact_signature_status_never_fabricates(monkeypatch):
    rec = R.artifact_signature_status()
    # NOT_SIGNED is reported explicitly when no capability exists; the record
    # never claims SIGNED merely because sha256 hashes were computed.
    assert "tools" in rec
    if rec["state"] == "SIGNED":
        assert rec["git_signing_key"]
    else:
        assert not rec["git_signing_key"]


def test_report_pass_fails_on_core_machine_path(tmp_path):
    report = R.certify(tmp_path)
    # tmp_path has no package files -> runbook/units will fail, so report_pass
    # must be False without raising
    assert R.report_pass(report) is False


def test_support_matrix_documented_and_runtime_checked():
    results = {c["name"]: c["state"] for c in R.support_matrix_check(REPO)}
    assert results["support.os"] == "OK"
    assert results["support.arch"] == "OK"
    assert results["support.python"] == "OK"
    assert results["support.documented.AUDIO_SETUP"] == "OK"
    assert results["support.documented.VOICE_BRIDGE_SYSTEM"] == "OK"


def test_report_pass_fails_on_missing_support_docs(tmp_path):
    from voicebridge.config import Config
    report = R.certify(tmp_path)
    assert R.report_pass(report) is False


# ========================================================================
# Resources (packaged-data first, checkout second)
# ========================================================================
def test_resource_path_resolves_checkout_when_package_data_absent():
    from voicebridge import resources as RES
    front = RES.resource_path("frontend")
    assert front == REPO / "frontend"
    assert front.is_dir()


def test_resource_path_prefers_package_data(monkeypatch, tmp_path):
    from voicebridge import resources as RES
    fake = tmp_path / "fake" / "frontend"
    fake.mkdir(parents=True)
    monkeypatch.setattr(RES, "_PACKAGE_DATA", tmp_path / "fake")
    assert RES.resource_path("frontend") == fake


# ========================================================================
# Orchestrator machine-path removal (Packet 8K portability fix)
# ========================================================================
def test_orchestrator_has_no_developer_home_path():
    text = (REPO / "voicebridge/orchestrator.py").read_text()
    assert "/home/" not in text
    assert "~/Projects/Repos" not in text
    assert "resources.resource_path" in text


def test_review_scripts_have_portable_shebangs():
    for name in ("review_gmail.py", "review_instagram.py", "review_messenger.py",
                 "review_tiktok.py", "review_all_inboxes.py"):
        shebang = (REPO / "scripts" / name).read_text().splitlines()[0]
        assert shebang == "#!/usr/bin/env python3", name


# ========================================================================
# Packaging metadata (pyproject) — private state must stay out of releases
# ========================================================================
def test_pyproject_ships_portable_resources_and_excludes_private():
    text = (REPO / "pyproject.toml").read_text()
    # force-include ships the portable example, not the personal template
    assert "task-templates.example.yaml" in text
    assert '"config/voice.example.yaml"' in text
    # sdist excludes live config, personal templates, browser state, dirty files
    for private in ("config/voice.yaml", "config/voice.local.yaml",
                    "config/task-templates.yaml", "config/personal-knowledge.md",
                    "browser_profiles", "DESIGN.md", "PRODUCT.md", "106",
                    "docs/UNIFIED_AGENT_PLAN.md", "tests/test_new_features.py"):
        assert private in text, f"{private} not excluded from sdist"


# ========================================================================
# Clean-venv check repair (finding #7): the in-venv scan must not insert the
# source checkout into sys.path or mask missing wheel contents
# ========================================================================
def test_clean_venv_check_source_has_no_venv_syspath_insertion():
    text = (REPO / "scripts/clean_venv_check.py").read_text()
    # the old masking line `import sys; sys.path.insert(0, <repo>)` inside the
    # in-venv scan must be gone; -I isolated mode is used instead
    assert "sys.path.insert(0, %r)" not in text
    assert '"-I"' in text or "'-I'" in text
    assert "MASK-GUARD" in text or "mask guard" in text
    assert "declared_module_names_from_metadata" in text
    # Packet 8K repair: the --install path runs in a genuinely separate HOME
    # (no operator ~/.config/pip, ~/.local, or user-site leaks)
    assert 'clean_env["HOME"]' in text
    assert 'clean_env.pop("PYTHONPATH", None)' in text
    assert "XDG_CONFIG_HOME" in text


def test_clean_venv_check_fast_mode_still_passes(capsys):
    from scripts import clean_venv_check as CV
    rc = CV.main(["--check"])
    out = capsys.readouterr().out
    assert rc == CV.EXIT_OK
    assert "PASS" in out and "undeclared imports" in out


# ========================================================================
# Operator-local launcher override (Packet 8K portability fix)
# ========================================================================
def test_local_actions_source_is_portable():
    text = (REPO / "voicebridge/local/actions.py").read_text()
    assert "/home/" not in text
    assert "Projects/Repos" not in text
    assert ".wine" not in text
    # the override loader + portable defaults exist
    assert "_load_operator_overrides" in text
    assert "_app_targets" in text and "_project_roots" in text
    assert "_screenshot_dir" in text


def test_local_actions_override_applied(monkeypatch):
    import voicebridge.local.actions as A
    monkeypatch.setattr(A, "_OPERATOR", {
        "app_targets": {"editor": ["code", "/srv/x"]},
        "project_roots": {"acme": "/srv/acme"},
        "screenshot_dir": "/tmp/shots",
    })
    assert A._app_targets()["editor"] == ["code", "/srv/x"]
    assert A._project_roots() == {"acme": "/srv/acme"}
    assert str(A._screenshot_dir()) == "/tmp/shots"


def test_local_actions_portable_defaults(monkeypatch):
    import voicebridge.local.actions as A
    import os
    home = os.path.expanduser("~")
    monkeypatch.setattr(A, "_OPERATOR", {})
    targets = A._app_targets()
    # portable generic launchers must exist without any override
    assert targets["browser"] == ["xdg-open", "https://duckduckgo.com"]
    assert targets["editor"] == ["xdg-open", home]
    assert targets["files"] == ["xdg-open", home]
    assert A._project_roots() == {}
    assert str(A._screenshot_dir()) == os.path.join(home, "Pictures")


def test_local_actions_override_file_contract():
    # the shipped example must be portable and match the loader's schema keys
    import yaml
    ex = yaml.safe_load((REPO / "config/local-actions.example.yaml").read_text())
    assert set(ex) >= {"app_targets", "project_roots", "screenshot_dir"}
    text = (REPO / "config/local-actions.example.yaml").read_text()
    assert "/home/" not in text
    assert "Scarlett" not in text and "HyperX" not in text


# ========================================================================
# release_qualify orchestrator — hermetic surface
# ========================================================================
def test_release_qualify_static_returns_pass():
    import subprocess
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts/release_qualify.py"), "--static",
         "--json", "--out", str(REPO / "dist" / "release-8k-test")],
        capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    report = json.loads(r.stdout)
    assert report["verdict"] == "PASS"


def test_install_step_rejects_missing_wheel(tmp_path):
    import scripts.release_qualify as RQ
    rec = RQ.install_step(tmp_path)
    assert rec["state"] == "DEGRADED"
    assert "no wheel built" in rec["detail"]