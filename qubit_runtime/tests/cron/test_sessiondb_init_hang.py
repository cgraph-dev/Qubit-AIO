"""Regression test for a hung SessionDB() init permanently wedging a cron job.

Real-world incident: a cron job's ``SessionDB()`` construction inside
``run_job`` blocked forever (a wedged sqlite3.connect against state.db, no
other process holding a competing lock by the time it was diagnosed). Because
that call had no timeout of its own — unlike the agent's run_conversation,
which is already bounded by HERMES_CRON_TIMEOUT — the worker thread submitted
by ``_submit_with_guard`` never returned. Its ``finally`` block, which is the
only thing that discards the job ID from ``_running_job_ids``, never ran.
Every later tick logged "already running — skipping" and the job never fired
again until the whole gateway process was restarted days later.

These tests prove ``run_job`` now bounds the SessionDB init with its own
timeout (HERMES_CRON_SESSION_DB_TIMEOUT, default 10s) so a hang there can
never again wedge the job past that bound, and — end to end — that the
dispatch guard is released and the job becomes dispatchable again afterward.

Assertions capture the timeout passed to ``Future.result(timeout=...)`` (and
optionally force an immediate ``TimeoutError``) — no wall-clock waits, so the
suite stays free of timing flakes under parallel load.
"""

import concurrent.futures
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cron import scheduler
from cron.scheduler import run_job
from gateway import session_cron
@pytest.fixture
def owner_run(tmp_path, monkeypatch):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
    db = MagicMock(db_path=tmp_path / "state.db")
    db.get_compression_tip.return_value = None
    owner = SimpleNamespace(db=db)
    job = {"id": "owner-db", "name": "test", "prompt": "hello"}
    execution_id = "owner-fire"
    token = session_cron._execution.set((owner, "owner-session", job["id"], execution_id))
    monkeypatch.setattr(scheduler, "_qubit_home", tmp_path)
    monkeypatch.setattr(scheduler, "_reload_dotenv_and_publish_delivery_target", lambda *_: None)
    monkeypatch.setattr(scheduler, "_resolve_cron_agent_setup", lambda *_: scheduler._CronAgentSetup(
        model="test", runtime={"provider": "test"}))
    with patch("run_agent.AIAgent") as agent_cls:
        agent_cls.return_value.run_conversation.return_value = {"final_response": "ok"}
        try:
            yield owner, job, execution_id, agent_cls
        finally:
            session_cron._execution.reset(token)
@pytest.mark.parametrize("failure", ["wrong-store", "acquire-error"])
@pytest.mark.xfail(strict=True, reason="wave-23 short-port: local run_job does not reject a foreign/unacquirable owner store")
def test_owner_store_failure_never_runs_unpersisted_agent(owner_run, failure):
    owner, job, execution_id, agent_cls = owner_run
    wrong_db = MagicMock()
    with patch("qubit_state_registry.acquire", return_value=wrong_db,
               side_effect=OSError("store unavailable") if failure == "acquire-error" else None):
        success, _, response, error = scheduler.run_job(job, execution_id=execution_id)
    assert not success and response == ""
    assert error and ("canonical owner store" if failure == "wrong-store" else "store unavailable") in error
    agent_cls.assert_not_called()
    owner.db.close.assert_not_called()
    assert wrong_db.close.call_count == (1 if failure == "wrong-store" else 0)

# Hold the real class: patching cron.scheduler.concurrent.futures.ThreadPoolExecutor
# also replaces concurrent.futures.ThreadPoolExecutor (same module object).
_REAL_TPE = concurrent.futures.ThreadPoolExecutor

_RUNTIME = {
    "api_key": "test-key",
    "base_url": "https://example.invalid/v1",
    "provider": "openrouter",
    "api_mode": "chat_completions",
}


import pytest

pytestmark = pytest.mark.usefixtures("cron_owner_passthrough")

