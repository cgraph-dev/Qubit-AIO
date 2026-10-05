"""``storage`` health must probe the real state root, not the profile root.

Regression: ``_status_components`` checked ``<home>/state.db`` while the runtime
nests its state at ``<home>/runtime/state.db``. Every install therefore reported
``storage: degraded`` -> ``overall: degraded`` permanently, even though the DB
was present and healthy. The gate has to stay honest in BOTH directions: no
false ``degraded`` on a healthy install, and no false ``ok`` when the DB is
genuinely missing.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
_QUBIT_RUNTIME = REPO_ROOT / "qubit_runtime"
if _QUBIT_RUNTIME.is_dir() and str(_QUBIT_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_QUBIT_RUNTIME))

from voicebridge import qubit_compat  # noqa: E402


def _make_home(tmp_path: Path, *, nested: bool, flat: bool) -> Path:
    if nested:
        (tmp_path / "runtime").mkdir(parents=True, exist_ok=True)
        (tmp_path / "runtime" / "state.db").touch()
    if flat:
        (tmp_path / "state.db").touch()
    return tmp_path


def test_nested_runtime_state_db_is_found(tmp_path):
    """The live layout: <home>/runtime/state.db must resolve."""
    home = _make_home(tmp_path, nested=True, flat=False)
    assert qubit_compat._resolve_state_db_path(home) == home / "runtime" / "state.db"


def test_flat_state_db_still_found(tmp_path):
    """Older/flat layouts keep working."""
    home = _make_home(tmp_path, nested=False, flat=True)
    assert qubit_compat._resolve_state_db_path(home) == home / "state.db"


def test_nested_wins_over_flat(tmp_path):
    home = _make_home(tmp_path, nested=True, flat=True)
    assert qubit_compat._resolve_state_db_path(home) == home / "runtime" / "state.db"


@pytest.mark.parametrize("home", [None, "empty"])
def test_missing_db_reports_none(home, tmp_path):
    """A genuinely absent DB must NOT be papered over."""
    if home == "empty":
        home = tmp_path
    assert qubit_compat._resolve_state_db_path(home) is None


def test_components_storage_ok_on_healthy_install(tmp_path):
    """End-to-end through the rollup the dashboard actually renders."""
    home = _make_home(tmp_path, nested=True, flat=False)
    components = qubit_compat._status_components(
        {"running": True, "state": "running"}, {}, home
    )
    assert components["storage"]["status"] == "ok"
    # ...and the rest of the rollup must not drag ``overall`` down either.
    assert all(c["status"] == "ok" for c in components.values())


def test_components_storage_degraded_when_db_absent(tmp_path):
    components = qubit_compat._status_components(
        {"running": True, "state": "running"}, {}, tmp_path
    )
    assert components["storage"]["status"] == "degraded"
