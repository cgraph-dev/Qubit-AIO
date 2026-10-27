import argparse
import io
import json
import sqlite3
from contextlib import redirect_stdout
from datetime import datetime, timezone

from agent.runtime_port import RuntimeCapabilities, TurnEvent, UnknownRuntimeError
from recipes import monitor_operative
from recipes.cron import build_cron_job_spec, cron_create_command
from recipes.monitor_operative import (
    MONITOR_OPERATIVE,
    CompressionSnapshot,
    KanbanSnapshot,
    collect_compression,
    collect_cron,
    collect_kanban,
    collect_memory,
    collect_sections,
)
from recipes.registry import build_recipe_registry
from recipes.runner import run_recipe
from recipes.sections import available, unavailable

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


class _FakeBackend:
    name = "fake"
    capabilities = RuntimeCapabilities(True, True, False, False)

    def __init__(self, events=()):
        self.events = events
        self.requests = []

    def run_turn(self, request, cancel=None):
        self.requests.append(request)
        yield from self.events


class _Registry:
    def __init__(self, backends):
        self._backends = backends

    def get(self, name):
        if name not in self._backends:
            raise UnknownRuntimeError(name, sorted(self._backends))
        return self._backends[name]


def _by_id(results):
    return {result.id: result for result in results}


def test_spec_declares_the_read_only_operative_surface():
    spec = MONITOR_OPERATIVE
    assert spec.id == "monitor_operative"
    assert spec.sections == ("cron", "kanban", "memory", "compression")
    assert spec.default_schedule == "0 */4 * * *"
    assert spec.approval_policy == "read_only_collectors"
    assert spec.output_format == "short_spoken_brief"
    assert set(spec.collectors) == set(spec.sections)
    assert "what's my operative status" in spec.voice_aliases
    assert "status of my work" in spec.voice_aliases


def test_registry_includes_monitor_operative():
    registry = build_recipe_registry()
    assert registry.get("monitor_operative") is MONITOR_OPERATIVE
    assert "morning_digest" in registry.available()


def test_cron_reports_counts_failures_overdue_next_runs_and_heartbeat():
    jobs = [
        {"id": "a", "name": "daily-report", "enabled": True, "state": "scheduled",
         "next_run_at": "2026-10-27T13:00:00+00:00"},
        {"id": "b", "name": "paused-job", "enabled": False, "state": "paused"},
        {"id": "c", "name": "broken-job", "enabled": True, "state": "scheduled",
         "last_status": "error", "failure_streak": 2,
         "next_run_at": "2026-10-27T10:00:00+00:00"},
    ]
    result = collect_cron(NOW, jobs=jobs, heartbeat=12.0, last_error=None)
    text = "\n".join(result.facts)
    assert result.available
    assert "3 job(s): 2 scheduled, 1 paused, 0 completed." in text
    assert "Failed: 1 — broken-job." in text
    assert "Overdue: 1 — broken-job" in text
    assert "Next runs: daily-report in 1h." in text
    assert "Scheduler heartbeat: fresh (12s ago)." in text


def test_disabled_job_without_a_pause_marker_counts_as_paused():
    jobs = [{"id": "d", "name": "disabled", "enabled": False, "state": "scheduled"}]
    result = collect_cron(NOW, jobs=jobs, heartbeat=1.0, last_error=None)
    assert "1 job(s): 0 scheduled, 1 paused, 0 completed." in result.facts


def test_empty_cron_store_is_available_and_stale_heartbeat_is_named():
    result = collect_cron(NOW, jobs=[], heartbeat=10_000, last_error="boom")
    text = "\n".join(result.facts)
    assert result.available
    assert "No cron jobs are configured." in text
    assert "Scheduler heartbeat: stale" in text
    assert "Last tick error: boom" in text


