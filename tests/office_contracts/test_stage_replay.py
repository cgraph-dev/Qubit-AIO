"""P0-06 contract tests for ``scripts/stage_replay.py``.

Every run is a fresh subprocess so process-global patches (the network guard,
``HOME``/``QUBIT_HOME``) behave exactly as they do for an operator. No test
touches the operator's live home: the harness is pointed at a throwaway home
under ``tmp_path`` and the sentinel home must stay empty.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "stage_replay.py"


def _env(home: Path, tmp: Path, *, qubit_home: str | None = None) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"QUBIT_HOME", "HERMES_HOME", "PYTHONPATH"}
    }
    env.update(
        {
            "HOME": str(home),
            "TMPDIR": str(tmp),
            "PYTHONPATH": str(REPO_ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    if qubit_home is not None:
        env["QUBIT_HOME"] = qubit_home
    return env


def _run(
    work_root: Path,
    *,
    home: Path,
    tmp: Path,
    extra_args: list[str] | None = None,
    qubit_home: str | None = None,
) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        str(SCRIPT),
        "--fixture",
        "synthetic-department",
        "--no-network",
        "--json",
        "--work-root",
        str(work_root),
        *(extra_args or []),
    ]
    return subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        env=_env(home, tmp, qubit_home=qubit_home),
        text=True,
        capture_output=True,
        timeout=120,
    )


def _report(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_stage_replay_passes_replay_and_network_guards(tmp_path: Path) -> None:
    home = tmp_path / "operator-home"
    home.mkdir()
    work = tmp_path / "work"
    result = _run(work, home=home, tmp=tmp_path / "tmp")
    report = _report(result)

    assert report["status"] == "PASS"
    assert report["task_id"] == "P0-06"
    assert report["fixture"] == "synthetic-department"
    assert report["equal"] is True
    assert report["events"]["recorded_digest"] == report["replay_digest"]
    assert report["unknown_reconciled"] == 1
    assert report["network_blocked"] is True
    assert report["network_probe"]["blocked"] is True
    assert report["network_probe"]["error"] == "QOFFICE_STAGE_NETWORK_BLOCKED"
    assert report["consistency"]["event_status_matches_store"] is True

    counts = report["counts"]
    assert counts["tenants"] == 1
    assert counts["businesses"] == 1
    assert counts["agents"] == 2
    assert counts["jobs"] == 2
    assert counts["approvals"] == 1
    assert counts["tool_invocations"] == 1
    assert counts["ledger_entries"] == 1
    assert counts["artifacts"] == 1

    # The throwaway home is the only home the harness touched.
    assert report["isolation"]["HOME"] == str(work.resolve() / "home")
    assert report["isolation"]["QUBIT_HOME"] == str(work.resolve() / "qubit-home")
    assert Path(report["db_path"]).is_relative_to(work.resolve())
    assert report["events"]["event_history_path"].startswith(str(work.resolve()))
    assert list(home.rglob("*")) == [], "the harness wrote into the operator home"


def test_stage_replay_is_deterministic_across_runs(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    report_a = _report(
        _run(tmp_path / "a", home=home, tmp=tmp_path / "tmp")
    )
    report_b = _report(
        _run(tmp_path / "b", home=home, tmp=tmp_path / "tmp")
    )

    assert report_a["digest"] == report_b["digest"]
    assert report_a["replay_digest"] == report_b["replay_digest"]
    assert report_a["events"]["event_history_sha256"] == report_b["events"][
        "event_history_sha256"
    ]


def test_stage_replay_refuses_a_live_looking_qubit_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    live = tmp_path / "live-home"
    live.mkdir()
    state_db = live / "state.db"
    state_db.write_bytes(b"not really sqlite")
    os.utime(state_db, (1_600_000_000, 1_600_000_000))

    result = _run(
        tmp_path / "work",
        home=home,
        tmp=tmp_path / "tmp",
        qubit_home=str(live),
    )

    assert result.returncode == 2
    assert "QOFFICE_STAGE_LIVE_HOME_REFUSED" in result.stderr
    assert not (tmp_path / "work").exists()


def test_stage_replay_cleans_up_unless_kept(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    tmp = tmp_path / "tmp"

    cleaned_root = tmp_path / "cleaned"
    cleaned = _report(_run(cleaned_root, home=home, tmp=tmp))
    assert cleaned["cleanup"]["cleaned"] is True
    assert not cleaned_root.exists()

    kept_root = tmp_path / "kept"
    kept = _report(_run(kept_root, home=home, tmp=tmp, extra_args=["--keep"]))
    assert kept["cleanup"]["kept"] is True
    assert kept_root.exists()
    assert (kept_root / "stage-report.json").is_file()
    assert (kept_root / "event-history.json").is_file()


def test_stage_replay_rejects_a_non_empty_work_root(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "occupied"
    work.mkdir()
    (work / "operator-file.txt").write_text("do not delete", encoding="utf-8")

    result = _run(work, home=home, tmp=tmp_path / "tmp")

    assert result.returncode == 2
    assert "QOFFICE_STAGE_WORK_ROOT_NOT_EMPTY" in result.stderr
    assert (work / "operator-file.txt").is_file()


@pytest.mark.parametrize("bad_fixture", ["unknown-fixture"])
def test_stage_replay_rejects_an_unknown_fixture(
    tmp_path: Path, bad_fixture: str
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--fixture",
            bad_fixture,
            "--no-network",
            "--json",
        ],
        cwd=str(REPO_ROOT),
        env=_env(home, tmp_path / "tmp"),
        text=True,
        capture_output=True,
        timeout=60,
    )

    assert result.returncode == 2
    assert "invalid choice" in result.stderr
