"""Parity-inventory reclassification pins (wave 2b).

The generated ``docs/generated/parity_inventory.json`` reported 10 MISSING
reference rows. An audit found most were *not* real gaps; the generator's
rename map / CLI surface could not see the working Qubit counterpart:

* ``hermes computer-use screen`` -- the vendored ``qubit agent computer-use
  screen`` parser exists (Bot Desktop), registered on a different surface than
  the ``qubit agent`` walk sees.
* ``hermes plugins adopt`` / ``check-updates`` / ``trust-update-url`` -- the
  parser (``qubit_cli/subcommands/plugins.py``) and handlers
  (``qubit_cli/plugins_cmd.py``) exist.
* ``optional-mcp n8n-official`` -- the tracked Qubit dir is the identity name
  ``n8n-official``; the stale ``{"n8n-official": "n8n"}`` map looked for a
  non-existent ``n8n`` dir.
* ``GET /api/hermes/update/check`` / ``POST /api/hermes/update`` -- intentional
  brand renames to ``/api/qubit/update*`` (real handlers).

Genuinely missing (stay MISSING): ``GET /api/chat/workspaces``,
``GET /api/hermes/update/receipt``, ``WEBSOCKET /api/display/ws``.

These tests pin the corrected classification and prove the counterpart map is
validated against the real source, so it cannot rot into a fabricated row.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


RECLASSIFIED_CLI = (
    "hermes computer-use screen",
    "hermes plugins adopt",
    "hermes plugins check-updates",
    "hermes plugins trust-update-url",
)
STILL_MISSING = (
    ("route", "GET /api/chat/workspaces"),
    ("route", "WEBSOCKET /api/display/ws"),
)


# ------------------------------------------------------------- source backing

def test_cli_counterparts_are_backed_by_real_source():
    from scripts import parity_inventory as pi

    for parts in (("computer-use", "screen"), ("plugins", "adopt"),
                  ("plugins", "check-updates"), ("plugins", "trust-update-url")):
        entry = pi._verified_cli_counterpart(parts)
        assert entry is not None, parts
        assert entry["counterpart"].startswith("qubit "), entry


def test_counterpart_validation_rejects_absent_symbols():
    from scripts import parity_inventory as pi

    # Unknown command -> no counterpart.
    assert pi._verified_cli_counterpart(("plugins", "does-not-exist")) is None
    # Every backing symbol must resolve in the worktree.
    assert pi._source_has_symbol(
        "qubit_runtime/qubit_cli/plugins_cmd.py", '"adopt":') is True
    assert pi._source_has_symbol(
        "qubit_runtime/qubit_cli/subcommands/plugins.py",
        '"trust-update-url"') is True
    assert pi._source_has_symbol(
        "qubit_runtime/qubit_cli/subcommands/computer_use.py",
        "build_screen_parser") is True
    assert pi._source_has_symbol(
        "qubit_runtime/qubit_cli/plugins_cmd.py", '"no-such-handler":') is False


def test_optional_mcp_n8n_official_is_identity():
    from scripts import parity_inventory as pi

    assert pi.OPTIONAL_MCP_RENAME.get("n8n-official") is None
    assert (REPO_ROOT / "qubit_runtime" / "optional-mcps" / "n8n-official"
            / "manifest.yaml").is_file()


def test_route_rename_is_exact_and_includes_receipt():
    from scripts import parity_inventory as pi

    assert pi.ROUTE_RENAME == {
        ("GET", "/api/hermes/update/check"): "/api/qubit/update/check",
        ("GET", "/api/hermes/update/receipt"): "/api/qubit/update/receipt",
        ("POST", "/api/hermes/update"): "/api/qubit/update",
    }
    assert ("GET", "/api/hermes/update/receipt") in pi.ROUTE_RENAME


# ------------------------------------------------ classification (host-free)

def test_cli_counterparts_classify_ported(monkeypatch):
    from scripts import parity_inventory as pi

    monkeypatch.setattr(pi, "ref_cli_rows", lambda: list(pi.CLI_COUNTERPARTS))
    monkeypatch.setattr(pi, "_cli_tree", lambda argv: [])
    monkeypatch.setattr(pi, "qubit_native_refusals", lambda: {})
    rows = {r["id"]: r for r in pi.build_cli()}
    for command in RECLASSIFIED_CLI:
        assert rows[command]["classification"] == "PORTED", command
        assert "verified at" in rows[command]["evidence"]["qubit"]


def test_route_rename_classifies_ported_including_receipt(monkeypatch):
    from scripts import parity_inventory as pi
    from voicebridge import qubit_compat as qc

    monkeypatch.setattr(pi, "ref_routes", lambda: [
        {"method": "GET", "path": "/api/hermes/update/check", "evidence": "x:1"},
        {"method": "POST", "path": "/api/hermes/update", "evidence": "x:1"},
        {"method": "GET", "path": "/api/hermes/update/receipt", "evidence": "x:1"},
    ])
    rows = {r["id"]: r for r in pi.build_routes(qc)}
    assert rows["GET /api/hermes/update/check"]["classification"] == "PORTED"
    assert rows["POST /api/hermes/update"]["classification"] == "PORTED"
    # The real canonical read (GET /api/qubit/update/receipt) now exists, so the
    # reference row is a brand rename and classifies PORTED.
    assert rows["GET /api/hermes/update/receipt"]["classification"] == "PORTED"


def test_optional_mcp_row_classifies_ported(tmp_path, monkeypatch):
    from scripts import parity_inventory as pi

    ref = tmp_path / "ref"
    q = tmp_path / "q"
    (ref / "n8n-official").mkdir(parents=True)
    (q / "n8n-official").mkdir(parents=True)
    (q / "n8n-official" / "manifest.yaml").write_text("x\n")
    monkeypatch.setattr(pi, "REF_OPTIONAL_MCPS", ref)
    monkeypatch.setattr(pi, "QUBIT_OPTIONAL_MCPS", q)
    monkeypatch.setattr(pi, "REF_SKILLS", tmp_path / "ref-skills")
    monkeypatch.setattr(pi, "QUBIT_RUNTIME_SKILLS", tmp_path / "q-skills")
    monkeypatch.setattr(pi, "ref_toolsets", lambda: {})
    monkeypatch.setattr(pi, "qubit_toolsets", lambda: {})
    rows = {r["id"]: r for r in pi.build_tools()}
    assert rows["optional-mcp n8n-official"]["classification"] == "PORTED"


# -------------------------------------------------- committed artifact pins

def test_committed_artifact_reflects_reclassifications():
    from scripts import parity_inventory as pi

    payload = json.loads(pi.DEFAULT_OUT.read_text(encoding="utf-8"))
    rows = {(r["surface"], r["id"]): r for r in payload["rows"]}
    for command in RECLASSIFIED_CLI:
        assert rows[("cli", command)]["classification"] == "PORTED", command
    assert rows[("tool", "optional-mcp n8n-official")]["classification"] == "PORTED"
    for key in (("route", "GET /api/hermes/update/check"),
                ("route", "GET /api/hermes/update/receipt"),
                ("route", "POST /api/hermes/update")):
        assert rows[key]["classification"] == "PORTED", key
    for key in STILL_MISSING:
        assert rows[key]["classification"] == "MISSING", key