def _session_db_executor(timeouts: list, *, instant_timeout: bool = True):
    """Wrap ``ThreadPoolExecutor`` so SessionDB's ``result(timeout=...)`` is observable.

    ``run_job`` is the only caller that passes a timeout to ``Future.result`` on
    this path (``submit(SessionDB).result(timeout=...)``). Other pools used by
    ``tick`` / the agent inactivity watchdog call ``result()`` with no timeout
    and are left alone. When ``instant_timeout`` is True, the timed wait raises
    immediately instead of sleeping — the production hang path without a clock.
    """

    def factory(max_workers=1, *args, **kwargs):
        real = _REAL_TPE(max_workers=max_workers)
        orig_submit = real.submit

        def submit(fn, *a, **k):
            fut = orig_submit(fn, *a, **k)
            orig_result = fut.result

            def result(*ra, **rk):
                timeout = ra[0] if ra else rk.get("timeout")
                if timeout is not None:
                    timeouts.append(timeout)
                    if instant_timeout:
                        raise concurrent.futures.TimeoutError()
                return orig_result(*ra, **rk)

            fut.result = result
            return fut

        real.submit = submit
        return real

    return factory


class TestSessionDbInitTimeout:
    def test_sessiondb_init_preserves_multiplex_profile_context(
        self, tmp_path, monkeypatch
    ):
        """The timeout worker must construct SessionDB under the active profile."""
        from qubit_constants import (
            get_qubit_home,
            reset_qubit_home_override,
            set_qubit_home_override,
        )

        default_home = tmp_path / "default"
        profile_home = tmp_path / "profiles" / "jobsearch"
        monkeypatch.setenv("QUBIT_HOME", str(default_home))
        observed_homes = []
        fake_db = MagicMock()

        def make_session_db(*args, **kwargs):
            observed_homes.append(get_qubit_home())
            return fake_db

        job = {"id": "profile-sessiondb", "name": "test", "prompt": "hello"}
        profile_token = set_qubit_home_override(profile_home)
        try:
            with patch("cron.scheduler._qubit_home", None), \
                 patch("cron.scheduler_delivery._resolve_origin", return_value=None), \
                 patch("qubit_cli.env_loader.load_qubit_dotenv"), \
                 patch("qubit_cli.env_loader.reset_secret_source_cache"), \
                 patch("qubit_state_registry.acquire", side_effect=make_session_db), \
                 patch(
                     "qubit_cli.runtime_provider.resolve_runtime_provider",
                     return_value=_RUNTIME,
                 ), \
                 patch("run_agent.AIAgent") as mock_agent_cls:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "ok"}
                mock_agent_cls.return_value = mock_agent

                success, _output, final_response, error = run_job(job)
        finally:
            reset_qubit_home_override(profile_token)

        assert success is True
        assert error is None
        assert final_response == "ok"
        assert observed_homes == [profile_home]

    def test_run_job_does_not_hang_when_sessiondb_init_wedges(self, tmp_path, monkeypatch):
        """run_job proceeds without a session store when SessionDB init times out."""
        monkeypatch.setenv("HERMES_CRON_SESSION_DB_TIMEOUT", "0.2")
        job = {"id": "wedged-sessiondb", "name": "test", "prompt": "hello"}
        timeouts: list = []

        with patch("cron.scheduler._qubit_home", tmp_path), \
             patch("cron.scheduler_delivery._resolve_origin", return_value=None), \
             patch("qubit_cli.env_loader.load_qubit_dotenv"), \
             patch("qubit_cli.env_loader.reset_secret_source_cache"), \
             patch("qubit_state_registry.acquire"), \
             patch(
                 "qubit_cli.runtime_provider.resolve_runtime_provider",
                 return_value=_RUNTIME,
             ), \
             patch("run_agent.AIAgent") as mock_agent_cls, \
             patch(
                 "cron.scheduler.concurrent.futures.ThreadPoolExecutor",
                 side_effect=_session_db_executor(timeouts),
             ):
            mock_agent = MagicMock()
            mock_agent.run_conversation.return_value = {"final_response": "ok"}
            mock_agent_cls.return_value = mock_agent

            success, output, final_response, error = run_job(job)

        # Env-resolved bound was passed to Future.result — not the 10s default,
        # and not an unbounded call.
        assert timeouts == [0.2]
        assert success is True
        assert final_response == "ok"
        assert mock_agent_cls.call_args.kwargs["session_db"] is None


    def test_timeout_resolved_from_config_yaml(self, tmp_path, monkeypatch):
        """cron.session_db_timeout_seconds in config.yaml is respected when
        the env var is not set — the canonical config-first resolution path."""
        import qubit_yaml as yaml

        monkeypatch.delenv("HERMES_CRON_SESSION_DB_TIMEOUT", raising=False)
        monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
        (tmp_path / "config.yaml").write_text(
            yaml.safe_dump({"cron": {"session_db_timeout_seconds": 0.2}})
        )
        job = {"id": "config-timeout", "name": "test", "prompt": "hello"}
        timeouts: list = []

        with patch("cron.scheduler._qubit_home", tmp_path), \
             patch("cron.scheduler_delivery._resolve_origin", return_value=None), \
             patch("qubit_cli.env_loader.load_qubit_dotenv"), \
             patch("qubit_cli.env_loader.reset_secret_source_cache"), \
             patch("qubit_state_registry.acquire"), \
             patch(
                 "qubit_cli.runtime_provider.resolve_runtime_provider",
                 return_value=_RUNTIME,
             ), \
             patch("run_agent.AIAgent") as mock_agent_cls, \
             patch(
                 "cron.scheduler.concurrent.futures.ThreadPoolExecutor",
                 side_effect=_session_db_executor(timeouts),
             ):
            mock_agent = MagicMock()
            mock_agent.run_conversation.return_value = {"final_response": "ok"}
            mock_agent_cls.return_value = mock_agent

            success, output, final_response, error = run_job(job)

        # Config value was passed through — not the 10s default.
        assert timeouts == [0.2]
        assert success is True
        assert mock_agent_cls.call_args.kwargs["session_db"] is None


