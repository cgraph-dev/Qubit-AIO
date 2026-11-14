"""P6-04 panel inventory: every panel declares a real authority and an action.

Hermetic: no store, no service, no network. The tests pin the registry contract
and every negative check the verifier relies on:

* coverage of the required panel set, unique ids;
* every authority module imports and the symbol resolves;
* every declared compat route exists in the live ROUTES table;
* all six states, both permission scopes, a deep link, split/stack and a
  source command are present on every row;
* a panel without a real action or a typed refusal fails;
* a renderer can never be declared as a data authority;
* the verifier returns 0 on the real registry, 1 on a broken registry and 2
  on an internal error.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


from voicebridge import qubit_compat as HC
from voicebridge.business import panel_inventory as PI

ROOT = Path(__file__).resolve().parents[2]
VERIFIER = ROOT / "scripts" / "verify_panel_inventory.py"


def _routes() -> set[tuple[str, str]]:
    return {
        (str(route.get("method", "")), str(route.get("pattern", "")))
        for route in HC.ROUTES
    }


def test_registry_covers_the_required_panel_set_once():
    ids = PI.panel_ids()
    assert len(ids) == len(set(ids))
    assert set(PI.REQUIRED_PANELS).issubset(set(ids))
    assert len(ids) >= 21


def test_every_row_declares_authority_permissions_states_action_and_layout():
    for row in PI.panel_rows():
        pid = row["panel_id"]
        authority = row["authority"]
        assert authority["module"].startswith(
            ("voicebridge.", "qubit_runtime.")), pid
        assert authority["symbol"], pid
        assert row["permissions"]["read"], pid
        assert row["permissions"]["write"], pid
        assert set(PI.REQUIRED_STATES).issubset(set(row["states"])), pid
        assert row["action"]["kind"] in ("real", "typed_refusal"), pid
        assert row["action"]["label"], pid
        assert row["deep_link"].startswith("/"), pid
        assert row["layout"]["split"] and row["layout"]["stack"], pid
        assert row["source_command"], pid


def test_every_authority_module_and_symbol_resolves():
    for row in PI.panel_rows():
        module_name = row["authority"]["module"]
        module = importlib.import_module(module_name)
        target = module
        for part in row["authority"]["symbol"].split("."):
            target = getattr(target, part)


def test_every_declared_route_exists_in_the_live_table():
    routes = _routes()
    for row in PI.panel_rows():
        authority = row["authority"]
        if authority["route"]:
            assert (authority["route_method"], authority["route"]) in routes, row
        action = row["action"]
        if action["kind"] == "real" and action.get("route"):
            assert (action["method"], action["route"]) in routes, row
        if action["kind"] == "typed_refusal" and action.get("route"):
            assert ("GET", action["route"]) in routes, row


def test_real_registry_has_no_validation_problems():
    assert PI.validate_rows() == []


def test_duplicate_panel_id_is_rejected():
    rows = PI.panel_rows()
    rows.append(dict(rows[0]))
    codes = {p["code"] for p in PI.validate_rows(rows)}
    assert "PANEL_ID_DUPLICATE" in codes


def test_missing_required_panel_is_rejected():
    rows = [r for r in PI.panel_rows() if r["panel_id"] != "settings"]
    codes = {p["code"] for p in PI.validate_rows(rows)}
    assert "PANEL_REQUIRED_MISSING" in codes


def test_missing_action_and_refusal_is_rejected():
    rows = PI.panel_rows()
    rows[0]["action"] = {"kind": "none", "label": "nothing"}
    codes = {p["code"] for p in PI.validate_rows(rows)}
    assert "ACTION_KIND_INVALID" in codes


def test_typed_refusal_without_detail_is_rejected():
    rows = PI.panel_rows()
    refusal = next(
        r for r in rows if r["action"]["kind"] == "typed_refusal")
    refusal["action"] = dict(refusal["action"], detail="")
    codes = {p["code"] for p in PI.validate_rows(rows)}
    assert "REFUSAL_DETAIL_MISSING" in codes


def test_renderer_cannot_be_an_authority():
    rows = PI.panel_rows()
    rows[0]["authority"] = dict(
        rows[0]["authority"], module="frontend.qubit-app.game.app.Panel")
    codes = {p["code"] for p in PI.validate_rows(rows)}
    assert "AUTHORITY_IS_RENDERER" in codes


def test_fabricated_module_is_rejected():
    rows = PI.panel_rows()
    rows[0]["authority"] = dict(
        rows[0]["authority"], module="voicebridge.business.not_a_real_module")
    codes = {p["code"] for p in PI.validate_rows(rows)}
    assert "AUTHORITY_MODULE_UNRESOLVED" in codes


def test_fabricated_route_is_rejected():
    rows = PI.panel_rows()
    rows[0]["authority"] = dict(
        rows[0]["authority"], route="/api/does/not/exist",
        route_method="GET")
    codes = {p["code"] for p in PI.validate_rows(rows)}
    assert "AUTHORITY_ROUTE_UNKNOWN" in codes


def test_unknown_state_is_rejected():
    rows = PI.panel_rows()
    rows[0]["states"] = list(rows[0]["states"]) + ["optimistic"]
    codes = {p["code"] for p in PI.validate_rows(rows)}
    assert "STATE_UNKNOWN" in codes


def test_verifier_exit_codes(monkeypatch, capsys):
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        module = importlib.import_module("verify_panel_inventory")
    finally:
        sys.path.pop(0)

    assert module.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["panels_total"] == len(PI.REQUIRED_PANELS)
    assert payload["problems"] == []

    monkeypatch.setattr(PI, "PANELS", ())
    assert module.main(["--json"]) == 1
    broken = json.loads(capsys.readouterr().out)
    assert broken["ok"] is False
    assert any(
        p["code"] == "PANEL_REQUIRED_MISSING" for p in broken["problems"])

    monkeypatch.setattr(
        module, "_route_index",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(PI, "PANELS", PI._build_panels())
    assert module.main(["--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "boom" in payload["error"]
