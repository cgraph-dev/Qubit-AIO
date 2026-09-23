"""D3 — deterministic config/env binding manifest guard.

Owner decision 2026-09-18 (D3): every supported Hermes/OpenClaw config key and
env var must be bound to Qubit's configuration system with a name, type,
default, validation, persistence, runtime consumer, error behaviour, and
redaction rule. These tests keep the in-repo inventory current and prove it
never carries a secret value.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMMITTED = REPO / "docs/archive/2026-09-21/QUBIT_CONFIG_BINDING_MANIFEST.json"
GENERATOR = REPO / "scripts/config_binding_manifest.py"

_SECRET_VALUE_RE = re.compile(
    r"sk-[A-Za-z0-9]{10,}|pplx-[A-Za-z0-9]{10,}|"
    r"ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}")


def _build() -> dict:
    spec = importlib.util.spec_from_file_location(
        "config_binding_manifest", GENERATOR)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["config_binding_manifest"] = mod
    spec.loader.exec_module(mod)
    return mod.build()


def _committed() -> dict:
    return json.loads(COMMITTED.read_text(encoding="utf-8"))


def test_committed_manifest_is_current():
    assert COMMITTED.is_file(), COMMITTED
    assert _committed() == _build(), (
        "docs/archive/2026-09-21/QUBIT_CONFIG_BINDING_MANIFEST.json is stale; regenerate with "
        "`python scripts/config_binding_manifest.py`")


def test_manifest_has_no_secret_values():
    assert not _SECRET_VALUE_RE.search(COMMITTED.read_text(encoding="utf-8")), (
        "secret-shaped value present in the binding manifest")


def test_manifest_records_binding_metadata_for_every_key():
    doc = _committed()
    required = {"path", "type", "default", "secret", "redaction",
                "persistence", "runtime_consumer", "validation", "cli_path",
                "error_behavior", "disposition"}
    assert doc["config_keys"], "no config keys inventoried"
    for row in doc["config_keys"]:
        assert required <= set(row), (row.get("path"), required - set(row))
        assert row["redaction"] in ("value", "none"), row
        if row["secret"]:
            assert row["redaction"] == "value", row


def test_unsafe_keys_have_a_typed_disposition():
    doc = _committed()
    for row in doc["config_keys"]:
        if row["unsafe"]:
            assert row["disposition"] == "SAFE_EQUIVALENT", row


def test_env_vars_use_references():
    doc = _committed()
    for row in doc["env_vars"]:
        assert row["reference"] == f"env:{row['name']}", row


def test_openclaw_config_surface_is_disposed():
    """The out-of-runtime OpenClaw config schema is explicitly disposed."""
    doc = _committed()
    surface = doc.get("openclaw_config_surface") or {}
    assert surface.get("disposition") in (
        "SAFE_EQUIVALENT", "OPERATOR_ONLY", "IMPLEMENTED"), surface
    assert surface.get("qubit_counterpart"), surface
    assert int(surface.get("total") or 0) > 0, surface


def test_openclaw_env_rows_are_disposed():
    """Every pinned OpenClaw env name carries a typed disposition (D3)."""
    doc = _committed()
    rows = doc.get("openclaw_env_vars") or []
    assert rows, "no OpenClaw env vars inventoried"
    for row in rows:
        assert row["reference"] == f"env:{row['name']}", row
        assert row["disposition"] in (
            "IMPLEMENTED", "SAFE_EQUIVALENT", "OPERATOR_ONLY"), row
        if row["secret"]:
            assert row["redaction"] == "value", row