def test_cron_failure_is_a_typed_tool_error(monkeypatch):
    def boom():
        raise RuntimeError("cron read exploded with sk-abcdefghijklmnop")

    monkeypatch.setattr(monitor_operative, "_default_cron_jobs", boom)
    result = collect_sections({"cron": collect_cron}, sections=("cron",))[0]
    assert result.status == "unavailable"
    assert result.reason == "tool_error"
    assert "sk-abcdefghijklmnop" not in result.detail


def test_kanban_reports_counts_blocked_stale_and_the_overdue_gap():
    snapshot = KanbanSnapshot(
        board="default",
        by_status={"ready": 3, "blocked": 2, "running": 1},
        oldest_ready_age_seconds=25 * 60 * 60,
        failing_tasks=1,
    )
    result = collect_kanban(NOW, snapshot=snapshot)
    text = "\n".join(result.facts)
    assert result.available
    assert "Board: default." in text
    assert "6 open task(s):" in text
    assert "blocked 2" in text
    assert "Blocked: 2." in text
    assert "Stale: the oldest ready task is 1d 1h old" in text
    assert "Tasks with repeated failures: 1." in text
    assert "Overdue: not supported (board tasks have no due-date field)." in text


def test_empty_kanban_board_is_available():
    result = collect_kanban(NOW, snapshot=KanbanSnapshot(board="default"))
    text = "\n".join(result.facts)
    assert result.available
    assert "No open tasks on this board." in text
    assert "Ready queue: no ready tasks." in text


def test_missing_kanban_database_is_an_honest_gap(tmp_path):
    missing = tmp_path / "kanban.db"
    result = collect_sections(
        {"kanban": lambda now: collect_kanban(now, db_path=missing)},
        sections=("kanban",))[0]
    assert result.status == "unavailable"
    assert result.reason == "connector_not_configured"


def test_unreadable_kanban_database_is_a_tool_error(tmp_path):
    bad = tmp_path / "kanban.db"
    bad.write_bytes(b"not a sqlite database")
    result = collect_sections(
        {"kanban": lambda now: collect_kanban(now, db_path=bad)},
        sections=("kanban",))[0]
    assert result.status == "unavailable"
    assert result.reason == "tool_error"


def test_memory_without_a_provider_is_not_configured():
    result = collect_sections(
        {"memory": lambda now: collect_memory(now, config={}, provider_names=["honcho"])},
        sections=("memory",))[0]
    assert result.status == "unavailable"
    assert result.reason == "connector_not_configured"


def test_memory_provider_that_is_not_installed_is_a_missing_connector():
    result = collect_sections(
        {"memory": lambda now: collect_memory(
            now, config={"memory": {"provider": "gone"}}, provider_names=["honcho"])},
        sections=("memory",))[0]
    assert result.status == "unavailable"
    assert result.reason == "missing_connector"


def test_memory_reports_the_configured_installed_provider():
    result = collect_memory(
        NOW, config={"memory": {"provider": "honcho"}},
        provider_names=["honcho", "supermemory"])
    assert result.available
    assert "Provider: honcho." in result.facts
    assert "Provider modules found: 2." in result.facts


def test_compression_reports_only_durable_counters():
    result = collect_compression(
        NOW, snapshot=CompressionSnapshot(
            total_sessions=12, ineffective=2, overload=1, fallback=3, recovery_armed=1))
    assert result.available
    assert "Session store: reachable (12 session(s))." in result.facts
    assert "Sessions with ineffective-compaction strikes: 2." in result.facts
    assert "Sessions with overload aborts: 1." in result.facts
    assert "Sessions with fallback streaks: 3." in result.facts
    assert "Recovery timers armed: 1." in result.facts


def test_missing_session_store_is_an_honest_gap(tmp_path):
    missing = tmp_path / "state.db"
    result = collect_sections(
        {"compression": lambda now: collect_compression(now, db_path=missing)},
        sections=("compression",))[0]
    assert result.status == "unavailable"
    assert result.reason == "connector_not_configured"


