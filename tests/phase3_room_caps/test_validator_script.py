"""P3-02 — the acceptance script contract (hermetic, in-process)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from voicebridge.business import room_capabilities as rc
from voicebridge.business.errors import BusinessError

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "validate_room_capabilities.py"
)


def _module():
    spec = importlib.util.spec_from_file_location(
        "validate_room_capabilities", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_json_report_is_pass_and_covers_every_room(capsys):
    module = _module()
    assert module.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
    assert payload["manifest_count"] >= 1
    coverage = payload["coverage"]
    assert coverage["total_rooms"] == 300
    assert coverage["uncovered_rooms"] == 0
    assert coverage["covered_rooms"] == coverage["total_rooms"]
    assert payload["counts_by_department"]["trading"] == 7


def test_unknown_flag_is_a_usage_error():
    module = _module()
    with pytest.raises(SystemExit) as exc:
        module.main(["--not-a-real-flag"])
    assert exc.value.code == 2


def test_invalid_manifest_exits_one(capsys, monkeypatch):
    module = _module()

    def _boom(*args, **kwargs):
        raise BusinessError(rc.ROOM_CAPABILITY_UNCOVERED_CODE, "room has no manifest")

    monkeypatch.setattr(module, "build_shipped_registry", _boom)
    assert module.main(["--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "FAIL"
    assert payload["code"] == rc.ROOM_CAPABILITY_UNCOVERED_CODE
