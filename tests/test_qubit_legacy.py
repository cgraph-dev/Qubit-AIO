"""Regression tests for the Qubit legacy env-namespace migration adapter.

The adapter is the single generic legacy-prefix consumer: it mirrors
``QUBIT_*`` <-> ``HERMES_*`` for unset counterparts only, and migrates the
legacy home name. A previous regression assigned the Qubit prefix twice, so
``QUBIT_API_KEY`` produced the garbage ``HERMES_PI_KEY`` and legacy
``HERMES_*`` names never reached the canonical namespace. These tests pin the
correct mapping so that defect cannot return.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import qubit_legacy as L  # noqa: E402


def test_qubit_prefix_mirrors_to_hermes_counterpart():
    env = {"QUBIT_API_KEY": "abc"}
    adopted = L.install(env)
    assert env["HERMES_API_KEY"] == "abc"
    assert adopted["QUBIT_API_KEY"] == "HERMES_API_KEY"
    # No truncated/garbage counterpart may be created.
    assert "HERMES_PI_KEY" not in env
    assert "HERMES_OO" not in env


def test_hermes_prefix_mirrors_to_qubit_counterpart():
    env = {"HERMES_API_KEY": "abc"}
    adopted = L.install(env)
    assert env["QUBIT_API_KEY"] == "abc"
    assert adopted["HERMES_API_KEY"] == "QUBIT_API_KEY"


def test_existing_counterpart_is_never_overwritten():
    env = {"QUBIT_API_KEY": "new", "HERMES_API_KEY": "old"}
    L.install(env)
    assert env["QUBIT_API_KEY"] == "new"
    assert env["HERMES_API_KEY"] == "old"


def test_legacy_home_migrates_to_canonical_home(tmp_path):
    env = {"HERMES_HOME": str(tmp_path / "legacy-home")}
    adopted = L.install(env)
    assert env["QUBIT_HOME"] == str(tmp_path / "legacy-home")
    assert adopted["HERMES_HOME"] == "QUBIT_HOME"


def test_external_default_home_is_never_adopted(monkeypatch):
    external = str(Path.home() / ".hermes")
    env = {"HERMES_HOME": external}
    adopted = L.install(env)
    assert "QUBIT_HOME" not in env
    assert "HERMES_HOME" not in adopted


def test_mirror_is_idempotent_and_empty_values_ignored():
    env = {"QUBIT_API_KEY": "abc", "HERMES_EMPTY": "   "}
    L.install(env)
    first = dict(env)
    L.install(env)
    assert env == first
    assert "QUBIT_EMPTY" not in env
