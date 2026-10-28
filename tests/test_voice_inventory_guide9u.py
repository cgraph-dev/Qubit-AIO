"""Honest voice-coverage classification guard (2026-09-28).

``scripts/voice_inventory_coverage.py`` used to classify a row as ``guard`` only
when its bound backend was exactly ``voice_unsupported``. Typed refusals that
resolve to a genuinely-callable backend (``*_refusal`` entries such as
``cli_import_refusal`` / ``cli_gateway_supervision_refusal``) and guidance
entries (``*_help`` / ``*_guidance``) were counted ``real``, inflating coverage.

This test locks the honest classification, asserting *relationships* and a fixed
literal sample (no change-detector on the totals):

* any resolved refusal/guidance entry is ``guard``, never ``real``;
* the reported ``real`` count is exactly ``total - guard - unresolved``;
* a fixed sample of known refusals/guidance is ``guard`` and a fixed sample of
  known working bindings is ``real``, so the guard can never become vacuous.

``docs/generated/`` is gitignored, so a fresh worktree has no artifact: the
artifact fixture is generated under ``tmp_path`` from the real
``scripts.parity_inventory.build_controls()`` rows. Nothing in the repository
is written.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

SCRIPTS = REPO / "scripts"

RESOLUTIONS = ("real", "guard", "unresolved")

# Literal phrase -> the entry it must resolve to. Independent of the generator's
# internal predicate so a regression in the predicate cannot silence the test.
KNOWN_GUARDS = (
    ("qubit import refusal", "cli_import_refusal"),
    ("qubit gateway supervision refusal", "cli_gateway_supervision_refusal"),
    ("qubit browser close", "cli_browser_close_refusal"),
    ("qubit proxy start", "cli_proxy_start_refusal"),
    ("qubit tui help", "cli_tui_help"),
    ("qubit console help", "cli_console_help"),
    ("qubit mic help", "cli_mic_help"),
    ("qubit kanban link task 5", "guard_kanban_link"),
)

KNOWN_REAL = (
    ("qubit schedules delete 3", "cli_schedules_delete"),
    ("qubit mcp remove demo", "cli_mcp_remove"),
    ("qubit cron list", "schedules_list"),
    ("qubit status", "status"),
)

_GUARD_ENTRY_IDS = {entry_id for _, entry_id in KNOWN_GUARDS}


def _load():
    """Load the generator script by path under a unique module name."""
    name = "voice_inventory_coverage_guide9u"
    spec = importlib.util.spec_from_file_location(
        name, SCRIPTS / "voice_inventory_coverage.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load()


@pytest.fixture(scope="module")
def artifact(mod, tmp_path_factory):
    """Render the coverage artifact into tmp_path from real parity rows."""
    from scripts import parity_inventory as pi

    rows = pi.build_controls()
    assert rows, "parity inventory fixture is empty"
    root = tmp_path_factory.mktemp("generated")
    inventory = root / "parity_inventory.json"
    inventory.write_text(
        json.dumps({"schema": pi.SCHEMA, "rows": rows},
                   indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    out = root / "voice_coverage.json"
    assert mod.main(["--inventory", str(inventory), "--out", str(out)]) == 0
    assert out.is_file(), "generator did not write the coverage artifact"
    return json.loads(out.read_text(encoding="utf-8"))


# ------------------------------------------- (a) known guards are never real
def test_known_refusals_and_guidance_classify_as_guard(mod):
    from voicebridge import voice_commands as VC

    wrong = []
    for phrase, expected_entry in KNOWN_GUARDS:
        resolution, entry_id, _kind = mod._classify(VC, phrase)
        if entry_id != expected_entry or resolution != "guard":
            wrong.append((phrase, expected_entry, entry_id, resolution))
    assert wrong == [], f"known refusals not classified guard: {wrong}"


def test_known_working_bindings_classify_as_real(mod):
    from voicebridge import voice_commands as VC

    wrong = []
    for phrase, expected_entry in KNOWN_REAL:
        resolution, entry_id, _kind = mod._classify(VC, phrase)
        if entry_id != expected_entry or resolution != "real":
            wrong.append((phrase, expected_entry, entry_id, resolution))
    assert wrong == [], f"known working bindings not classified real: {wrong}"


# ---------------------------------- (b) no refusal entry is ever counted real
def test_refusal_backend_rows_are_never_counted_real(mod, artifact):
    from voicebridge import voice_commands as VC

    offenders = []
    for row in artifact["rows"]:
        entry_id = row.get("entry")
        if not entry_id:
            continue
        entry = VC._lookup_entry(entry_id)
        if entry is not None and mod._is_guard(entry.id, entry.backend):
            if row.get("resolution") != "guard":
                offenders.append((row.get("id"), entry_id,
                                  row.get("resolution")))
    assert offenders == [], (
        "refusal/guidance entries counted as non-guard: %s" % offenders[:10])


def test_artifact_never_marks_a_known_guard_entry_real(artifact):
    offenders = [
        (row.get("id"), row.get("voice_intent"))
        for row in artifact["rows"]
        if row.get("entry") in _GUARD_ENTRY_IDS
        and row.get("resolution") == "real"
    ]
    assert offenders == [], (
        "known refusal entries counted real in the artifact: %s" % offenders)


# --------------------------------------------- (c) counts partition honestly
def test_real_count_equals_total_minus_guard_minus_unresolved(artifact):
    summary = artifact["summary"]
    assert summary["real"] == (
        summary["rows"] - summary["guard"] - summary["unresolved"]), summary
    assert sum(summary[name] for name in RESOLUTIONS) == summary["rows"]

    for key, bucket in artifact["surfaces"].items():
        assert bucket["real"] == (
            bucket["total"] - bucket["guard"] - bucket["unresolved"]), (
            key, bucket)
        assert sum(bucket[name] for name in RESOLUTIONS) == bucket["total"], (
            key, bucket)


def test_artifact_rows_carry_a_valid_resolution(artifact):
    bad = [row for row in artifact["rows"]
           if row.get("resolution") not in RESOLUTIONS]
    assert bad == [], bad[:10]
    assert len(artifact["rows"]) == artifact["summary"]["rows"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