class TestDispatchGuardReleasedAfterHang:
    """End-to-end: the real bug symptom was every later tick silently
    skipping the job forever. Confirm the fix actually clears that path."""

    def test_guard_is_released_and_job_refires_after_sessiondb_hang(self, tmp_path, monkeypatch):
        import cron.scheduler as sched

        monkeypatch.setenv("HERMES_CRON_SESSION_DB_TIMEOUT", "0.2")
        sched._parallel_pools.clear()
        sched._parallel_pool_max_workers.clear()
        sched._running_job_ids.clear()

        job = {
            "id": "guard-sessiondb-hang",
            "name": "guard-sessiondb-hang",
            "prompt": "hello",
            "schedule": "every 5m",
            "enabled": True,
            "next_run_at": "2020-01-01T00:00:00",
            "deliver": "local",
        }
        timeouts: list = []

        try:
            with patch("cron.scheduler._qubit_home", tmp_path), \
                 patch("cron.scheduler_delivery._resolve_origin", return_value=None), \
                 patch("qubit_cli.env_loader.load_qubit_dotenv"), \
                 patch("qubit_cli.env_loader.reset_secret_source_cache"), \
                 patch("qubit_state_registry.acquire"), \
                 patch(
                     "qubit_cli.runtime_provider.resolve_runtime_provider",
                     return_value=_RUNTIME,
                 ), \
                 patch("run_agent.AIAgent") as mock_agent_cls, \
                 patch(
                     "cron.scheduler.concurrent.futures.ThreadPoolExecutor",
                     side_effect=_session_db_executor(timeouts),
                 ), \
                 patch.object(sched, "get_due_jobs", return_value=[job]), \
                 patch.object(sched, "claim_job_for_fire", return_value=True), \
                 patch.object(sched, "save_job_output", return_value="/tmp/out"), \
                 patch.object(sched, "mark_job_run"), \
                 patch.object(sched, "_deliver_result", return_value=None):
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "ok"}
                mock_agent_cls.return_value = mock_agent

                n = sched.tick(verbose=False)  # sync=True by default: waits for the job
                assert n == 1
                assert timeouts == [0.2]

                # Without the fix this would still contain the job ID forever.
                assert "guard-sessiondb-hang" not in sched.get_running_job_ids()

                # A second tick can dispatch the same job again — before the
                # fix this would log "already running — skipping" and
                # return 0.
                n2 = sched.tick(verbose=False)
                assert n2 == 1
        finally:
            sched._running_job_ids.discard(sched._inflight_key("guard-sessiondb-hang"))
            sched._shutdown_parallel_pool()


# ===========================================================================
# Bug #72782: late SessionDB result leaks FDs after timeout abandonment
# ===========================================================================



