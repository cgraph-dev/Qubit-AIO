"""The office autowork driver must be reachable from the scheduler, or the
office never animates and looks broken.

THE GAP THIS PINS
-----------------
`voicebridge.office.autowork` is a complete driver: it picks eligible seats,
runs one real attributed turn per seat, settles the ledger and writes
`office_event` rows. Nothing ever called it. `office autowork --now` fires only
when a human types it, so every agent showed "Waiting for instructions" forever
and the office read as dead -- while the engine was simply never started.

The binding has two parts that can each fail silently, so both are asserted:

1. A scheduler JOB exists, enabled, pointed at the script. A driver with no
   trigger is the whole bug.
2. The script is DEPLOYED where the scheduler resolves scripts. Cron confines
   `script` to `<QUBIT_HOME>/scripts/` and raises on anything outside it, so a
   job naming a checkout path is a job that fires and fails forever. This was
   hit for real: the first version of the job pointed at
   `scripts/office_autowork_tick.py` in the repo, which the scheduler cannot
   reach.

Deliberately NOT asserted: the cadence value or the script's byte content.
Those are change-detectors; the contract is "the trigger exists and is
resolvable", not "the interval is 900".
"""
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT_NAME = "office_autowork_tick.py"
JOB_NAME = "office autowork tick"


def _qubit_home() -> Path:
    """The home the cron store resolves for this checkout.

    The running gateway carries `QUBIT_HOME` in its own environment; that is
    the authoritative home. When the variable is absent (a bare pytest process)
    the vendored store falls back to `~/.qubit`, which is NOT where this
    deployment puts its scripts -- so a test that reads the resolver without
    binding the home first would pass against a directory nothing ever runs
    from, and fail against the real one. Reproduce the ticker's environment
    instead: ask the same code path for the home it will actually use.
    """
    from voicebridge import config as C
    from voicebridge import qubit_runtime as QR
    cfg = C.Config.load()
    QR.ensure_path()
    QR.use_qubit_home(cfg)
    import os
    return Path(os.environ.get("QUBIT_HOME") or (Path.home() / ".qubit"))


def _jobs() -> list:
    home = _qubit_home()
    path = home / "cron" / "jobs.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return data.get("jobs") or []


def test_the_repo_ships_the_driver_script():
    """The canonical source exists in the checkout.

    The deployed copy is what cron runs; this is what a reader maintains. Both
    must exist or the deployed one becomes orphaned folklore.
    """
    src = REPO / "scripts" / SCRIPT_NAME
    assert src.exists(), f"missing canonical source {src}"
    text = src.read_text()
    # It must drive the real driver, not reimplement one.
    assert "autowork" in text and "AWA.tick" in text


def test_the_deployed_script_is_where_cron_can_resolve_it():
    """A DEPLOYED copy exists, and conforms to the resolver's confinement rule.

    Skipped when the machine's deployment is not visible to this process: the
    suite must not fail because a developer's checkout is not a live install.
    When a home IS visible, its scripts dir is the only place cron will look,
    so the deployed copy must be there and nowhere else.
    """
    home_scripts = _qubit_home() / "scripts"
    if not home_scripts.exists():
        pytest.skip(f"no deployed scripts dir at {home_scripts}")
    deployed = home_scripts / SCRIPT_NAME
    assert deployed.exists(), (
        f"cron cannot run {SCRIPT_NAME}: it resolves scripts only under "
        f"{home_scripts}, and the job names this file. A job whose script is "
        "not deployed fires and fails on every run.")
    # It must be the deployed variant (self-contained, locates the repo), not
    # a copy of the in-repo one that only works with the checkout as cwd.
    text = deployed.read_text()
    assert "REPO_CANDIDATES" in text, (
        "the deployed copy must locate the checkout itself; cron does not run "
        "it with the repo as cwd")


def test_the_scheduler_resolves_a_name_from_its_own_scripts_dir(tmp_path,
                                                                monkeypatch):
    """The resolver accepts a bare name that exists in the home's scripts dir.

    This is the contract the deployment must satisfy, asserted hermetically:
    ask the real resolver about a home we control, and confirm a bare name
    resolves INSIDE that home. It proves the convention (deploy next to the
    other job scripts, refer to it by name) without depending on the machine's
    live deployment -- which is what the live check below is for.
    """
    import os
    import sys
    home = tmp_path / "home"
    (home / "scripts").mkdir(parents=True)
    (home / "scripts" / SCRIPT_NAME).write_text("# deployed stub\n")
    monkeypatch.setenv("QUBIT_HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(home))
    sys.path.insert(0, str(REPO / "qubit_runtime"))
    from cron import scheduler_script as SS

    path, err = SS._resolve_script_path(SCRIPT_NAME)
    assert err is None, f"resolver refused a deployed script: {err}"
    assert path is not None
    assert str(path) == str((home / "scripts" / SCRIPT_NAME).resolve())


def test_the_resolver_confines_scripts_to_the_home(tmp_path, monkeypatch):
    """A script outside the home's scripts dir must be REFUSED.

    This is the failure the first version of the real job had: it named a
    checkout path, which the resolver cannot reach, so the job would have
    fired and failed on every run. Pinning the refusal keeps that shape from
    looking acceptable.
    """
    import sys
    home = tmp_path / "home"
    (home / "scripts").mkdir(parents=True)
    outside = tmp_path / "elsewhere.py"
    outside.write_text("# not in the scripts dir\n")
    monkeypatch.setenv("QUBIT_HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(home))
    sys.path.insert(0, str(REPO / "qubit_runtime"))
    from cron import scheduler_script as SS

    path, err = SS._resolve_script_path(str(outside))
    assert err is not None or path is None, (
        "the resolver accepted a script outside the home's scripts dir")


def test_a_scheduled_office_job_exists_and_is_enabled():
    """A trigger must exist, or the driver is never called.

    Without this the office silently never animates -- the original symptom.
    """
    jobs = _jobs()
    if not jobs:
        pytest.skip("no cron store in this home (fresh install)")
    matches = [j for j in jobs if j.get("name") == JOB_NAME]
    assert matches, (
        f"no scheduled job named {JOB_NAME!r}: the office autowork driver has "
        "no trigger, so agents never work on their own")
    job = matches[0]
    assert job.get("enabled") is True
    assert job.get("script") == SCRIPT_NAME
    # no_agent: a task-text job would spend a model call per tick just to
    # decide to run work the script does directly.
    assert job.get("no_agent") is True


def test_the_script_respects_the_owner_switch():
    """The script must not turn the driver on, and must not force a tick.

    An autonomous driver that enables itself is a different product than the
    one that was built; and a scheduler that forces a tick every run would
    spend the owner's money on every invocation instead of on the cadence.
    These are asserted as source contracts because they are the difference
    between "scheduled" and "unattended spending".
    """
    for src in (REPO / "scripts" / SCRIPT_NAME,
                _qubit_home() / "scripts" / SCRIPT_NAME):
        if not src.exists():
            continue
        text = src.read_text()
        # The scheduled path calls plain tick(), which enforces the flag and
        # the cadence internally. run_now would bypass both.
        assert "AWA.tick(" in text, f"{src.name} never calls the gated tick()"
        assert "enable(" not in text, (
            f"{src.name} tries to flip the owner switch")
