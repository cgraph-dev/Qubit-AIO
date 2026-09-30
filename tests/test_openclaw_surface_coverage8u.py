"""8u — OpenClaw contractual-pin surface coverage gate (fail-closed).

The upgrade manifest's method extractor previously matched every quoted dotted
string in ``core-descriptors.ts``. That captured the ``operator.*`` scope
strings as "methods" and dropped the non-dotted method names (``health``,
``connect``, ``send``, ...). These tests pin the true surface (the
``CORE_GATEWAY_METHOD_SPECS`` table rows) and assert the manifest records it
exactly, then assert every contractual-pin method carries exactly one
disposition or is explicitly ``OPEN``. OPEN is the fail-closed default and is
reported, never hidden.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

MANIFEST = REPO_ROOT / "docs/generated/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json"
SCRIPT = REPO_ROOT / "scripts/qubit_upgrade_manifest.py"
DESCRIPTOR = "src/gateway/methods/core-descriptors.ts"

TRUE_COUNTS = {"contractual_pin": 434, "observed_pin": 445}
NON_DOTTED = frozenset({
    "agent", "connect", "health", "last-heartbeat", "poll", "send",
    "set-heartbeats", "status", "system-event", "system-presence", "wake",
})
TERMINAL = ("IMPLEMENTED", "SAFE_EQUIVALENT", "OPERATOR_ONLY_EXTERNAL")
ALLOWED = TERMINAL + ("OPERATOR_ONLY_ARCHITECTURE", "OPEN")


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "qubit_upgrade_manifest", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _candidate_clones():
    env = os.environ.get("OPENCLAW_PINNED_CLONE")
    if env:
        yield Path(env)
    for name in ("openclaw", "openclaw-v2026.9.2", "openclaw-v2026.9.4"):
        yield Path("/tmp/opencode/pinned") / name


def _clone_for(commit):
    for clone in _candidate_clones():
        if not (clone / ".git").exists():
            continue
        proc = subprocess.run(
            ["git", "-C", str(clone), "cat-file", "-e", f"{commit}^{{commit}}"],
            capture_output=True)
        if proc.returncode == 0:
            return clone
    return None


def _blob(clone, commit):
    return subprocess.run(
        ["git", "-C", str(clone), "show", f"{commit}:{DESCRIPTOR}"],
        capture_output=True, text=True, check=True).stdout


def _true_names(blob):
    """Independent parse of the ``CORE_GATEWAY_METHOD_SPECS`` table rows."""
    start = blob.index("CORE_GATEWAY_METHOD_SPECS = [")
    body = blob[start:]
    end = body.find("\n] as const satisfies")
    if end != -1:
        body = body[:end]
    names, lines, i = [], body.splitlines(), 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("["):
            m = re.match(r'\[\s*"([^"]+)"', line)
            if m:
                names.append(m.group(1))
            else:
                for nxt in lines[i + 1:]:
                    m2 = re.match(r'\s*"([^"]+)"', nxt)
                    if m2:
                        names.append(m2.group(1))
                        break
        i += 1
    return sorted(set(names))


def _manifest():
    return json.loads(MANIFEST.read_text())


def test_true_surface_parses_to_the_contractual_and_observed_counts():
    mod = _load_script()
    contractual = _clone_for(mod.CONTRACTUAL_PIN)
    if contractual is None:
        pytest.skip("pinned OpenClaw clone unavailable")
    observed = _clone_for(mod.OBSERVED_PIN) or contractual

    pins = {
        "contractual_pin": _true_names(_blob(contractual, mod.CONTRACTUAL_PIN)),
        "observed_pin": _true_names(_blob(observed, mod.OBSERVED_PIN)),
    }
    for key, names in pins.items():
        assert len(names) == TRUE_COUNTS[key], key
        assert not [n for n in names if n.startswith("operator.")], key
        assert NON_DOTTED <= set(names), key
    # The script extractor must agree with the independent parse.
    assert mod.extract_methods(contractual, mod.CONTRACTUAL_PIN) == \
        pins["contractual_pin"]
    assert mod.extract_methods(observed, mod.OBSERVED_PIN) == \
        pins["observed_pin"]


def test_manifest_records_only_real_method_names():
    surface = _manifest()["gateway_method_surface"]
    for key, count in TRUE_COUNTS.items():
        assert surface["counts"][key] == count, key
    pin = surface["contractual_pin"]
    assert len(pin) == TRUE_COUNTS["contractual_pin"]
    assert len(set(pin)) == len(pin)
    assert not [n for n in pin if n.startswith("operator.")]
    assert NON_DOTTED <= set(pin)


def test_pinned_surface_is_disposed_or_explicitly_open():
    manifest = _manifest()
    pinned = set(manifest["gateway_method_surface"]["contractual_pin"])
    section = manifest["pinned_surface_disposition"]
    methods = section["methods"]
    assert set(methods) == pinned
    for name, row in methods.items():
        assert row["status"] in ALLOWED, name
    counts = section["counts"]
    for status in ALLOWED:
        assert counts.get(status, 0) == sum(
            1 for r in methods.values() if r["status"] == status), status
    assert sum(counts.values()) == len(pinned)


def test_pinned_implemented_rows_resolve_to_real_registered_handlers():
    from voicebridge.gateway.rpc import default_registry

    registered = set(default_registry().names())
    methods = _manifest()["pinned_surface_disposition"]["methods"]
    for name, row in methods.items():
        if row["status"] == "IMPLEMENTED":
            assert name in registered, name
            assert row.get("evidence"), name
            assert (REPO_ROOT / row["evidence"]).is_file(), name


def test_pinned_refusal_rows_declare_code_and_proof():
    methods = _manifest()["pinned_surface_disposition"]["methods"]
    for name, row in methods.items():
        if row["status"] == "SAFE_EQUIVALENT":
            assert row.get("code"), name
            assert row.get("qubit_counterpart"), name
            assert row.get("evidence"), name
        elif row["status"] in ("OPERATOR_ONLY_EXTERNAL",
                               "OPERATOR_ONLY_ARCHITECTURE"):
            assert row.get("code"), name
            assert row.get("operator_prerequisite"), name
            assert row.get("evidence"), name


def test_coverage_gate_reports_the_open_count(capsys, request):
    manifest = _manifest()
    pinned = manifest["gateway_method_surface"]["contractual_pin"]
    counts = manifest["pinned_surface_disposition"]["counts"]
    disposed = sum(counts.get(s, 0) for s in TERMINAL)
    open_count = counts.get("OPERATOR_ONLY_ARCHITECTURE", 0) + counts.get("OPEN", 0)
    summary = (
        f"OpenClaw pinned surface: {disposed}/{len(pinned)} disposed, "
        f"{open_count}/{len(pinned)} OPEN "
        f"(IMPLEMENTED={counts.get('IMPLEMENTED', 0)} "
        f"SAFE_EQUIVALENT={counts.get('SAFE_EQUIVALENT', 0)} "
        f"OPERATOR_ONLY_EXTERNAL={counts.get('OPERATOR_ONLY_EXTERNAL', 0)})")
    print(summary)
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(summary)
    captured = capsys.readouterr()
    assert summary in captured.out
    assert disposed + open_count == len(pinned)


def test_manifest_pinned_surface_does_not_drift_from_delta_overlay():
    """The delta overlay stays intact (backward-compatible schema)."""
    manifest = _manifest()
    overlay = manifest["disposition_overlay"]
    assert overlay["counts"].get("OPEN", 0) == 0
    union = set(manifest["delta"]["latest_release"]["added"]) \
        | set(manifest["delta"]["latest_main"]["added"])
    assert set(overlay["methods"]) == union
