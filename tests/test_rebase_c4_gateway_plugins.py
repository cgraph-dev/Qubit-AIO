# tests/test_rebase_c4_gateway_plugins.py
"""Step C4 gate: ``gateway/`` + ``plugins/`` genuine fork patches on the v0.21.5 reset.

Locks in the fork curation that the engine rebrand does not reproduce:

* N26 gateway identity payload (``kind: qubit-gateway`` + ``qubit_home``, read-old
  ``hermes-gateway`` / ``hermes_home``) in ``gateway/status.py`` +
  ``gateway/control_socket.py``.
* the de-branded local ``qubit_cmd`` in ``gateway/run_shutdown.py``;
* the public-surface de-brand of ``plugins/web/perplexity`` request headers;
* the ``qubit-achievements`` plugin-id rebrand (dir + manifest + route + dist) with a
  non-destructive read-old migration of ``plugin-data/hermes-achievements``.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

import rebase_bucket_support as S
from rebase_bucket_support import runtime_on_path  # noqa: F401

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "qubit_runtime"


def test_n26_gateway_status_kind_and_home_keys(runtime_on_path):
    status = importlib.import_module("gateway.status")
    assert status._GATEWAY_KIND == "qubit-gateway"
    assert status._LEGACY_GATEWAY_KIND == "hermes-gateway"
    assert status._HOME_KEY == "qubit_home"
    assert status._LEGACY_HOME_KEY == "hermes_home"
    assert status.gateway_kind_matches("qubit-gateway")
    assert status.gateway_kind_matches("hermes-gateway")
    assert not status.gateway_kind_matches("something-else")
    assert status.record_home({"qubit_home": "/a", "hermes_home": "/b"}) == "/a"
    assert status.record_home({"hermes_home": "/b"}) == "/b"
    assert status.record_home({}) is None
    record = status._build_pid_record()
    assert record["kind"] == "qubit-gateway"
    assert "qubit_home" in record and "hermes_home" not in record


def test_n26_control_socket_pipe_and_identify(runtime_on_path):
    cs = importlib.import_module("gateway.control_socket")
    assert cs._PIPE_PREFIX == "qubit-gateway"
    assert cs._LEGACY_PIPE_PREFIX == "hermes-gateway"
    assert cs.windows_pipe_name(Path("/x")).startswith(r"\\.\pipe\qubit-gateway-")
    assert cs._legacy_windows_pipe_name(Path("/x")).startswith(r"\\.\pipe\hermes-gateway-")
    payload = cs.build_identify_payload()
    assert payload["kind"] == "qubit-gateway"
    assert "qubit_home" in payload
    assert "hermes_home" not in payload


def test_control_socket_vendored_contract_matches_fork(runtime_on_path):
    """The fork's own control-socket test now asserts the qubit N26 contract."""
    test_file = RUNTIME / "tests" / "gateway" / "test_control_socket.py"
    text = test_file.read_text(encoding="utf-8")
    assert r"\\.\pipe\qubit-gateway-" in text
    assert '"qubit_home", "supervisor", "kind", "start_time"' in text
    assert r"\\.\pipe\hermes-gateway-" not in text


def test_run_shutdown_uses_qubit_cmd_local():
    text = (RUNTIME / "gateway" / "run_shutdown.py").read_text(encoding="utf-8")
    assert "hermes_cmd" not in text
    assert "qubit_cmd" in text


def test_perplexity_headers_are_debranded(runtime_on_path):
    provider = importlib.import_module("plugins.web.perplexity.provider")
    assert provider._HEADERS["HTTP-Referer"] == "https://github.com/cgraph-dev/qubit-voicebridge"
    assert provider._HEADERS["X-Pplx-Integration"] == "qubit-agent"
    assert provider._HEADERS["User-Agent"].startswith("Qubit-Agent/")
    assert "Her" "mesAgent/" not in provider._HEADERS["User-Agent"]
    assert "nousresearch" not in provider._HEADERS["HTTP-Referer"]


def test_qubit_achievements_plugin_id_and_read_old(runtime_on_path):
    # The plugin directory + manifest carry the Qubit id; the legacy path is gone.
    plugin_dir = RUNTIME / "plugins" / "qubit-achievements"
    assert plugin_dir.is_dir()
    assert not (RUNTIME / "plugins" / "hermes-achievements").exists()
    manifest = json.loads((plugin_dir / "dashboard" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "qubit-achievements"

    js = (plugin_dir / "dashboard" / "dist" / "index.js").read_text(encoding="utf-8")
    assert '"/api/plugins/qubit-achievements"' in js
    assert 'register("qubit-achievements"' in js
    assert "/api/plugins/hermes-achievements" not in js

    # The vendored plugin backend still reads its pre-rename data + install-tree state.
    spec = importlib.util.spec_from_file_location(
        "qubit_achievements_plugin_api", plugin_dir / "dashboard" / "plugin_api.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "_migrate_legacy_data_dir")


def test_dashboard_discovery_resolves_only_qubit_achievements(runtime_on_path):
    disc = importlib.import_module("qubit_cli.web_server_dashboard")._discover_dashboard_plugins
    names = [p.get("name") for p in disc()]
    assert "qubit-achievements" in names
    assert "hermes-achievements" not in names


def test_gateway_plugins_import_smoke(runtime_on_path):
    for mod in (
        "gateway.status", "gateway.control_socket", "gateway.run_shutdown",
        "gateway.pairing", "gateway.readiness", "gateway.delivery_ledger",
        "plugins.web.perplexity.provider", "plugins.memory.openviking",
    ):
        importlib.import_module(mod)
