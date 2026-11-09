"""Every shim handler ``handlers_extra`` reaches for must actually exist.

The defect this pins: ``handlers_extra.workspace_list`` called
``WS.h_wb_workspace_list(...)``, but ``workbench_shim/workspace.py`` exposes
the function as ``workspace_list`` and only pins the ``h_wb_*`` name inside its
``HANDLERS`` dict. The call raised ``AttributeError``, the surrounding
``except`` turned it into ``503 WB_BACKEND_UNAVAILABLE``, and the live
``/qubit-workbench/api/list`` answered 503 forever — the ported client's file
tree could never render. Three sibling wrappers (``crons_recent``,
``approval_pending``, ``approval_respond``, ``cron_verb``) had the same shape.

A ``try``/``except`` that converts every ``AttributeError`` into a typed
refusal will hide a typo'd function name indefinitely, so the resolution is
pinned statically here: srcwalk the four wrappers for ``MOD.attr`` calls into
``workbench_shim`` and assert each name exists on that module.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

import voicebridge.workbench_shim as SHIM
from voicebridge.workbench_shim import approvals, crons, sessions, system, workspace

#: Short aliases ``handlers_extra`` binds with ``from ..workbench_shim import X``.
_ALIASES = {
    "AP": approvals,
    "CR": crons,
    "WS": workspace,
    "SS": sessions,
    "SYS": system,
    "_shim": SHIM,
}


def _module_attr_calls(tree: ast.AST):
    """Yield ``(alias, attr, lineno)`` for every ``ALIAS.attr`` attribute read."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in _ALIASES:
                yield node.value.id, node.attr, node.lineno


def test_every_shim_attribute_handlers_extra_calls_exists():
    src = pathlib.Path(
        "voicebridge/workbench_ui/handlers_extra.py"
    ).read_text(encoding="utf-8")
    missing = []
    for alias, attr, lineno in _module_attr_calls(ast.parse(src)):
        module = _ALIASES[alias]
        if not hasattr(module, attr):
            missing.append(f"handlers_extra.py:{lineno} calls {alias}.{attr}, "
                           f"but {module.__name__} has no attribute {attr!r}")
    assert not missing, "unresolvable shim call(s):\n  " + "\n  ".join(missing)


@pytest.mark.parametrize("name", ["workspace_list", "crons_recent",
                                  "approval_pending", "approval_respond"])
def test_the_wrappers_resolve_instead_of_refusing(name):
    """The named wrapper must not answer WB_BACKEND_UNAVAILABLE for 'missing attr'."""
    from voicebridge.workbench_ui import handlers_extra as HE

    fn = getattr(HE, name)
    cfg = None  # these resolve their authority lazily; a None cfg must not
    try:        # raise AttributeError before reaching the adapter
        status, payload = fn(cfg, {"path": "."}, {}, {})
    except AttributeError as exc:  # pragma: no cover - the defect itself
        pytest.fail(f"{name} raised AttributeError: {exc}")
    detail = (payload or {}).get("detail", "") if isinstance(payload, dict) else ""
    # Only a MODULE-attribute failure is the defect. ``cfg=None`` legitimately
    # produces "'NoneType' object has no attribute 'state_dir'", which is a
    # different (and expected) refusal for this no-config harness.
    assert "module 'voicebridge" not in detail, (
        f"{name} still fails on a missing module attribute: {detail}")


def test_cron_action_is_reached_through_the_route_module():
    """``h_wb_cron_action`` lives in the route module, not the crons shim."""
    from voicebridge import workbench_ui_routes as R

    assert hasattr(R, "h_wb_cron_action")
    assert not hasattr(crons, "h_wb_cron_action"), (
        "if crons ever grows h_wb_cron_action, the handlers_extra comment is stale")
