"""Policy tests for the public workflow surface.

``actionlint`` proves syntax; these tests prove the security properties the
repository promises for GitHub-hosted PR CI: least-privilege permissions,
concurrency, SHA-pinned actions, no ``pull_request_target``, no secrets on
pull requests, and draft-only releases.

They also pin which workflows are managed versus explicitly legacy, so a new
workflow cannot be exempted silently.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
POLICY = ROOT / ".github" / "scripts" / "check_workflow_security.py"

EXPECTED_LEGACY = {
    "qubit-ci.yml",
    "windows-ci.yml",
    "windows-install-update-e2e.yml",
}

NEW_MANAGED = {
    "blob-size-check.yml",
    "bootstrap-installer.yml",
    "case-collision-check.yml",
    "docs-site-checks.yml",
    "install-e2e.yml",
    "lazy-deps-guard.yml",
    "osv-scanner.yml",
    "promote-public.yml",
    "promote-release.yml",
}

EXPECTED_SECRET_WORKFLOWS = {
    "promote-public.yml": {"QUBIT_PUBLIC_PUSH_TOKEN"},
}


def _load_policy():
    spec = importlib.util.spec_from_file_location("workflow_security", POLICY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POLICY_MODULE = _load_policy()


def _workflow_paths() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.y*ml"))


def _managed_paths() -> list[Path]:
    return [path for path in _workflow_paths()
            if path.name not in POLICY_MODULE.LEGACY_WORKFLOWS]


def _jobs(doc: dict):
    for name, job in (doc.get("jobs") or {}).items():
        if isinstance(job, dict):
            yield name, job


def test_policy_script_passes_on_repository_workflows():
    assert POLICY_MODULE.main() == 0


def test_every_workflow_parses_and_has_a_name():
    for path in _workflow_paths():
        doc = POLICY_MODULE.load(path)
        assert isinstance(doc.get("name"), str) and doc["name"], path.name


def test_legacy_allowlist_is_exact_and_every_entry_exists():
    names = {path.name for path in _workflow_paths()}
    assert POLICY_MODULE.LEGACY_WORKFLOWS == EXPECTED_LEGACY
    assert EXPECTED_LEGACY <= names


def test_new_high_value_workflows_are_managed():
    assert NEW_MANAGED <= {path.name for path in _workflow_paths()}
    assert NEW_MANAGED.isdisjoint(POLICY_MODULE.LEGACY_WORKFLOWS)


def test_no_workflow_uses_pull_request_target():
    for path in _workflow_paths():
        doc = POLICY_MODULE.load(path)
        assert "pull_request_target" not in POLICY_MODULE.trigger_names(doc), path.name


def test_managed_workflows_have_permissions_concurrency_and_pinned_actions():
    for path in _managed_paths():
        doc = POLICY_MODULE.load(path)
        errors: list[str] = []
        POLICY_MODULE.check_permissions(doc, path, errors)
        POLICY_MODULE.check_concurrency(doc, path, errors)
        POLICY_MODULE.check_pins(doc, path, errors)
        assert not errors, errors


def test_managed_workflows_never_reference_repository_secrets():
    for path in _managed_paths():
        doc = POLICY_MODULE.load(path)
        errors: list[str] = []
        POLICY_MODULE.check_secrets(doc, path, errors)
        assert not errors, errors


def test_secret_allowlist_is_exact_and_environment_gated():
    assert POLICY_MODULE.SECRET_WORKFLOWS == EXPECTED_SECRET_WORKFLOWS
    for name, secrets in EXPECTED_SECRET_WORKFLOWS.items():
        path = WORKFLOWS / name
        assert path.is_file(), name
        doc = POLICY_MODULE.load(path)
        assert "workflow_dispatch" in POLICY_MODULE.trigger_names(doc), name
        assert "pull_request" not in POLICY_MODULE.trigger_names(doc), name
        # Every job that can read an allowed secret must be environment-gated.
        for job_name, job in _jobs(doc):
            text = "\n".join(POLICY_MODULE.all_strings(job))
            if any("secrets." + secret in text for secret in secrets):
                assert job.get("environment"), (name, job_name)


def test_pull_request_workflows_are_hosted_read_only_and_bounded():
    for path in _managed_paths():
        doc = POLICY_MODULE.load(path)
        if "pull_request" not in POLICY_MODULE.trigger_names(doc):
            continue
        permissions = doc.get("permissions")
        assert isinstance(permissions, dict), path.name
        assert all(level in ("read", "none") for level in permissions.values()), path.name
        for job_name, job in _jobs(doc):
            runner = " ".join(POLICY_MODULE.all_strings(job.get("runs-on")))
            assert "self-hosted" not in runner, (path.name, job_name)
            if "uses" in job:
                continue
            assert job.get("timeout-minutes"), (path.name, job_name)


def test_install_e2e_installs_an_artifact_not_the_checkout():
    text = (WORKFLOWS / "install-e2e.yml").read_text(encoding="utf-8")
    assert "scripts/artifact_install_e2e.py" in text
    assert "--build" in text
    assert "--require-network" in text
    assert "pip install -e ." not in text
    assert "pip install ." not in text
    assert "pip install -e \"$" not in text


def test_bootstrap_installer_workflow_is_hermetic_and_checksum_gated():
    text = (WORKFLOWS / "bootstrap-installer.yml").read_text(encoding="utf-8")
    assert "tests/test_bootstrap_installer.py" in text
    assert "sha256sum" in text
    assert "BOOTSTRAP_CHECKSUM_MISMATCH" in text
    assert "--dry-run" in text
    assert "secrets." not in text


def test_docs_site_checks_builds_without_deploying():
    text = (WORKFLOWS / "docs-site-checks.yml").read_text(encoding="utf-8")
    assert "npm run build:fast" in text
    assert "check_doc_links.py" in text
    assert "lint:diagrams" in text
    assert "docusaurus deploy" not in text
    assert "npm run deploy" not in text
    assert "secrets." not in text


def test_osv_scanner_is_scheduled_detection_only():
    doc = POLICY_MODULE.load(WORKFLOWS / "osv-scanner.yml")
    triggers = POLICY_MODULE.trigger_names(doc)
    assert "schedule" in triggers
    assert "pull_request" not in triggers
    assert doc["permissions"]["security-events"] == "write"
    job = doc["jobs"]["scan"]
    assert job["uses"].startswith(
        "google/osv-scanner-action/.github/workflows/osv-scanner-reusable.yml@")
    assert job["with"]["fail-on-vuln"] is False
    assert "--lockfile=uv.lock" not in job["with"]["scan-args"]


def test_release_workflow_is_draft_only():
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert "gh release create" in text
    assert "--draft" in text
    for forbidden in ("twine upload", "npm publish", "--draft=false"):
        assert forbidden not in text


def test_release_install_never_reads_the_checkout():
    text = (WORKFLOWS / "release-install.yml").read_text(encoding="utf-8")
    assert "actions/checkout" not in text
    assert "gh release download" in text
    assert "sha256sum -c SHA256SUMS" in text


def test_promote_workflow_is_dispatch_only_draft_and_never_creates_a_tag():
    text = (WORKFLOWS / "promote-release.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in text
    assert "gh release create" in text
    assert "--draft" in text
    assert "--verify-tag" in text
    assert "environment: release" in text
    for forbidden in ("twine upload", "npm publish", "--draft=false",
                      "git tag", "git push --tags"):
        assert forbidden not in text


def test_policy_rejects_a_promote_workflow_that_could_create_a_tag():
    doc = POLICY_MODULE.load(WORKFLOWS / "promote-release.yml")
    errors: list[str] = []
    POLICY_MODULE.check_promote_shape(doc, WORKFLOWS / "promote-release.yml", errors)
    assert not errors, errors

    unsafe = {
        "on": {"workflow_dispatch": None},
        "jobs": {
            "draft": {
                "environment": "release",
                "steps": [{"run": "gh release create v1.0.0 --draft"}],
            }
        },
    }
    errors = []
    POLICY_MODULE.check_promote_shape(unsafe, Path("promote-release.yml"), errors)
    assert any("--verify-tag" in error for error in errors), errors


def test_promote_public_is_manual_and_apply_is_environment_gated():
    path = WORKFLOWS / "promote-public.yml"
    doc = POLICY_MODULE.load(path)
    triggers = POLICY_MODULE.trigger_names(doc)
    assert "workflow_dispatch" in triggers
    assert "pull_request" not in triggers
    apply_job = doc["jobs"]["apply"]
    assert apply_job["environment"] == "public-production"
    assert apply_job["if"] == "${{ inputs.apply }}"
    text = path.read_text(encoding="utf-8")
    assert "scripts/promote_public_release.py" in text
    assert "--apply" in text
    assert "refs/heads/main" not in text
    assert "--branch main" not in text
