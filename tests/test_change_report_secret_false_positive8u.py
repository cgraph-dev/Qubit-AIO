"""8U — change-report secret-guard false-positive regression.

The change report lists the git file-status/diff paths, which include the
byte-faithful vendored fork (renamed ``qubit_runtime/``) and the built dashboard
(``frontend/qubit-dist/``). Those trees legitimately contain filenames with
secret-ish words (``test_api_key_providers.py``, ``1password/SKILL.md``). Such
harmless vendor *filenames* must never be treated as secret *values*, while a
real secret value must still be caught and redacted.

Hermetic: ``_git`` is stubbed with a controlled status/diff, so the test never
depends on the real dirty worktree.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

_VENDOR_FILE = "qubit_runtime/tests/qubit_cli/test_api_key_providers.py"
_VENDOR_SKILL = "qubit_runtime/optional-skills/security/1password/SKILL.md"
_QUBIT_FILE = "voicebridge/credentials.py"


def _stub_git(*args: str) -> str:
    if args[:2] == ("status", "--short"):
        return "\n".join([
            f"?? {_VENDOR_FILE}",
            f"?? {_VENDOR_SKILL}",
            f" M {_QUBIT_FILE}",
        ])
    if args[:2] == ("diff", "--numstat"):
        return (f"1\t0\t{_VENDOR_FILE}\n"
                f"2\t1\t{_QUBIT_FILE}\n")
    return ""


def test_vendor_paths_are_filtered_but_qubit_paths_kept(monkeypatch):
    from scripts import change_report as CR

    monkeypatch.setattr(CR, "_git", _stub_git)

    assert CR._is_vendor_path(_VENDOR_FILE)
    assert CR._is_vendor_path(_VENDOR_SKILL)
    assert CR._is_vendor_path("qubit_hermes/agent/secret_scope.py")
    assert CR._is_vendor_path("frontend/qubit-dist/app.js")
    assert not CR._is_vendor_path(_QUBIT_FILE)

    status = CR._status_short()
    assert "api_key_providers" not in status
    assert "1password" not in status
    assert _QUBIT_FILE in status

    diff = CR._diff_stat()
    assert "api_key_providers" not in diff
    assert "1password" not in diff
    assert _QUBIT_FILE in diff


def test_harmless_vendor_filename_does_not_trip_the_guard(monkeypatch):
    from scripts import change_report as CR

    monkeypatch.setattr(CR, "_git", _stub_git)
    md = CR.build_report("t", "ok", "ok", "none", "restore .bak")

    for harmless in ("api_key_providers", "1password", "SKILL.md"):
        assert harmless not in md, f"harmless vendor filename tripped: {harmless}"
    assert _QUBIT_FILE in md  # a real Qubit-owned change is still listed


def test_synthetic_secret_value_is_caught_and_redacted(monkeypatch):
    from scripts import change_report as CR

    monkeypatch.setattr(CR, "_git", _stub_git)
    secret = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    md = CR.build_report(
        "t", f"token={secret}", f"api_key: {secret}", "none", "restore .bak")

    assert secret not in md, "secret value leaked into the report"
    assert "[REDACTED]" in md


def test_canonical_scanner_still_detects_values_not_filenames():
    from voicebridge.release import scan_text_secrets
    from voicebridge.redaction import redact_text

    secret = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

    # A real value is caught (and redacted) by the canonical guard.
    assert "openai_key" in scan_text_secrets(secret)
    assert secret not in redact_text(f"token={secret}")

    # Harmless vendor filenames are not secret values.
    for harmless in (_VENDOR_FILE, _VENDOR_SKILL,
                     "qubit_runtime/agent/secret_scope.py"):
        assert scan_text_secrets(harmless) == [], harmless
        assert redact_text(harmless) == harmless

    # A config-style assignment is still detected (detection not weakened).
    assert scan_text_secrets("api_key: hunter2", is_config=True) == [
        "assign_secret"]
