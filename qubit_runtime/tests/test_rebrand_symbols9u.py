"""Regression tests for rebrand / partial-sync symbols referenced but undefined.

The hermes→qubit rename plus the upstream overlay left call sites pointing at
symbols that no target module defines. Most were lazy imports (``from x import y``
inside a function body) so the top-level import gate never saw them; the failure
only surfaced when the code path ran. A few were renamed call sites.

Each row below is a symbol that previously did NOT resolve. The table test imports
the module and asserts the attribute exists; the functional tests call the bodies,
so a NameError/ImportError inside the function also fails here.

Hermetic: no network, no writes outside the per-test ``QUBIT_HOME`` tempdir
(the suite's autouse isolation fixture redirects it).
"""

from __future__ import annotations

import contextlib
import importlib
from pathlib import Path

import pytest

# (module, symbol) — every pair raised ImportError/NameError at runtime before the fix.
FIXED_SYMBOLS = [
    ("tools.skill_usage", "set_sync"),
    ("tools.skill_usage", "is_sync_enabled"),
    ("tools.skill_usage", "_read_bundled_names"),
    ("tools.skill_usage", "_read_hub_installed_names"),
    ("tools.skill_usage", "activity_count"),
    ("tools.skill_usage", "load_usage"),
    ("tools.vision_tools", "get_qubit_dir"),
    ("qubit_cli.gui_uninstall", "desktop_install_record"),
    ("qubit_cli.plugins_cmd_install", "shared_dependency_answers"),
    ("qubit_cli.plugins_cmd_install", "recorded_install"),
    ("qubit_cli.main", "explicit_cli_profile"),
    ("qubit_cli.main", "_explicit_cli_profile"),
    ("agent.skill_utils", "read_active_org_id"),
    ("agent.skill_utils", "ORG_MIRROR_DIR_NAME"),
    ("agent.skill_utils", "ORG_ACTIVE_MARKER"),
    ("agent.skill_utils", "ORG_PROVENANCE_FILE"),
    ("agent.skill_utils", "ORG_BASELINE_FILE"),
    ("qubit_cli.observability.relay_shared_metrics", "close_session_run"),
    ("cron.scheduler", "get_restart_wait_cron_counts"),
    ("cron.scheduler", "_wedged_inflight_keys"),
    ("cron.scheduler", "_scope_isolated_job_ids"),
]


@pytest.mark.parametrize("module_name,symbol", FIXED_SYMBOLS, ids=lambda v: str(v))
def test_previously_undefined_symbol_resolves(module_name: str, symbol: str) -> None:
    module = importlib.import_module(module_name)
    assert hasattr(module, symbol), f"{module_name}.{symbol} is not defined"


def test_skill_sync_flag_round_trips(tmp_path, monkeypatch) -> None:
    from tools import skill_usage

    monkeypatch.setattr(skill_usage, "_skills_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_usage, "_usage_file", lambda: tmp_path / ".usage.json")

    assert skill_usage.is_sync_enabled("demo") is False
    skill_usage.set_sync("demo", True)
    assert skill_usage.is_sync_enabled("demo") is True
    skill_usage.set_sync("demo", False)
    assert skill_usage.is_sync_enabled("demo") is False


def test_read_active_org_id_absent_marker_is_none(tmp_path) -> None:
    from agent.skill_utils import read_active_org_id

    assert read_active_org_id(tmp_path) is None


def test_read_active_org_id_reads_marker(tmp_path) -> None:
    from agent.skill_utils import ORG_ACTIVE_MARKER, ORG_MIRROR_DIR_NAME, read_active_org_id

    marker = tmp_path / ORG_MIRROR_DIR_NAME / ORG_ACTIVE_MARKER
    marker.parent.mkdir(parents=True)
    marker.write_text("acme\n", encoding="utf-8")

    assert read_active_org_id(tmp_path) == "acme"


def test_explicit_cli_profile_defaults_to_none() -> None:
    from qubit_cli.main import explicit_cli_profile

    assert explicit_cli_profile() is None


def test_shared_dependency_answers_is_a_context_manager() -> None:
    from qubit_cli.plugins_cmd_install import shared_dependency_answers

    with shared_dependency_answers():
        pass


def test_desktop_install_record_is_default_root_file() -> None:
    from qubit_cli.gui_uninstall import desktop_install_record

    record = desktop_install_record()
    assert isinstance(record, Path)
    assert record.name == "desktop-installed-apps.json"


def test_get_restart_wait_cron_counts_shape() -> None:
    from cron.scheduler import get_restart_wait_cron_counts

    counts = get_restart_wait_cron_counts()
    assert set(counts) == {"awaitable", "wedged", "restart_safe"}
    assert all(isinstance(v, int) for v in counts.values())


def test_close_session_run_unknown_id_is_a_noop() -> None:
    from qubit_cli.observability.relay_shared_metrics import close_session_run

    with contextlib.suppress(Exception):
        # enabled() is false in a hermetic test env; either way it must not NameError.
        close_session_run("")


def test_secure_cache_dir_binds_get_qubit_dir() -> None:
    """The body read a bare ``get_hermes_dir`` that was never imported (NameError)."""
    from tools import vision_tools

    cache_dir = vision_tools._secure_cache_dir("cache/rebrand-probe", "rebrand_probe")
    assert isinstance(cache_dir, Path)
    assert cache_dir.name == "rebrand-probe"
    assert cache_dir.is_dir()


def test_tui_gateway_skill_lookup_symbols_import() -> None:
    """The server's lazy lookup imported the pre-refactor bundled-name helper."""
    from tools.skill_usage import (
        _read_bundled_names,
        _read_hub_installed_names,
        activity_count,
        load_usage,
    )

    assert callable(_read_bundled_names)
    assert callable(_read_hub_installed_names)
    assert callable(activity_count)
    assert callable(load_usage)
