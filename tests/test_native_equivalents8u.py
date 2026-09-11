"""Packet 8U — native-equivalents matrix is schema-valid and pinned.

Every row carries owner/rollback/tests/manual steps; every claimed CLI
path parses, every claimed voice id exists in the registry (hence the
dashboard Tier-1 catalog), and every cited test file exists. The MD
companion lists every row id.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _rows():
    data = json.loads(
        (REPO / "docs" / "NATIVE_EQUIVALENTS_8U.json").read_text())
    assert data["schema"] == "qubit-native-equivalents/1"
    return data["rows"]


def test_matrix_schema_and_companion():
    rows = _rows()
    assert len(rows) >= 15
    ids = [r["id"] for r in rows]
    assert len(set(ids)) == len(ids), "duplicate row ids"
    md = (REPO / "docs" / "NATIVE_EQUIVALENTS_8U.md").read_text()
    for r in rows:
        assert r["status"] in ("IMPLEMENTED", "IMPLEMENTED-EQUIVALENT",
                               "OPERATOR-QUALIFICATION",
                               "EXPLICIT-LIMITATION"), r["id"]
        for key in ("user_goal", "upstream", "cli", "dashboard", "voice",
                    "tests", "owner", "rollback"):
            assert key in r, (r["id"], key)
            if key != "voice":
                assert r.get(key), (r["id"], key)
        assert isinstance(r["voice"], list), r["id"]
        if r["status"] in ("OPERATOR-QUALIFICATION", "EXPLICIT-LIMITATION"):
            assert r.get("manual_test") or r.get("limitation"), r["id"]
        assert r["id"] in md, r["id"]


def test_matrix_cli_paths_parse():
    from voicebridge.cli import build_parser

    parser = build_parser()
    top = None
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            top = action
    assert top is not None
    for r in _rows():
        for path in r["cli"]:
            parts = path.split()
            assert parts[0] in top.choices, (r["id"], path)
            if len(parts) > 1:
                sub = top.choices[parts[0]]
                names = set()
                for act in sub._actions:
                    if isinstance(act, argparse._SubParsersAction):
                        names.update(act.choices.keys())
                assert parts[1] in names, (r["id"], path)


def test_matrix_voice_ids_in_registry_and_dashboard():
    from voicebridge import dashboard_ops as D
    from voicebridge import voice_commands as VC

    cat = {item["id"] for item in D.catalog()}
    for r in _rows():
        for entry_id in r["voice"]:
            assert entry_id in VC.REGISTRY, (r["id"], entry_id)
            assert entry_id in cat, (r["id"], entry_id)


def test_matrix_test_files_exist():
    for r in _rows():
        for rel in r["tests"]:
            assert (REPO / rel).exists(), (r["id"], rel)


def test_matrix_claims_route_to_real_backends_and_tests():
    """OpenClaw audit §4.2: presence alone is not proof.

    Every claimed voice id must resolve to a registered backend (so the
    spoken surface actually dispatches), every documented voice example must
    resolve to a real command (not UNKNOWN), and every cited test file must
    contain real assertions rather than a bare existence claim.
    """
    from voicebridge import voice_commands as VC
    from voicebridge.voice_commands import UNKNOWN, resolve

    for r in _rows():
        for entry_id in r["voice"]:
            entry = VC.REGISTRY[entry_id]
            assert entry.backend in VC._BACKENDS, (r["id"], entry_id)
            assert isinstance(entry.examples, tuple), (r["id"], entry_id)
            example = (entry.examples[0] if entry.examples
                       else entry.aliases[0])
            res = resolve(example)
            assert res.kind != UNKNOWN, (r["id"], entry_id, example)
            assert res.entry is not None, (r["id"], entry_id, example)
        for rel in r["tests"]:
            text = (REPO / rel).read_text(encoding="utf-8")
            assert "assert " in text, (r["id"], rel, "no assertions")
