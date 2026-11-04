#!/usr/bin/env python3
"""Policy checks for the Qubit public-release workflows.

`actionlint` validates syntax and expressions; this script validates the
security properties that YAML syntax cannot express:

* an explicit least-privilege `permissions` block;
* a `concurrency` group;
* no `pull_request_target`;
* no self-hosted runners in a `pull_request` workflow;
* no `secrets.*` references outside the explicit `SECRET_WORKFLOWS`
  allowlist (currently only the environment-gated promotion workflow);
* every remote action pinned to a full 40-hex commit SHA, and every Docker
  action pinned to a sha256 digest (local `./` actions are allowed);
* a `v*`-tag release workflow creates drafts only (`gh release create --draft`).

The three remaining upstream-derived workflows are listed as legacy and are
intentionally not rewritten here (they still use self-hosted runners, unpinned
actions, or tag pushes). The allowlist is explicit so a *new* workflow cannot
inherit those exemptions silently. Every other workflow is managed and must
satisfy the full policy.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

LEGACY_WORKFLOWS = {
    "qubit-ci.yml",
    "windows-ci.yml",
    "windows-install-update-e2e.yml",
}

#: Workflows allowed to read repository secrets, and the exact secret names
#: they may read. Every entry must be a manually dispatched workflow with a
#: protected environment; nothing here runs on pull requests.
SECRET_WORKFLOWS = {
    "promote-public.yml": {"QUBIT_PUBLIC_PUSH_TOKEN"},
}

SHA = re.compile(r"^[0-9a-f]{40}$")
DOCKER_DIGEST = re.compile(r"^docker://.+@sha256:[0-9a-f]{64}$")
SECRET_REF = re.compile(r"\bsecrets\.[A-Za-z0-9_]+")
REMOTE_ACTION = re.compile(r"^[^/@\s]+/[^@\s]+@(?P<ref>.+)$")


def load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: workflow is not a mapping")
    return data


def trigger_names(doc: dict) -> set[str]:
    # PyYAML follows YAML 1.1 and parses the unquoted key `on` as boolean True.
    trigger = doc.get("on", doc.get(True))
    if trigger is None:
        return set()
    if isinstance(trigger, str):
        return {trigger}
    if isinstance(trigger, list):
        return {str(item) for item in trigger}
    if isinstance(trigger, dict):
        # Values may be null, a mapping, or a list; the key is what matters.
        return {str(key) for key in trigger}
    return set()


def walk(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key), value
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def uses_values(doc: dict):
    for key, value in walk(doc):
        if key == "uses" and isinstance(value, str):
            yield value


def all_strings(node) -> list[str]:
    if isinstance(node, dict):
        return [s for value in node.values() for s in all_strings(value)]
    if isinstance(node, list):
        return [s for value in node for s in all_strings(value)]
    if isinstance(node, str):
        return [node]
    return []


def check_permissions(doc: dict, path: Path, errors: list[str]) -> None:
    permissions = doc.get("permissions")
    if permissions is None:
        errors.append(f"{path.name}: missing top-level `permissions`")
        return
    if permissions == "write-all":
        errors.append(f"{path.name}: `permissions: write-all` is forbidden")
        return
    if isinstance(permissions, dict):
        for scope, level in permissions.items():
            if level not in ("read", "write", "none"):
                errors.append(
                    f"{path.name}: permissions.{scope}={level!r} is not read/write/none"
                )


def check_concurrency(doc: dict, path: Path, errors: list[str]) -> None:
    if not doc.get("concurrency"):
        errors.append(f"{path.name}: missing `concurrency`")


def check_pins(doc: dict, path: Path, errors: list[str]) -> None:
    for value in uses_values(doc):
        if value.startswith("./"):
            continue  # local composite/reusable workflow
        if value.startswith("docker://"):
            if not DOCKER_DIGEST.match(value):
                errors.append(f"{path.name}: docker action is not digest-pinned: {value}")
            continue
        match = REMOTE_ACTION.match(value)
        if not match or not SHA.match(match.group("ref")):
            errors.append(f"{path.name}: remote action is not SHA-pinned: {value}")


def check_secrets(doc: dict, path: Path, errors: list[str]) -> None:
    allowed = SECRET_WORKFLOWS.get(path.name, set())
    for text in all_strings(doc):
        match = SECRET_REF.search(text)
        if not match:
            continue
        name = match.group(0).split(".", 1)[1]
        if name in allowed:
            continue
        errors.append(f"{path.name}: managed workflow references a secret: {text.strip()[:120]}")


def check_pull_request(doc: dict, path: Path, errors: list[str]) -> None:
    triggers = trigger_names(doc)
    if "pull_request_target" in triggers:
        errors.append(f"{path.name}: `pull_request_target` is forbidden")
    if "pull_request" not in triggers:
        return
    for job_name, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        runner = job.get("runs-on")
        texts = all_strings(runner)
        if any("self-hosted" in text for text in texts):
            errors.append(
                f"{path.name}: job {job_name!r} uses a self-hosted runner on pull_request"
            )
    permissions = doc.get("permissions")
    if isinstance(permissions, dict):
        writes = {scope: level for scope, level in permissions.items()
                  if level not in ("read", "none")}
        if writes:
            errors.append(
                f"{path.name}: pull_request workflow grants write permission: {writes}"
            )


def check_release_shape(doc: dict, path: Path, errors: list[str]) -> None:
    if path.name != "release.yml":
        return
    triggers = trigger_names(doc)
    if "push" not in triggers:
        errors.append(f"{path.name}: must trigger on a v* tag push")
    trigger = doc.get("on", doc.get(True))
    push = trigger.get("push") if isinstance(trigger, dict) else None
    if not isinstance(push, dict) or push.get("tags") != ["v*"]:
        errors.append(f"{path.name}: push trigger must be exactly tags: [\"v*\"]")
    text = "\n".join(all_strings(doc))
    if "gh release create" in text and "--draft" not in text:
        errors.append(f"{path.name}: `gh release create` must pass `--draft`")
    for verb in ("twine upload", "npm publish", "gh release edit --draft=false"):
        if verb in text:
            errors.append(f"{path.name}: publishing verb is forbidden: {verb}")


PROMOTE_WORKFLOWS = {"promote-release.yml"}


def check_promote_shape(doc: dict, path: Path, errors: list[str]) -> None:
    """The operator promote workflow is draft-only and must not create tags."""
    if path.name not in PROMOTE_WORKFLOWS:
        return
    triggers = trigger_names(doc)
    if "workflow_dispatch" not in triggers:
        errors.append(f"{path.name}: promote workflow must be workflow_dispatch only")
    text = "\n".join(all_strings(doc))
    if "gh release create" in text and "--draft" not in text:
        errors.append(f"{path.name}: `gh release create` must pass `--draft`")
    if "gh release create" in text and "--verify-tag" not in text:
        errors.append(
            f"{path.name}: `gh release create` must pass `--verify-tag` so it "
            "cannot create a tag"
        )
    for verb in ("twine upload", "npm publish", "gh release edit --draft=false",
                 "git push --tags", "git tag"):
        if verb in text:
            errors.append(f"{path.name}: publishing/tag verb is forbidden: {verb}")
    if not any(
        isinstance(job, dict) and job.get("environment") == "release"
        for job in (doc.get("jobs") or {}).values()
    ):
        errors.append(f"{path.name}: must gate the draft step on the `release` environment")


def main() -> int:
    errors: list[str] = []
    checked: list[str] = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        if path.name in LEGACY_WORKFLOWS:
            continue
        checked.append(path.name)
        try:
            doc = load(path)
        except Exception as exc:  # noqa: BLE001 - report, do not traceback
            errors.append(f"{path.name}: cannot parse: {exc}")
            continue
        check_permissions(doc, path, errors)
        check_concurrency(doc, path, errors)
        check_pins(doc, path, errors)
        check_secrets(doc, path, errors)
        check_pull_request(doc, path, errors)
        check_release_shape(doc, path, errors)
        check_promote_shape(doc, path, errors)

    if not checked:
        print("workflow-security: no managed workflows found", file=sys.stderr)
        return 1
    if errors:
        print("workflow-security: FAILED", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"workflow-security: OK ({len(checked)} managed workflow(s): {', '.join(checked)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
