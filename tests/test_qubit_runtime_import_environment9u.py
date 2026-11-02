"""The voice service's interpreter keeps its own packages across the fork pin.

Regression for the live defect where ``pin_qubit_home()`` imported the vendored
tree's ``qubit_bootstrap`` (via the foreign-import aliaser preload) and its
import-time ``pm.environments.activate_dependencies()`` replaced the service
venv's site-packages with the managed generation. ``numpy``, ``faster_whisper``
and ``sherpa_onnx`` then stopped resolving mid-process, breaking accurate ASR
and TTS in a long-lived bridge.

The boundary import must not evict packages the process booted with, and the
vendored runtime must still be importable from the repo afterwards.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

AUDIO_MODULES = ("numpy", "faster_whisper", "sherpa_onnx")


def _present():
    return {name: importlib.util.find_spec(name) is not None
            for name in AUDIO_MODULES}


class _FakeCfg:
    """Minimal config for ``plugin_runtime.list_plugins`` (no live state)."""

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir

    def state_dir(self) -> Path:
        return self._state_dir

    def get(self, *path, default=None):
        return default


@pytest.fixture()
def safe_home(tmp_path, monkeypatch):
    """Pin a throwaway Qubit home so the test never touches the owner state."""
    home = tmp_path / "runtime"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return home


def test_pin_qubit_home_keeps_service_packages(safe_home):
    from voicebridge import qubit_runtime

    before = _present()
    assert all(before.values()), (
        "the test interpreter is missing the audio packages this regression "
        f"guards: {before}")

    resolved = qubit_runtime.pin_qubit_home()

    assert resolved == safe_home
    after = _present()
    assert after == before, f"pin_qubit_home evicted service packages: {after}"


def test_list_plugins_does_not_evict_service_packages(safe_home, tmp_path,
                                                      monkeypatch):
    from voicebridge import plugin_runtime, qubit_runtime

    monkeypatch.setattr(plugin_runtime, "estop_engaged", lambda cfg: False)
    monkeypatch.setattr(plugin_runtime, "_bundled_rows", lambda: [])
    monkeypatch.setattr(plugin_runtime, "plugin_catalog",
                        lambda cfg: {"plugins": []})

    qubit_runtime.pin_qubit_home()
    before = _present()
    assert all(before.values())

    result = plugin_runtime.list_plugins(_FakeCfg(tmp_path / "plugin-state"))

    assert result.get("state") == "OK", result
    assert _present() == before, (
        "plugin_runtime.list_plugins evicted service packages")


def test_vendored_runtime_stays_importable_from_the_repo(safe_home):
    from voicebridge import qubit_runtime

    saved_path = list(sys.path)
    try:
        root = qubit_runtime.ensure_path()
        assert str(root) in sys.path
        assert importlib.util.find_spec("qubit_cli") is not None
        assert all(_present().values())
    finally:
        sys.path[:] = saved_path
