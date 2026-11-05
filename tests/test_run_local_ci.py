"""Hermetic tests for ``scripts/run_local_ci.py``.

No child process, network, build, or install runs here: job builders are pure
functions over an injected :class:`Context`, and execution uses a fake runner.
The suite pins the safety contract (no publish/service verbs), the selection
and refusal paths, the typed job states, and the honest report header.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_local_ci.py"


def _load():
    spec = importlib.util.spec_from_file_location("run_local_ci", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


LC = _load()


def _npm_which(name):
    return "/usr/bin/npm" if name == "npm" else None


def _ctx(tmp_path: Path, **kwargs) -> "LC.Context":
    which = kwargs.pop("which", lambda _name: None)
    return LC.Context(repo=tmp_path, which=which, **kwargs)


def _fake_runner(codes):
    calls = []
    queue = list(codes)

    def run(argv, cwd, env):
        calls.append((list(argv), str(cwd), env))
        return queue.pop(0) if queue else (0, "")

    return run, calls


def test_job_catalog_lists_every_job_and_marks_opt_in():
    rows = {row["name"]: row for row in LC.job_catalog()}
    assert set(rows) == {
        "workflow-lint",
        "lockfiles",
        "runtime-smoke",
        "frontend",
        "bootstrap-tests",
        "release-qualification",
        "install-e2e",
    }
    assert rows["release-qualification"]["opt_in"] is True
    assert rows["install-e2e"]["opt_in"] is True
    for name in ("workflow-lint", "lockfiles", "runtime-smoke",
                 "frontend", "bootstrap-tests"):
        assert rows[name]["opt_in"] is False


def test_groups_expand_and_unknown_names_are_reported():
    selected, unknown = LC.expand_jobs(["fast", "frontend"])
    assert selected[0] == "workflow-lint"
    assert "frontend" in selected
    assert unknown == []

    selected, unknown = LC.expand_jobs(["all", "does-not-exist"])
    assert "release-qualification" in selected
    assert unknown == ["does-not-exist"]


def test_list_json_is_machine_readable(capsys):
    assert LC.main(["--list", "--json"]) == LC.EXIT_PASS
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    assert {row["name"] for row in payload} >= {"workflow-lint", "install-e2e"}


def test_unknown_job_is_blocked(capsys):
    assert LC.main(["--job", "nope"]) == LC.EXIT_BLOCKED
    captured = capsys.readouterr()
    assert "unknown job or group: nope" in captured.err
    assert "BLOCKED" in captured.out


def test_opt_in_jobs_refuse_without_the_flag(capsys):
    assert LC.main(["--job", "release-qualification", "--json"]) == LC.EXIT_BLOCKED
    report = json.loads(capsys.readouterr().out)
    assert report["verdict"] == "BLOCKED"
    job = report["jobs"][0]
    assert job["state"] == "blocked"
    assert "--allow-opt-in" in job["detail"]
    assert job["steps"] == []


def test_report_is_explicitly_local_and_not_hosted(capsys):
    assert LC.main(["--job", "workflow-lint", "--dry-run", "--json"]) == LC.EXIT_PASS
    report = json.loads(capsys.readouterr().out)
    assert report["execution"] == "local"
    assert report["hosted_equivalent"] is False
    assert "not GitHub-hosted" in report["claim"]
    assert report["dry_run"] is True
    assert report["verdict"] == "PLANNED"


def test_dry_run_executes_nothing(tmp_path):
    (tmp_path / "qubit_runtime").mkdir()
    (tmp_path / "qubit_runtime" / "qubit_state.py").write_text(
        "VALUE = 1\n", encoding="utf-8")
    plan = LC.plan_runtime_smoke(_ctx(tmp_path))
    runner, calls = _fake_runner([])
    result = LC.run_job(plan, runner=runner, dry_run=True)
    assert result.state == "planned"
    assert calls == []
    assert all(step.state == "planned" for step in result.steps)


def test_missing_actionlint_degrades_but_policy_still_runs(tmp_path):
    plan = LC.plan_workflow_lint(_ctx(tmp_path))
    assert plan.steps[0].unavailable
    runner, calls = _fake_runner([(0, "policy ok")])
    result = LC.run_job(plan, runner=runner)
    assert result.state == "degraded"
    assert len(calls) == 1
    assert calls[0][0][1:] == [".github/scripts/check_workflow_security.py"]


def test_missing_npm_blocks_the_lockfile_job(tmp_path):
    plan = LC.plan_lockfiles(_ctx(tmp_path))
    assert plan.blocked and "npm not found" in plan.blocked
    runner, calls = _fake_runner([])
    result = LC.run_job(plan, runner=runner, dry_run=True)
    assert result.state == "blocked"
    assert calls == []


def test_lockfile_job_uses_dry_run_and_flags_website_npm(tmp_path):
    for rel in LC.NPM_LOCKFILE_DIRS + (LC.NPM_WEBSITE_DIR,):
        directory = tmp_path / rel
        directory.mkdir(parents=True)
        (directory / "package-lock.json").write_text("{}", encoding="utf-8")

    plan = LC.plan_lockfiles(
        _ctx(tmp_path, npm_version="10.9.8", which=_npm_which))
    assert not plan.warnings
    by_name = {step.name: step for step in plan.steps}
    website = by_name[f"npm ci --dry-run {LC.NPM_WEBSITE_DIR}"]
    assert website.unavailable and "requires npm >= 11.17.0" in website.unavailable
    for rel in LC.NPM_LOCKFILE_DIRS:
        step = by_name[f"npm ci --dry-run {rel}"]
        assert step.argv[0].endswith("npm")
        assert step.argv[1:] == ("ci", "--ignore-scripts", "--no-audit",
                                 "--no-fund", "--dry-run")


def test_npm_version_mismatch_degrades_an_otherwise_passing_job(tmp_path):
    plan = LC.plan_lockfiles(
        _ctx(tmp_path, npm_version="10.8.0", which=_npm_which))
    assert plan.warnings
    runner, _ = _fake_runner([(0, "") for _ in plan.steps])
    result = LC.run_job(plan, runner=runner)
    assert result.state == "degraded"


def test_runtime_smoke_compiles_real_state_modules():
    # Regression for the hosted instruction that named a non-existent
    # ``qubit_runtime/qubit_state`` directory and was silently skipped.
    plan = LC.plan_runtime_smoke(LC.Context(repo=ROOT, which=lambda _n: None))
    assert not plan.blocked
    compile_step = plan.steps[0]
    assert "compileall" in compile_step.argv
    targets = [arg for arg in compile_step.argv[4:]]
    for rel in targets:
        assert (ROOT / rel).exists(), rel
    assert any(rel.startswith("qubit_runtime/qubit_state") for rel in targets)


def test_ci_workflow_uses_the_real_state_module_glob():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8")
    assert "qubit_runtime/qubit_state*.py" in workflow
    for line in workflow.splitlines():
        assert line.strip() != "qubit_runtime/qubit_state"


def test_require_clean_blocks_compile_on_a_dirty_tree(tmp_path):
    plan = LC.plan_runtime_smoke(
        _ctx(tmp_path, dirty=True, require_clean=True))
    assert plan.blocked and "dirty" in plan.blocked


def test_failure_stops_later_steps_and_is_fail(tmp_path):
    plan = LC.JobPlan(
        name="synthetic",
        description="synthetic",
        opt_in=False,
        steps=(
            LC.Step("first", ("true",)),
            LC.Step("second", ("true",)),
        ),
    )
    runner, calls = _fake_runner([(1, "boom")])
    result = LC.run_job(plan, runner=runner)
    assert result.state == "fail"
    assert len(calls) == 1
    assert [step.state for step in result.steps] == ["fail", "skipped"]


def test_typed_exit_codes_map_to_job_states(tmp_path):
    for code, expected in ((0, "pass"), (2, "fail"), (4, "degraded"),
                           (8, "blocked")):
        plan = LC.JobPlan(
            name="synthetic",
            description="synthetic",
            opt_in=False,
            steps=(LC.Step("run", ("true",)),),
        )
        runner, _ = _fake_runner([(code, "")])
        assert LC.run_job(plan, runner=runner).state == expected, code


def test_unsafe_argv_is_refused_before_execution(tmp_path):
    for argv in (
        ("gh", "release", "create", "v1"),
        ("gh", "release", "upload", "v1", "x.whl"),
        ("twine", "upload", "dist/*"),
        ("npm", "publish"),
        ("git", "push", "origin", "main"),
        ("git", "tag", "v1"),
        ("systemctl", "restart", "qubit-voicebridge.service"),
        ("service", "stop", "qubit-gateway"),
        ("launchctl", "bootstrap", "gui/501", "x.plist"),
    ):
        assert LC.unsafe_reason(argv), argv

    plan = LC.JobPlan(
        name="unsafe",
        description="unsafe",
        opt_in=False,
        steps=(LC.Step("publish", ("gh", "release", "create", "v1")),),
    )
    runner, calls = _fake_runner([(0, "")])
    result = LC.run_job(plan, runner=runner)
    assert result.state == "blocked"
    assert calls == []


def test_shipped_jobs_never_publish_or_control_services(tmp_path):
    context = LC.Context(
        repo=tmp_path,
        which=lambda name: f"/usr/bin/{name}",
        npm_version=LC.NPM_LOCKFILE_VERSION,
        node_version=LC.HOSTED_NODE,
        work_dir=tmp_path / "work",
    )
    for name, builder in LC.BUILDERS.items():
        plan = builder(context)
        assert plan.name == name
        for step in plan.steps:
            assert LC.unsafe_reason(step.argv) is None, (name, step.name)


def test_script_never_uses_a_shell_or_workflow_writes():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert "pull_request_target" not in source
    # No writes to the hosted workflow directory.
    assert ".github/workflows" not in source.replace(
        "``.github/workflows/``", "")


def test_build_report_is_deterministic_and_typed():
    report_a = LC.build_report(
        repo=Path("/repo"),
        jobs=[],
        selected=["workflow-lint"],
        unknown=[],
        dry_run=True,
        context=LC.Context(repo=Path("/repo"), which=lambda _n: None),
    )
    report_b = LC.build_report(
        repo=Path("/repo"),
        jobs=[],
        selected=["workflow-lint"],
        unknown=[],
        dry_run=True,
        context=LC.Context(repo=Path("/repo"), which=lambda _n: None),
    )
    assert report_a == report_b
    assert report_a["schema"] == "qubit.local-ci.v1"
    assert report_a["verdict"] == "PLANNED"
    assert report_a["hosted_equivalent"] is False