def test_old_state_store_without_counters_is_not_supported(tmp_path):
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sessions (id TEXT)")
    conn.commit()
    conn.close()
    result = collect_sections(
        {"compression": lambda now: collect_compression(now, db_path=path)},
        sections=("compression",))[0]
    assert result.status == "unavailable"
    assert result.reason == "not_supported"


def test_compression_read_failure_is_a_tool_error(monkeypatch):
    def boom(db_path=None):
        raise RuntimeError("store read exploded")

    monkeypatch.setattr(monitor_operative, "_default_compression_snapshot", boom)
    result = collect_sections({"compression": collect_compression}, sections=("compression",))[0]
    assert result.status == "unavailable"
    assert result.reason == "tool_error"


def test_sections_degrade_independently():
    def broken(now):
        raise monitor_operative.SectionUnavailable("tool_error", "boom")

    collectors = {
        "cron": broken,
        "kanban": lambda now: collect_kanban(
            now, snapshot=KanbanSnapshot("default", {"ready": 1})),
        "memory": lambda now: collect_memory(
            now, config={"memory": {"provider": "honcho"}}, provider_names=["honcho"]),
        "compression": broken,
    }
    results = _by_id(collect_sections(collectors, now=NOW))
    assert not results["cron"].available
    assert results["kanban"].available
    assert results["memory"].available
    assert not results["compression"].available
    assert results["cron"].reason == "tool_error"


def test_facts_are_bounded_and_never_leak_secrets():
    jobs = [
        {"id": f"j{i}", "name": f"job sk-abcdefghijklmnop {i}", "enabled": True,
         "state": "scheduled", "last_status": "error", "failure_streak": 1,
         "next_run_at": "2026-10-27T10:00:00+00:00"}
        for i in range(40)
    ]
    result = collect_cron(
        NOW, jobs=jobs, heartbeat=5.0,
        last_error="token sk-abcdefghijklmnop rejected")
    assert len(result.facts) <= 20
    assert all(len(fact) <= 500 for fact in result.facts)
    joined = "\n".join(result.facts)
    assert "sk-abcdefghijklmnop" not in joined
    assert "+37 more" in joined


def test_secret_in_an_unavailable_detail_is_redacted(monkeypatch):
    def boom():
        raise RuntimeError("sk-abcdefghijklmnop")

    monkeypatch.setattr(monitor_operative, "_default_cron_jobs", boom)
    result = collect_sections({"cron": collect_cron}, sections=("cron",))[0]
    assert "sk-abcdefghijklmnop" not in result.detail


def test_dry_run_is_deterministic_and_never_calls_a_model():
    backend = _FakeBackend([TurnEvent("done", text="should not run")])
    collectors = {
        "cron": lambda now: collect_cron(now, jobs=[], heartbeat=1.0, last_error=None),
        "kanban": lambda now: collect_kanban(now, snapshot=KanbanSnapshot("default")),
        "memory": lambda now: collect_memory(
            now, config={"memory": {"provider": "honcho"}}, provider_names=["honcho"]),
        "compression": lambda now: collect_compression(
            now, snapshot=CompressionSnapshot(total_sessions=1)),
    }
    first = run_recipe(
        MONITOR_OPERATIVE, registry=_Registry({"fake": backend}), engine="fake",
        dry_run=True, collectors=collectors, now=NOW)
    second = run_recipe(
        MONITOR_OPERATIVE, registry=_Registry({"fake": backend}), engine="fake",
        dry_run=True, collectors=collectors, now=NOW)
    assert first.exit_code == 0
    assert backend.requests == []
    assert first.brief == second.brief
    assert first.brief.startswith("# Operative status")
    assert "## Cron" in first.brief and "## Compression" in first.brief
    assert [section.id for section in first.sections] == [
        "cron", "kanban", "memory", "compression"]


