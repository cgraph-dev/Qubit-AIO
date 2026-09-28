# tests/test_rebase_c3_fork_patches.py
"""C3 gate: the genuine Qubit fork patches in ``tools/``, ``providers/`` and
``qubit_state*`` re-applied on top of the engine v0.21.5 reset.

Smallest set from ``FORK_PATCH_MATRIX.md`` §2.2/§4:

* fix-preserve  ``tools/bot_relay.py`` + ``tools/environments/local.py``
  (Qubit-first executable resolution; ``hermes`` is a read-old fallback only);
* D1  ``tools/file_tools_write_guards.py`` (newer Qubit guards + ``_qubit_*``
  N4 cache keys; no ``_hermes_*`` key mismatch);
* D2  ``tools/url_safety.py`` (``_qubit_ssrf_guarded`` idempotency + the
  ``security.fake_ip_ranges`` / IPv4-translated protections, qubit contextvars);
* D3  ``toolsets.py`` (``browser_vault_*`` + ``manage_connections`` +
  ``setup`` + ``CLIENT_SURFACE_TOOLSETS``; ``_QUBIT_*`` constants);
* ``providers/__init__.py`` (per-home ``_HOME_LAYERS`` + ``get_qubit_home``).

The tests import the runtime from its own tree under an isolated home and never
write a product tree.
"""
from __future__ import annotations

import importlib
import ipaddress
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "qubit_runtime"


@pytest.fixture
def runtime_on_path(tmp_path, monkeypatch):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.syspath_prepend(str(RUNTIME))
    yield


# --------------------------------------------------------------------------- #
# fix-preserve: Qubit-first executable resolution
# --------------------------------------------------------------------------- #

def test_bot_relay_qubit_first_executable(runtime_on_path, tmp_path, monkeypatch):
    br = importlib.import_module("tools.bot_relay")
    exe_dir = tmp_path / "bin"
    exe_dir.mkdir()
    monkeypatch.setattr(sys, "executable", str(exe_dir / "python"))
    monkeypatch.setattr(br.shutil, "which", lambda name: None)

    (exe_dir / "qubit").write_text("")
    assert br._qubit_cli() == str(exe_dir / "qubit")

    # The legacy hermes shim is still a read-old fallback for pre-rename installs.
    (exe_dir / "qubit").unlink()
    (exe_dir / "hermes").write_text("")
    assert br._qubit_cli() == str(exe_dir / "hermes")

    # No sibling -> shutil.which("qubit") wins over a bare name.
    (exe_dir / "hermes").unlink()
    monkeypatch.setattr(
        br.shutil, "which", lambda name: "/opt/q/bin/qubit" if name == "qubit" else None
    )
    assert br._qubit_cli() == "/opt/q/bin/qubit"

    # No sibling, nothing on PATH -> the Qubit bare name, never bare "hermes".
    monkeypatch.setattr(br.shutil, "which", lambda name: None)
    assert br._qubit_cli() == "qubit"


def test_local_environment_qubit_first_executable(runtime_on_path, tmp_path, monkeypatch):
    loc = importlib.import_module("tools.environments.local")
    exe_dir = tmp_path / "bin"
    exe_dir.mkdir()
    (exe_dir / "qubit").write_text("")
    monkeypatch.setattr(sys, "executable", str(exe_dir / "python"))
    monkeypatch.setattr(sys, "argv", [""])
    monkeypatch.setattr(loc.shutil, "which", lambda name: None)
    monkeypatch.setattr(loc, "_QUBIT_BIN_DIR", loc._SENTINEL)

    assert loc._resolve_qubit_bin_dir() == str(exe_dir)


# --------------------------------------------------------------------------- #
# D1 file_tools_write_guards.py
# --------------------------------------------------------------------------- #

