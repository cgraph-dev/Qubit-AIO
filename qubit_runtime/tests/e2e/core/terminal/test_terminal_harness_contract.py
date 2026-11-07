"""Contract tests for the terminal-lane E2E harness (no backend, no PTY process).

These pin the three harness seams that made the terminal suites fail for reasons unrelated to the
product: the readiness sentinel a Qubit ``serve`` actually prints, the Ink TUI's intentionally
continuous startup hero (no frame is ever still), and the throwaway-home gateway that must idle-exit
instead of outliving the client.
"""

from __future__ import annotations

import shutil
import threading
import time

import pytest

from tests.e2e.core.terminal import _gateway_client, _pty


def test_backend_ready_sentinel_accepts_canonical_and_legacy() -> None:
    """``qubit serve`` prints QUBIT_BACKEND_READY; the legacy spelling stays readable."""
    assert _gateway_client.READY_RE.search("QUBIT_BACKEND_READY port=4321").group(1) == "4321"
    assert _gateway_client.READY_RE.search("HERMES_BACKEND_READY port=99").group(1) == "99"
    assert _gateway_client.READY_RE.search("Qubit backend listening on 127.0.0.1:1") is None


def test_tui_wait_quiet_is_a_bounded_pause_not_a_still_frame() -> None:
    """The animated ghost never settles, so the TUI path waits for status ``ready`` + a pause."""
    term = object.__new__(_pty.PtyHermes)
    term.tui = True
    term.raw = bytearray()
    term._lock = threading.Lock()
    term._dead = lambda: False
    term.dump = lambda: "<screen>"

    term._tui_ready = lambda: True
    started = time.monotonic()
    term.wait_quiet(1.0, timeout=5)
    assert 0.2 <= time.monotonic() - started < 2.0

    term._tui_ready = lambda: False
    with pytest.raises(AssertionError, match="settle on ready"):
        term.wait_quiet(0.2, timeout=0.2)


def test_throwaway_home_lets_the_auto_gateway_idle_exit(tmp_path) -> None:
    """The sandbox gateway is detached; the E2E home lowers its idle-exit so it cannot linger."""
    import yaml

    home = tmp_path / "home"
    _pty.write_qubit_home(home, "http://127.0.0.1:9/v1", extra_config=_pty._BASE_EXTRA_CONFIG)
    config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert config["gateway"]["unmanaged_idle_exit_seconds"] == 2


def test_private_tui_dir_is_a_prebuilt_bundle(tmp_path) -> None:
    """TUI launches use a private copy so a concurrent checkout rebuild cannot corrupt it."""
    if shutil.which("node") is None or not (_pty.REPO_ROOT / "ui-tui" / "dist" / "entry.js").is_file():
        pytest.skip("node or the prebuilt Ink bundle is unavailable")
    tui = _pty._private_tui_dir(tmp_path)
    entry = tui / "dist" / "entry.js"
    assert entry.is_file() and entry.stat().st_size > 0
