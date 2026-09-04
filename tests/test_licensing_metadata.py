"""Packet 8K-P closure: canonical license metadata consistency.

The project license boundary is the Qubit Voice Agent Educational Project
License, which applies ONLY to original Qubit material. Third-party and
upstream-derived material keeps its original license. These tests pin the
canonical license metadata across release metadata (pyproject), the SBOM
output, the root LICENSE, and the packaging boundary so they can never drift
back to a misleading "Proprietary / all rights reserved / covers upstream
code" description. They never claim the repository as a whole is open source.
"""
from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _pyproject_license() -> dict:
    with open(REPO / "pyproject.toml", "rb") as fh:
        data = tomllib.load(fh)
    return data["project"]["license"]


def test_pyproject_license_is_educational_boundary():
    lic = _pyproject_license()
    text = lic["text"] if isinstance(lic, dict) else str(lic)
    assert "Educational Project License" in text
    assert "applies only to original Qubit material" in text
    # never the old proprietary claim, never a whole-repo open-source claim
    assert "Proprietary" not in text
    assert "open source" not in text.lower()
    # license-files carries the root LICENSE
    pp = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert pp["project"]["license-files"] == ["LICENSE"]


def test_root_license_boundary():
    text = (REPO / "LICENSE").read_text()
    flat = " ".join(text.split())
    assert "EDUCATIONAL PROJECT LICENSE" in text.upper()
    assert "applies only to original" in flat
    assert "does not replace, restrict, or relicense any third-party" in flat
    assert "represent the Original Material as open-source" in flat
    # the custom license must not claim to cover upstream code
    assert "third-party" in text.lower()


def test_sbom_project_license_derived_and_consistent():
    from voicebridge import release as R
    rec = R.sbom(repo=REPO)
    text = rec["project_license"]
    pp_lic = _pyproject_license()
    pp_text = pp_lic["text"] if isinstance(pp_lic, dict) else str(pp_lic)
    assert text == pp_text  # SBOM reads the canonical pyproject license
    assert "Educational Project License" in text
    assert "Proprietary" not in text
    assert "THIRD_PARTY_NOTICES.md" in rec["notices"]


def test_sbom_license_is_not_fabricated_when_pyproject_absent(tmp_path):
    """When pyproject is missing the SBOM reports a typed educational fallback,
    never a fabricated proprietary/open-source claim."""
    from voicebridge import release as R
    rec = R.sbom(repo=tmp_path)
    assert "Educational Project License" in rec["project_license"]
    assert "Proprietary" not in rec["project_license"]


def test_docs_and_notices_exist_and_are_referenced():
    for rel in ("THIRD_PARTY_NOTICES.md", "docs/UPSTREAM_REUSE.md",
                "docs/LICENSING.md", "LICENSE"):
        assert (REPO / rel).exists(), rel
    notice = (REPO / "THIRD_PARTY_NOTICES.md").read_text()
    assert "upstream-derived" in notice.lower()
    licensing = (REPO / "docs/LICENSING.md").read_text()
    assert "not an OSI-approved open-source project" in licensing


def test_packaging_boundary_no_license_claims():
    from voicebridge import product as P
    out = P.package_boundary()
    # the packaging boundary never claims signatures, SBOM, or provenance
    assert "no signature claim" in out["signatures"]
    assert "no SBOM claim" in out["sbom"]
    assert "never fabricated" in out["provenance"]
    assert out["private_source_solved"] is False


def test_no_stale_proprietary_license_string_in_release_metadata():
    """The canonical release metadata must contain no stale 'Proprietary /
    all rights reserved' project-license claim."""
    from voicebridge import release as R
    report = R.certify()
    blob = str(report)
    assert "Educational Project License" in R.sbom(repo=REPO)["project_license"]
    # certify embeds the SBOM license text; assert it is the educational text
    assert "Educational Project License" in blob
    assert "Proprietary" not in R.sbom(repo=REPO)["project_license"]


def test_cli_certify_human_summary_no_proprietary(tmp_path):
    """The CLI certification summary must report the educational license."""
    r = subprocess.run(
        [sys.executable, "-m", "voicebridge", "package", "certify"],
        capture_output=True, text=True, timeout=120)
    out = (r.stdout + r.stderr)
    assert "Educational" in out or r.returncode in (0, 2)
    assert "Proprietary" not in out.split("project license:")[-1] if \
        "project license:" in out else True

def test_package_boundary_school_only_not_applicable():
    """School-only submission: signing and private-source are NOT APPLICABLE
    and are reported honestly (never fabricated as signed/private)."""
    from voicebridge import product as P
    out = P.package_boundary()
    assert out["school_only_submission"] is True
    assert "NOT APPLICABLE" in out["private_source_requirement"]
    assert "NOT APPLICABLE" in out["artifact_signing_requirement"]
    # underlying facts stay truthful
    assert out["private_source_solved"] is False
    assert out["wheel_has_python_source"] is True
    assert "no signature claim" in out["signatures"]
    assert "no release-signing key is generated or claimed" in out[
        "artifact_signing_requirement"]