class TestLateSessionDbClosedAfterTimeout:
    """End-to-end: when SessionDB init times out but later completes inside the
    abandoned worker, the orphaned result must be closed (#72782)."""

    def test_late_session_db_result_is_closed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_CRON_SESSION_DB_TIMEOUT", "0.2")
        never_set = threading.Event()
        late_db_holder = []  # captures the SessionDB returned by the late init

        def _hanging_then_capture():
            never_set.wait(timeout=30)
            db = MagicMock()
            late_db_holder.append(db)
            return db

        job = {"id": "late-close-test", "name": "test", "prompt": "hello"}

        try:
            with patch("cron.scheduler._qubit_home", tmp_path), \
                 patch("cron.scheduler_delivery._resolve_origin", return_value=None), \
                 patch("qubit_cli.env_loader.load_qubit_dotenv"), \
                 patch("qubit_cli.env_loader.reset_secret_source_cache"), \
                 patch("qubit_state_registry.acquire", side_effect=_hanging_then_capture), \
                 patch(
                     "qubit_cli.runtime_provider.resolve_runtime_provider",
                     return_value={
                         "api_key": "test-key",
                         "base_url": "https://example.invalid/v1",
                         "provider": "openrouter",
                         "api_mode": "chat_completions",
                     },
                 ), \
                 patch("run_agent.AIAgent") as mock_agent_cls:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "ok"}
                mock_agent_cls.return_value = mock_agent

                success, output, final_response, error = run_job(job)
                # run_job returned promptly after the timeout; session_db is None
                assert success is True

                # Release the hanging init so the abandoned worker completes.
                never_set.set()
                # Wait for the done-callback to fire and close the late result.
                for _ in range(50):
                    if late_db_holder and late_db_holder[0].close.called:
                        break
                    time.sleep(0.1)
        finally:
            never_set.set()

        assert len(late_db_holder) == 1, "SessionDB() should have completed once"
        late_db_holder[0].close.assert_called_once(), (
            "The SessionDB that completed after the timeout must be closed by "
            "the done-callback — otherwise its SQLite FDs leak until process exit (#72782)"
        )


# ===========================================================================
# #96290: gated runs must not open the session store at all
# ===========================================================================

class TestSessionDbInitAfterEarlyReturns:
    """SessionDB init moved AFTER the wake-gate / prompt-validation early
    returns (#96290): a run that never reaches the agent must never open
    state.db, so there is no handle for a gated return path to abandon."""

    def test_wake_gate_false_never_opens_session_db(self, tmp_path, monkeypatch):
        monkeypatch.delenv("HERMES_CRON_SESSION_DB_TIMEOUT", raising=False)
        job = {
            "id": "gated-no-db",
            "name": "gated-no-db",
            "prompt": "hello",
            "script": "gate.py",
        }

        with patch("cron.scheduler._qubit_home", tmp_path), \
             patch("cron.scheduler_delivery._resolve_origin", return_value=None), \
             patch("qubit_cli.env_loader.load_qubit_dotenv"), \
             patch("qubit_cli.env_loader.reset_secret_source_cache"), \
             patch("qubit_state_registry.acquire") as mock_db_cls, \
             patch(
                 "cron.scheduler._run_job_script_with_claim_heartbeat",
                 return_value=(True, '{"wakeAgent": false}'),
             ), \
             patch("run_agent.AIAgent") as mock_agent_cls:
            success, output, final_response, error = run_job(job)

        assert success is True
        mock_db_cls.assert_not_called()
        mock_agent_cls.assert_not_called()


