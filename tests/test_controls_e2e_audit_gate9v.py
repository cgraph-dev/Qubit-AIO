"""Regression: the live-controls audit must not mutate by default.

The 2026-11-10 incident came from running `scripts/controls_e2e_audit.py`
against the operator dashboard: its profile delete controls removed
`e2e-audit-prof` and the gateway lost its `state.db` generation guard.  The
mutating audits are now gated behind `--allow-live-mutations`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_audit():
    path = Path(__file__).resolve().parents[1] / "scripts" / "controls_e2e_audit.py"
    spec = importlib.util.spec_from_file_location("controls_e2e_audit9v", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeHarness:
    def __init__(self):
        self.rows = []
        self.gotos = []

    def run(self, page_path, control, action, fn, **kwargs):
        self.rows.append((page_path, control, action, fn, kwargs))

    def goto(self, path):
        self.gotos.append(path)


def _assert_one_skip(module, audit, control):
    module.ALLOW_LIVE_MUTATIONS = False
    harness = _FakeHarness()
    audit(harness)
    assert harness.gotos == []
    assert len(harness.rows) == 1
    page_path, got_control, action, fn, _kwargs = harness.rows[0]
    assert got_control == control
    assert "refused" in action
    result = fn()
    assert result["disposition"] == "E2E_SKIP"
    assert "--allow-live-mutations" in result["detail"]


def test_profile_mutations_are_skipped_by_default():
    module = _load_audit()
    _assert_one_skip(module, module.audit_profiles, "profiles_create_delete")


def test_webhook_mutations_are_skipped_by_default():
    module = _load_audit()
    _assert_one_skip(module, module.audit_webhooks, "webhooks_create_delete")
