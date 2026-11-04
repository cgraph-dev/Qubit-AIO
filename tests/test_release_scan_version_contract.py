"""Focused contracts for the release scan + version identity gate.

Hermetic: builds throwaway tar archives and temp trees only. No build, no
install, no network, no service, no operator HOME read.
"""
from __future__ import annotations

import io
import json
import sys
import tarfile
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from voicebridge import release as R  # noqa: E402
from scripts import release_qualify as RQ  # noqa: E402


def _project_version() -> str:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


# ---------------------------------------------------------------------------
# Version identity
# ---------------------------------------------------------------------------
def test_version_identity_agrees_on_this_repo():
    ident = R.version_identity(REPO)
    assert ident["state"] == "OK", ident
    assert ident["pyproject_version"] == _project_version()
    assert ident["package_version"] == _project_version()
    assert ident["runtime_version"]  # informational, not gated
    assert ident["checks"]["package_matches_pyproject"] is True


def test_version_identity_tag_must_match_v_prefixed_project_version():
    version = _project_version()
    ok = R.version_identity(REPO, tag=f"v{version}")
    assert ok["state"] == "OK", ok
    assert ok["checks"]["tag_matches_pyproject"] is True

    wrong = R.version_identity(REPO, tag="v999.0.0")
    assert wrong["state"] == "FAIL"
    assert wrong["checks"]["tag_matches_pyproject"] is False
    assert "v999.0.0" in wrong["detail"]

    bad_format = R.version_identity(REPO, tag=version)
    assert bad_format["state"] == "FAIL"
    assert bad_format["checks"]["tag_format_ok"] is False


def test_version_identity_detects_package_mismatch(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "qubit-voicebridge"\nversion = "1.2.3"\n',
        encoding="utf-8")
    ident = R.version_identity(tmp_path, package_version="9.9.9")
    assert ident["state"] == "FAIL"
    assert ident["checks"]["package_matches_pyproject"] is False
    assert "9.9.9" in ident["detail"] and "1.2.3" in ident["detail"]


def test_release_qualify_fails_before_building_on_tag_mismatch(tmp_path):
    rc = RQ.main([
        "--build", "--tag", "v999.0.0", "--json", "--out", str(tmp_path),
    ])
    # Capture is done by the caller in normal use; here assert the typed exit
    # and then re-run the pure identity contract for the report fields.
    assert rc == RQ.EXIT_FAIL
    ident = R.version_identity(REPO, tag="v999.0.0")
    assert ident["state"] == "FAIL"


# ---------------------------------------------------------------------------
# Developer-only scan exemptions (must not hide real product paths)
# ---------------------------------------------------------------------------
def test_dev_only_path_classification_is_anchored():
    assert R.is_dev_only_path("tmp/audit/probe.py")
    assert R.is_dev_only_path("qubit_voicebridge-0.0.1/tmp/audit/probe.py")
    assert R.is_dev_only_path("chain_probe.py")
    assert R.is_dev_only_path("qubit_voicebridge-0.0.1/REPORT-port-1.md")
    # Product surfaces are never dev-only, even with a similar name.
    assert not R.is_dev_only_path("voicebridge/cdp_supervisor.py")
    assert not R.is_dev_only_path("voicebridge/_data/tmp/keep.py")
    assert not R.is_dev_only_path("frontend/qubit-web/src/App.tsx")


def test_machine_path_scan_skips_dev_only_but_flags_product(tmp_path):
    (tmp_path / "tmp" / "audit").mkdir(parents=True)
    (tmp_path / "tmp" / "audit" / "probe.py").write_text(
        'ROOT = "/home/trick/Projects/Repos/Qubit"\n', encoding="utf-8")
    (tmp_path / "voicebridge").mkdir()
    (tmp_path / "voicebridge" / "leak.py").write_text(
        'ROOT = "/home/trick/Projects/Repos/Qubit"\n', encoding="utf-8")
    hits = R.machine_path_scan(tmp_path)
    assert hits["core"] == {"voicebridge/leak.py": ["/home/trick"]}
    assert not any("tmp/" in path for path in hits["core"])


def _tar_gz(path: Path, members: dict[str, str]) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for name, text in members.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def test_artifact_scan_exempts_dev_only_but_flags_product_paths(tmp_path):
    artifact = _tar_gz(tmp_path / "demo-0.0.1.tar.gz", {
        "demo-0.0.1/tmp/audit/probe.py":
            'ROOT = "/home/trick/Projects/Repos/Qubit"\n',
        "demo-0.0.1/REPORT-port-1.md": "path /home/trick/Projects\n",
        "demo-0.0.1/voicebridge/leak.py":
            'ROOT = "/home/trick/Projects/Repos/Qubit"\n',
    })
    scan = R.artifact_content_scan(artifact)
    flagged = scan["machine_paths"]
    assert "demo-0.0.1/voicebridge/leak.py" in flagged
    assert not any("/tmp/" in name for name in flagged)
    assert not any("REPORT-" in name for name in flagged)


# ---------------------------------------------------------------------------
# Secret-scan false positives (detection is not weakened)
# ---------------------------------------------------------------------------
def test_shell_variable_expansion_is_not_a_secret_value():
    for text in ('TOKEN="${2:-}"', "Token: $TOK", "token: ${TOKEN}"):
        assert R.scan_text_secrets(text, is_config=True) == []
    # A literal credential is still caught.
    assert R.scan_text_secrets("api_key: hunter2", is_config=True) == [
        "assign_secret"]


def test_private_key_header_literal_is_not_a_key_but_a_real_block_is():
    header = "-----BEGIN OPENSSH PRIVATE KEY-----"
    assert "private_key" not in R.scan_text_secrets(header)
    block = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAAB\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    )
    assert "private_key" in R.scan_text_secrets(block)
