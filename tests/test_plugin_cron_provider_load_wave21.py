"""Cron-provider plugins are classified ``exclusive`` (not eagerly loaded).

The vendored plugin manifest detector must recognize a cron-provider source
(``register_cron_scheduler`` / ``CronScheduler``) the same way it recognizes a
memory provider, so ``cron_providers/chronos`` routes to its own discovery
instead of the general plugin manager whose ``PluginContext`` has no
``register_cron_scheduler`` (the reference fix for the eager-import failure).

Regression guard: the Qubit port dropped the cron markers, so ``chronos`` was
imported by the general manager and died with
``'PluginContext' object has no attribute 'register_cron_scheduler'``.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _ensure_fork():
    from voicebridge import qubit_runtime

    qubit_runtime.ensure_path()


def test_detect_kind_recognizes_cron_provider_markers():
    _ensure_fork()
    from qubit_cli.plugins_manifest import _detect_kind_from_source

    assert _detect_kind_from_source(
        "def register(ctx):\n    ctx.register_cron_scheduler(x)\n") == "exclusive"
    assert _detect_kind_from_source(
        "from cron.scheduler_provider import CronScheduler\n") == "exclusive"
    # The memory marker must keep working (no regression).
    assert _detect_kind_from_source(
        "ctx.register_memory_provider(p)\n") == "exclusive"


def test_chronos_source_is_detected_exclusive():
    chronos = (REPO_ROOT / "qubit_runtime" / "plugins" / "cron_providers"
               / "chronos" / "__init__.py")
    assert chronos.is_file(), chronos
    _ensure_fork()
    from qubit_cli.plugins_manifest import _detect_kind_from_source

    assert _detect_kind_from_source(chronos.read_text()) == "exclusive"


def test_chronos_is_discovered_and_not_a_general_plugin(tmp_path, monkeypatch):
    """Discovery still lists chronos (its own provider path), and the general
    plugin scan does not claim it as a standalone plugin."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    cfg_file = tmp_path / "voice.yaml"
    cfg_file.write_text("logging:\n  jsonl: false\n")

    _ensure_fork()
    from voicebridge.config import Config

    cfg = Config.load(cfg_file)
    from voicebridge import qubit_runtime

    qubit_runtime.use_qubit_home(cfg)
    from voicebridge import plugin_catalog as PC

    rows = {r["id"]: r for r in PC.local_catalog(cfg).get("entries", [])}
    assert "cron_providers/chronos" in rows
    assert rows["cron_providers/chronos"].get("loadable") is True
    # No discovery/load error is recorded for chronos.
    assert not rows["cron_providers/chronos"].get("error")