def test_write_guards_keep_n4_qubit_keys(runtime_on_path):
    src = (RUNTIME / "tools" / "file_tools_write_guards.py").read_text(encoding="utf-8")
    for token in (
        "_qubit_config_resolved",
        "_qubit_config_resolved_loaded",
        "_real_qubit_home_cached",
        "_real_qubit_home_loaded",
        "_get_qubit_config_resolved",
        "_get_real_qubit_home",
        "_qubit_home_real",
        "_qubit_exempt_homes",
    ):
        assert token in src, f"D1: missing Qubit cache key {token!r}"
    for stale in (
        "_hermes_config_resolved",
        "_get_hermes_config_resolved",
        "_hermes_home_real",
        "_real_hermes_home",
    ):
        assert stale not in src, f"D1: stale Hermes cache key {stale!r} resurfaced"
    # newer upstream guards must survive the rename
    for guard in (
        "_stale_overwrite_blocker",
        "_stale_write_refusal",
        "get_nt_namespace_error",
        "has_binary_extension",
        "is_sqlite_sidecar",
    ):
        assert guard in src, f"D1: newer write guard {guard!r} regressed"

    fw = importlib.import_module("tools.file_tools_write_guards")
    # The N4 KeyError came from cache globals whose names did not match the
    # module-level slots passed to the loader; the Qubit-keyed getters must
    # resolve under the isolated home without raising.
    assert fw._get_qubit_config_resolved() is None or isinstance(fw._get_qubit_config_resolved(), str)
    assert fw._get_real_qubit_home() is None or isinstance(fw._get_real_qubit_home(), str)
    assert isinstance(fw._qubit_exempt_homes(), tuple)


# --------------------------------------------------------------------------- #
# D2 tools/url_safety.py
# --------------------------------------------------------------------------- #

def test_url_safety_ssrf_idempotency_and_fake_ip_extensions(runtime_on_path):
    us = importlib.import_module("tools.url_safety")
    src = (RUNTIME / "tools" / "url_safety.py").read_text(encoding="utf-8")

    assert "_qubit_ssrf_guarded" in src
    assert "hermes_ssrf" not in src, "D2: Hermes SSRF contextvar name resurfaced"
    assert "qubit_ssrf" in src

    for attr in (
        "_resolve_fake_ip_ranges",
        "_global_fake_ip_ranges",
        "_FAKE_IP_UNDECLARABLE_NETWORKS",
        "_IPV4_TRANSLATED_NETWORK",
        "_embedded_ipv4",
        "_is_declared_fake_ip",
    ):
        assert hasattr(us, attr), f"D2: security extension {attr!r} dropped"

    assert us._FAKE_IP_UNDECLARABLE_NETWORKS, "D2: undeclarable network set empty"
    mapped = us._embedded_ipv4(ipaddress.ip_address("::ffff:127.0.0.1"))
    assert str(mapped) == "127.0.0.1", "D2: IPv4-translated unwrap regressed"


# --------------------------------------------------------------------------- #
# D3 toolsets.py
# --------------------------------------------------------------------------- #

def test_toolsets_core_bundle_and_client_surfaces(runtime_on_path):
    ts = importlib.import_module("toolsets")
    assert ts._QUBIT_CORE_TOOLS
    for tool in ("browser_vault_list", "browser_vault_unlock", "manage_connections"):
        assert tool in ts._QUBIT_CORE_TOOLS, f"D3: {tool!r} missing from core tools"
    assert ts.CLIENT_SURFACE_TOOLSETS == frozenset({"project", "desktop_ui"})
    names = ts.get_toolset_names()
    assert "setup" in names
    assert "desktop_ui" in names
    assert "project" in names
    assert ts.resolve_toolset("desktop_ui")
    src = (RUNTIME / "toolsets.py").read_text(encoding="utf-8")
    assert "_HERMES_CORE_TOOLS" not in src
    assert "_HERMES_WEBHOOK_SAFE_TOOLS" not in src


# --------------------------------------------------------------------------- #
# providers/__init__.py
# --------------------------------------------------------------------------- #

def test_providers_home_layer_and_qubit_home(runtime_on_path):
    providers = importlib.import_module("providers")
    assert hasattr(providers, "_HOME_LAYERS")
    assert providers.list_providers(), "provider registry empty"
    src = (RUNTIME / "providers" / "__init__.py").read_text(encoding="utf-8")
    assert "get_qubit_home" in src
    assert "get_hermes_home" not in src, "providers: Hermes home accessor resurfaced"


# --------------------------------------------------------------------------- #
# import + registry smokes for every bucket
# --------------------------------------------------------------------------- #

def test_bucket_import_and_registry_smoke(runtime_on_path):
    for module in (
        "qubit_state",
        "qubit_state_common",
        "qubit_state_fts",
        "qubit_state_holders",
        "qubit_state_schema",
        "qubit_state_sessions",
        "providers",
        "providers.base",
        "toolsets",
        "tools.bot_relay",
        "tools.environments.local",
        "tools.file_tools_write_guards",
        "tools.url_safety",
    ):
        importlib.import_module(module)