def test_runner_uses_the_spec_collectors_by_default(monkeypatch):
    monkeypatch.setattr(monitor_operative, "_default_cron_jobs", lambda: [])
    monkeypatch.setattr(monitor_operative, "_default_cron_heartbeat", lambda: (1.0, None))
    monkeypatch.setattr(
        monitor_operative, "_default_kanban_snapshot",
        lambda db_path=None: KanbanSnapshot("default", {"ready": 2}))
    monkeypatch.setattr(
        monitor_operative, "_default_memory_config",
        lambda: {"memory": {"provider": "honcho"}})
    monkeypatch.setattr(
        monitor_operative, "_default_memory_provider_names", lambda: ["honcho"])
    monkeypatch.setattr(
        monitor_operative, "_default_compression_snapshot",
        lambda db_path=None: CompressionSnapshot(total_sessions=3))

    backend = _FakeBackend([])
    run = run_recipe(
        MONITOR_OPERATIVE, registry=_Registry({"fake": backend}), engine="fake",
        dry_run=True, now=NOW)
    assert run.exit_code == 0
    assert all(section.available for section in run.sections)
    assert "2 open task(s)" in "\n".join(run.sections[1].facts)


def test_cli_dry_run_renders_the_brief(monkeypatch, tmp_path):
    from qubit_cli import recipe_cmd

    monkeypatch.setattr(monitor_operative, "_default_cron_jobs", lambda: [])
    monkeypatch.setattr(monitor_operative, "_default_cron_heartbeat", lambda: (1.0, None))
    monkeypatch.setattr(
        monitor_operative, "_default_kanban_snapshot",
        lambda db_path=None: KanbanSnapshot("default"))
    monkeypatch.setattr(
        monitor_operative, "_default_memory_config",
        lambda: {"memory": {"provider": "honcho"}})
    monkeypatch.setattr(
        monitor_operative, "_default_memory_provider_names", lambda: ["honcho"])
    monkeypatch.setattr(
        monitor_operative, "_default_compression_snapshot",
        lambda db_path=None: CompressionSnapshot())

    args = argparse.Namespace(
        recipe_action="run", recipe_id="monitor_operative", engine="native",
        dry_run=True, json=False)
    out = io.StringIO()
    with redirect_stdout(out):
        assert recipe_cmd.cmd_recipe(
            args, backend_registry=_Registry({"native": _FakeBackend()})) == 0
    text = out.getvalue()
    assert text.startswith("# Operative status")
    assert "## Cron" in text


def test_cli_json_listing_includes_monitor_operative():
    from qubit_cli.recipe_cmd import cmd_recipe

    out = io.StringIO()
    with redirect_stdout(out):
        assert cmd_recipe(argparse.Namespace(recipe_action="list", json=True)) == 0
    payload = json.loads(out.getvalue())
    assert [row["id"] for row in payload] == [
        "deep_research", "monitor_operative", "morning_digest"]


def test_monitor_cron_spec_is_metadata_only_and_never_creates_a_job(tmp_path, monkeypatch):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
    job = build_cron_job_spec(MONITOR_OPERATIVE)
    command = cron_create_command(MONITOR_OPERATIVE)
    assert job["name"] == "monitor-operative"
    assert job["schedule"] == "0 */4 * * *"
    assert job["recipe"] == "monitor_operative"
    assert job["command"] == "qubit recipe run monitor_operative --json"
    assert command.startswith("qubit cron create ")
    assert "monitor-operative" in command
    assert not (tmp_path / "cron").exists()


def test_unknown_recipe_and_engine_refusals_are_unchanged():
    from qubit_cli.recipe_cmd import cmd_recipe

    assert cmd_recipe(argparse.Namespace(
        recipe_action="run", recipe_id="nope", engine="native",
        dry_run=True, json=False)) == 2
    assert cmd_recipe(argparse.Namespace(
        recipe_action="run", recipe_id="monitor_operative", engine="nope",
        dry_run=True, json=False), backend_registry=_Registry({})) == 2