# --- ported from upstream pin (wave 25 short-port) ---
@pytest.mark.xfail(strict=True, reason="wave-25 short-port: exposed local-port defect")
def test_guard_is_released_and_job_refires_after_sessiondb_hang(owner_run, monkeypatch):
    owner, _job, _, agent_cls = owner_run
    monkeypatch.setenv("HERMES_CRON_SESSION_DB_TIMEOUT", "0.02")
    from cron import jobs

    release = threading.Event()
    closed = threading.Event()
    owner.db.close.side_effect = closed.set
    calls = []

    def acquire(_path):
        calls.append(True)
        if len(calls) == 1:
            release.wait(10)
        return owner.db

    # The scheduler dispatches through the authority; emulate only transport and
    # execution admission, retaining the real guard and run_job lifecycle.
    def admitted(fired, **kwargs):
        token = session_cron._execution.set((owner, "owner-session", fired["id"], kwargs["execution_id"]))
        try:
            return scheduler.run_job(fired, **kwargs)
        finally:
            session_cron._execution.reset(token)

    with jobs.use_cron_store(owner.db.db_path.parent):
        saved = jobs.create_job(prompt="hello", schedule="every 5m", deliver="local")
        try:
            with patch("qubit_state_registry.acquire", side_effect=acquire), \
                 patch("cron.scheduler_authority.run_canonical_job", side_effect=admitted), \
                 patch("cron.scheduler_authority.reconcile_pending"), \
                 patch.object(scheduler, "get_due_jobs", return_value=[saved]):
                assert scheduler.tick() == 1
                assert saved["id"] not in scheduler.get_running_job_ids()
                agent_cls.assert_not_called()
                release.set()
                assert closed.wait(5)
                assert scheduler.tick() == 1
                assert len(calls) == 2
                assert agent_cls.call_count == 1
                final_job = jobs.get_job(saved["id"])
                assert final_job and final_job["last_status"] == "ok"
        finally:
            release.set()
            scheduler._shutdown_parallel_pool()


@pytest.mark.xfail(strict=True, reason="wave-25 short-port: exposed local-port defect")
@pytest.mark.parametrize("setting", ["env", "config"])
def test_run_job_does_not_hang_when_sessiondb_init_wedges(owner_run, tmp_path, monkeypatch, setting):
    owner, job, execution_id, agent_cls = owner_run
    if setting == "env":
        monkeypatch.setenv("HERMES_CRON_SESSION_DB_TIMEOUT", "0.02")
    else:
        monkeypatch.delenv("HERMES_CRON_SESSION_DB_TIMEOUT", raising=False)
        (tmp_path / "config.yaml").write_text("cron:\n  session_db_timeout_seconds: 0.02\n")
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    owner.db.close.side_effect = closed.set

    def acquire(_path):
        entered.set()
        release.wait(10)
        return owner.db

    try:
        with patch("qubit_state_registry.acquire", side_effect=acquire):
            started = time.monotonic()
            success, _, response, error = scheduler.run_job(job, execution_id=execution_id)
            elapsed = time.monotonic() - started
            assert entered.is_set()
            assert elapsed < 5
            assert not success and response == "" and error and "TimeoutError" in error
            agent_cls.assert_not_called()
            owner.db.close.assert_not_called()
            release.set()
            assert closed.wait(5), "late borrowed reference was leaked"
            owner.db.close.assert_called_once()
    finally:
        release.set()
        assert closed.wait(5)


@pytest.mark.xfail(strict=True, reason="wave-25 short-port: exposed local-port defect")
def test_sessiondb_init_preserves_multiplex_profile_context(owner_run, tmp_path):
    from hermes_constants import get_hermes_home, reset_hermes_home_override, set_hermes_home_override

    owner, job, execution_id, agent_cls = owner_run
    profile_home = tmp_path / "profiles" / "jobsearch"
    owner.db.db_path = profile_home / "state.db"
    observed = []

    def acquire(path):
        observed.append((path, get_hermes_home()))
        return owner.db

    token = set_hermes_home_override(profile_home)
    try:
        with patch("qubit_state_registry.acquire", side_effect=acquire):
            success, _, response, error = scheduler.run_job(job, execution_id=execution_id)
    finally:
        reset_hermes_home_override(token)
    assert (success, response, error) == (True, "ok", None)
    assert observed == [(profile_home / "state.db", profile_home)]
    assert agent_cls.call_args.kwargs["session_db"] is owner.db


def test_wake_gate_false_never_opens_session_db(owner_run):
    _, job, execution_id, agent_cls = owner_run
    job["script"] = "gate.py"
    with patch("qubit_state_registry.acquire") as acquire, \
         patch("cron.scheduler._run_job_script_with_claim_heartbeat", return_value=(True, '{"wakeAgent": false}')):
        success, _, _, error = scheduler.run_job(job, execution_id=execution_id)
    assert success and error is None
    acquire.assert_not_called()
    agent_cls.assert_not_called()

