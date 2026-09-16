"""OC-D02 — workspace-conformance doctor, findings, and attestation.

A real, user-authorable policy layer on top of the static release checks. A
workspace policy (``.qubit-policy.json`` at the workspace root, or an explicit
rules list) is evaluated into typed findings:

* ``required_files`` — declared files that must exist;
* ``forbidden_globs`` — patterns that must never be present (keys, dotenv);
* ``no_secrets`` — reuse the canonical release secret scan;
* ``no_machine_paths`` — reuse the canonical machine-path scan;
* ``file_modes`` — sensitive files must not be group/other accessible.

The doctor is read-only and never follows symlinks out of the workspace tree.
An *attestation* is the deterministic, content-addressed digest of the ruleset
and the findings, so the same workspace + policy always yields the same
``attestation_id``. Attestations never contain file contents or secret values.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

SCHEMA = "qubit-policy-conformance/1"
POLICY_FILE = ".qubit-policy.json"

OK = "OK"
FAIL = "FAIL"
MISSING = "MISSING"
SKIPPED = "SKIPPED"

_SEVERITIES = {"critical", "high", "medium", "low"}
_MAX_GLOB_FILES = 20_000


class ConformanceError(Exception):
    """Malformed policy or unreadable workspace."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def default_rules() -> List[Dict[str, Any]]:
    """The built-in workspace-conformance ruleset (passes a clean tree)."""
    return [
        {"id": "required-files", "kind": "required_files", "severity": "high",
         "detail": "declared workspace files exist",
         "paths": ["pyproject.toml", "LICENSE"]},
        {"id": "no-committed-secrets", "kind": "no_secrets",
         "severity": "critical",
         "detail": "no secret-shaped values are committed"},
        {"id": "no-machine-paths", "kind": "no_machine_paths",
         "severity": "high",
         "detail": "no developer machine paths leak into shipped code"},
        {"id": "no-private-keys", "kind": "forbidden_globs",
         "severity": "critical",
         "detail": "no private keys or dotenv files are present",
         "globs": [".env", "*.pem", "*.key", "id_rsa", "id_ed25519"]},
        {"id": "sensitive-modes", "kind": "file_modes", "severity": "high",
         "detail": "sensitive files are not group/other accessible",
         "paths": [".env"]},
    ]


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def validate_rules(rules: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for raw in rules or []:
        if not isinstance(raw, dict):
            raise ConformanceError("POLICY_MALFORMED", "rule must be an object")
        rid = str(raw.get("id") or "").strip()
        kind = str(raw.get("kind") or "").strip()
        if not rid or kind not in ("required_files", "forbidden_globs",
                                   "no_secrets", "no_machine_paths",
                                   "file_modes"):
            raise ConformanceError("POLICY_MALFORMED",
                                   f"unsupported rule {rid!r} ({kind!r})")
        if rid in seen:
            raise ConformanceError("POLICY_MALFORMED", f"duplicate rule {rid}")
        seen.add(rid)
        sev = str(raw.get("severity") or "medium").lower()
        if sev not in _SEVERITIES:
            raise ConformanceError("POLICY_MALFORMED",
                                   f"bad severity for {rid}")
        rule = {"id": rid, "kind": kind, "severity": sev,
                "detail": str(raw.get("detail") or "")}
        if kind in ("required_files", "forbidden_globs", "file_modes"):
            paths = raw.get("paths") or raw.get("globs") or []
            if not isinstance(paths, (list, tuple)):
                raise ConformanceError("POLICY_MALFORMED",
                                       f"{rid} paths must be a list")
            key = "globs" if kind == "forbidden_globs" else "paths"
            rule[key] = [str(p) for p in paths]
        out.append(rule)
    return out


def load_rules(root: Path,
               rules: Optional[Sequence[Dict[str, Any]]] = None
               ) -> List[Dict[str, Any]]:
    if rules is not None:
        return validate_rules(rules)
    policy = Path(root) / POLICY_FILE
    if policy.is_file():
        try:
            data = json.loads(policy.read_text(errors="replace"))
        except (OSError, ValueError) as exc:
            raise ConformanceError("POLICY_MALFORMED", str(exc))
        raw_rules = data.get("rules") if isinstance(data, dict) else None
        if not isinstance(raw_rules, list):
            raise ConformanceError("POLICY_MALFORMED",
                                   f"{POLICY_FILE} must have a rules list")
        return validate_rules(raw_rules)
    return default_rules()


def _finding(rule: Dict[str, Any], state: str, detail: str,
             path: str = "") -> Dict[str, Any]:
    return {"rule": rule["id"], "kind": rule["kind"],
            "severity": rule["severity"], "state": state,
            "detail": detail, "path": path}


def _check_required_files(root: Path, rule: Dict[str, Any]) -> List[dict]:
    out = []
    for rel in rule.get("paths", []):
        p = root / rel
        state = OK if p.exists() else MISSING
        out.append(_finding(rule, state,
                            "present" if state == OK else "missing", rel))
    return out


def _check_forbidden_globs(root: Path, rule: Dict[str, Any]) -> List[dict]:
    hits: List[str] = []
    for pattern in rule.get("globs", []):
        for p in root.rglob(pattern):
            rel = p.relative_to(root).as_posix()
            if any(part in (".git", ".venv", "node_modules", "__pycache__")
                   for part in p.parts):
                continue
            if p.is_symlink():
                continue
            hits.append(rel)
            if len(hits) >= _MAX_GLOB_FILES:
                break
    if hits:
        return [_finding(rule, FAIL, "forbidden files present", h)
                for h in sorted(set(hits))[:50]]
    return [_finding(rule, OK, "no forbidden files")]


def _check_no_secrets(root: Path, rule: Dict[str, Any]) -> List[dict]:
    from ..release import secret_scan

    hits = secret_scan(root)
    if hits:
        return [_finding(rule, FAIL, "secret-shaped value", rel)
                for rel in sorted(hits)]
    return [_finding(rule, OK, "no secret-shaped values")]


def _check_no_machine_paths(root: Path, rule: Dict[str, Any]) -> List[dict]:
    from ..release import machine_path_scan

    core = machine_path_scan(root).get("core", {})
    if core:
        return [_finding(rule, FAIL, "machine path token", rel)
                for rel in sorted(core)]
    return [_finding(rule, OK, "no machine path tokens")]


def _check_file_modes(root: Path, rule: Dict[str, Any]) -> List[dict]:
    out = []
    for rel in rule.get("paths", []):
        p = root / rel
        if not p.exists():
            out.append(_finding(rule, SKIPPED, "not present", rel))
            continue
        mode = p.stat().st_mode & 0o777
        if mode & (0o077):
            out.append(_finding(rule, FAIL, f"mode {oct(mode)} is too open",
                                rel))
        else:
            out.append(_finding(rule, OK, f"mode {oct(mode)}", rel))
    return out


_CHECKS = {
    "required_files": _check_required_files,
    "forbidden_globs": _check_forbidden_globs,
    "no_secrets": _check_no_secrets,
    "no_machine_paths": _check_no_machine_paths,
    "file_modes": _check_file_modes,
}


def findings(root: Path,
             rules: Optional[Sequence[Dict[str, Any]]] = None
             ) -> List[Dict[str, Any]]:
    """Run the ruleset and return typed findings (read-only)."""
    root = Path(root)
    if not root.is_dir():
        raise ConformanceError("WORKSPACE_UNREADABLE", str(root))
    rule_list = load_rules(root, rules)
    out: List[Dict[str, Any]] = []
    for rule in rule_list:
        check = _CHECKS[rule["kind"]]
        try:
            out.extend(check(root, rule))
        except ConformanceError:
            raise
        except Exception as exc:  # a broken check is a typed finding
            out.append(_finding(rule, FAIL, f"check error: {type(exc).__name__}"))
    return out


def doctor(root: Path,
           rules: Optional[Sequence[Dict[str, Any]]] = None
           ) -> Dict[str, Any]:
    """Full conformance report: findings, counts, and pass/fail state."""
    rule_list = load_rules(Path(root), rules)
    found = findings(root, rule_list)
    counts = {OK: 0, FAIL: 0, MISSING: 0, SKIPPED: 0}
    for f in found:
        counts[f["state"]] = counts.get(f["state"], 0) + 1
    state = "PASS" if counts[FAIL] == 0 and counts[MISSING] == 0 else "FAIL"
    return {
        "schema": SCHEMA,
        "state": state,
        "ruleset": [r["id"] for r in rule_list],
        "counts": counts,
        "findings": found,
        "critical": [f for f in found
                     if f["severity"] == "critical" and f["state"] == FAIL],
    }


def pass_conformance(report: Dict[str, Any]) -> bool:
    """True only when no FAIL/MISSING finding remains."""
    return bool(report) and report.get("state") == "PASS"


def attestation(root: Path,
                rules: Optional[Sequence[Dict[str, Any]]] = None,
                *, generated_at: str = "",
                report: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Content-addressed attestation of the workspace + ruleset.

    The fingerprint is deterministic (no timestamps in the digest); the
    optional ``generated_at`` label is carried alongside but excluded from it.
    Pass a precomputed ``report`` to avoid re-running the (read-only) checks.
    """
    rule_list = load_rules(Path(root), rules)
    report = report if report is not None else doctor(root, rule_list)
    ruleset_digest = _digest([r for r in rule_list])
    findings_digest = _digest(report["findings"])
    fingerprint = hashlib.sha256(
        f"{ruleset_digest}:{findings_digest}".encode("utf-8")).hexdigest()
    return {
        "schema": SCHEMA,
        "attestation_id": fingerprint,
        "ruleset_digest": ruleset_digest,
        "findings_digest": findings_digest,
        "state": report["state"],
        "counts": report["counts"],
        "ruleset": report["ruleset"],
        "generated_at": str(generated_at or ""),
        "immutable": True,
    }
