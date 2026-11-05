#!/usr/bin/env python3
"""Deterministic local equivalent of the GitHub-hosted release CI.

The GitHub-hosted workflows in ``.github/workflows/`` are the release
authority, but the public repository's hosted jobs are blocked account-side
(see ``docs/STATUS.md``). This runner reproduces the *actionable* jobs on the
machine that owns the checkout, with typed outcomes and an explicit statement
that this is **not** GitHub-hosted execution.

Design rules:

* every job is a fixed list of argv steps -- no shell interpolation, no
  secrets, no environment mutation beyond the child process;
* publish, tag-push, release-creation and service-control verbs are rejected
  before any step runs (``assert_safe_argv``);
* optional jobs (release qualification, artifact install E2E) refuse unless
  ``--allow-opt-in`` is passed;
* a missing required tool is BLOCKED, an optional tool (``actionlint``) is
  DEGRADED -- neither is ever reported as a pass;
* no job starts, stops, enables or restarts a service; nothing here publishes
  or creates a release.

Typed exit codes:

    0 PASS      every selected job passed
    2 FAIL      a job failed for a real defect
    4 DEGRADED  a prerequisite or pinned-tool mismatch prevented parity
    8 BLOCKED   an unsafe request, a missing required tool, or a dirty tree
                when ``--require-clean`` was asked for

``--dry-run`` prints the exact plan and runs nothing (verdict ``PLANNED``).
``--list`` prints the job catalog and exits 0.

Usage::

    python3 scripts/run_local_ci.py --list
    python3 scripts/run_local_ci.py --job fast
    python3 scripts/run_local_ci.py --job workflow-lint --json
    python3 scripts/run_local_ci.py --job all            # skips opt-in jobs
    python3 scripts/run_local_ci.py --job all --allow-opt-in
    python3 scripts/run_local_ci.py --job release-qualification --allow-opt-in
    python3 scripts/run_local_ci.py --job install-e2e --allow-opt-in

This script never claims a ``VERIFIED`` release verdict and never writes to
``.github/``.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

REPO = Path(__file__).resolve().parents[1]

EXIT_PASS = 0
EXIT_FAIL = 2
EXIT_DEGRADED = 4
EXIT_BLOCKED = 8

SCHEMA = "qubit.local-ci.v1"

#: What the hosted workflow pins. Recorded so a local run cannot pretend it
#: used the same toolchain.
HOSTED_PYTHON = "3.12"
HOSTED_NODE = "22"
NPM_LOCKFILE_VERSION = "10.9.8"
NPM_WEBSITE_VERSION = "11.17.0"
NPM_LOCKFILE_DIRS = (
    "frontend/qubit-web",
    "qubit_runtime",
    "qubit_runtime/scripts/whatsapp-bridge",
)
NPM_WEBSITE_DIR = "qubit_runtime/website"

#: Verbs that must never appear in a locally executed step. This is a
#: belt-and-braces guard: the job table below is already static and safe, but a
#: future edit must fail closed instead of silently publishing/restarting.
FORBIDDEN_ARGV = (
    ("gh", "release", "create"),
    ("gh", "release", "upload"),
    ("gh", "release", "edit"),
    ("gh", "workflow", "run"),
    ("twine", "upload"),
    ("npm", "publish"),
    ("docker", "push"),
    ("cargo", "publish"),
    ("git", "push"),
    ("git", "tag"),
    ("systemctl", "restart"),
    ("systemctl", "start"),
    ("systemctl", "stop"),
    ("systemctl", "enable"),
    ("systemctl", "disable"),
    ("service", "restart"),
    ("service", "start"),
    ("service", "stop"),
    ("launchctl", "bootstrap"),
    ("launchctl", "bootout"),
    ("launchctl", "kickstart"),
    ("supervisorctl", "restart"),
    ("supervisorctl", "start"),
    ("supervisorctl", "stop"),
)
#: These argv prefixes are the real refusal surface (a tool named "gh" alone
#: is not enough; the subcommand must also match).
FORBIDDEN_PREFIXES = FORBIDDEN_ARGV


def _npm_tuple(version: str | None) -> tuple[int, int, int] | None:
    if not version:
        return None
    parts = version.strip().split(".")
    try:
        nums = [int("".join(ch for ch in part if ch.isdigit()) or "0")
                for part in parts[:3]]
    except ValueError:
        return None
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)  # type: ignore[return-value]


def _python_tuple() -> tuple[int, int, int]:
    return (sys.version_info.major, sys.version_info.minor,
            sys.version_info.micro)


def _python_supported() -> bool:
    return (3, 12) <= _python_tuple()[:2] < (3, 15)


@dataclass(frozen=True)
class Step:
    """One child process. ``argv`` is always an explicit list."""

    name: str
    argv: tuple[str, ...]
    cwd: Path | None = None
    env: dict[str, str] | None = None
    #: When set the step is not run; the job is DEGRADED with this reason.
    unavailable: str | None = None
    #: A failure here degrades the job instead of failing it.
    optional: bool = False


@dataclass(frozen=True)
class JobPlan:
    name: str
    description: str
    opt_in: bool
    steps: tuple[Step, ...]
    blocked: str | None = None
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass
class Context:
    """Everything a job builder needs, injectable for hermetic tests."""

    repo: Path = REPO
    python: str = sys.executable or "python3"
    which: Callable[[str], str | None] = shutil.which
    npm_version: str | None = None
    node_version: str | None = None
    dirty: bool = False
    require_clean: bool = False
    work_dir: Path | None = None
    tag: str | None = None
    verbose: bool = False

    def tool(self, name: str) -> str | None:
        found = self.which(name)
        return found or None

    def cwd(self, rel: str | Path) -> Path:
        return self.repo / rel


# ---------------------------------------------------------------------------
# Job catalog
# ---------------------------------------------------------------------------

def plan_workflow_lint(ctx: Context) -> JobPlan:
    steps: list[Step] = []
    actionlint = ctx.tool("actionlint")
    if actionlint is None:
        steps.append(Step(
            name="actionlint",
            argv=(),
            unavailable=(
                "actionlint not found on PATH; the hosted job pins "
                "rhysd/actionlint:1.7.12 by digest"
            ),
        ))
    else:
        steps.append(Step(name="actionlint", argv=(actionlint,), cwd=ctx.repo))
    steps.append(Step(
        name="workflow security policy",
        argv=(ctx.python, ".github/scripts/check_workflow_security.py"),
        cwd=ctx.repo,
    ))
    notes = ["hosted actionlint is a digest-pinned Docker image; local uses PATH"]
    return JobPlan(
        name="workflow-lint",
        description="actionlint (if present) + workflow security policy",
        opt_in=False,
        steps=tuple(steps),
        notes=tuple(notes),
    )


def plan_lockfiles(ctx: Context) -> JobPlan:
    npm = ctx.tool("npm")
    if npm is None:
        return JobPlan(
            name="lockfiles",
            description="npm lockfile integrity (dry-run)",
            opt_in=False,
            steps=(),
            blocked="npm not found on PATH",
        )

    steps: list[Step] = []
    warnings: list[str] = []
    actual = ctx.npm_version
    if actual != NPM_LOCKFILE_VERSION:
        warnings.append(
            f"local npm {actual or 'unknown'} != hosted pin "
            f"{NPM_LOCKFILE_VERSION}; dry-run results may differ"
        )
    for rel in NPM_LOCKFILE_DIRS:
        lock = ctx.cwd(rel) / "package-lock.json"
        if not lock.is_file():
            steps.append(Step(
                name=f"lockfile present: {rel}",
                argv=(),
                unavailable=f"{rel}/package-lock.json is missing",
            ))
            continue
        steps.append(Step(
            name=f"npm ci --dry-run {rel}",
            argv=(npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund",
                  "--dry-run"),
            cwd=ctx.cwd(rel),
        ))

    website_ok = _npm_tuple(actual) is not None and _npm_tuple(actual) >= _npm_tuple(NPM_WEBSITE_VERSION)  # type: ignore[operator]
    website_lock = ctx.cwd(NPM_WEBSITE_DIR) / "package-lock.json"
    if not website_lock.is_file():
        steps.append(Step(
            name=f"lockfile present: {NPM_WEBSITE_DIR}",
            argv=(),
            unavailable=f"{NPM_WEBSITE_DIR}/package-lock.json is missing",
        ))
    elif not website_ok:
        steps.append(Step(
            name=f"npm ci --dry-run {NPM_WEBSITE_DIR}",
            argv=(),
            unavailable=(
                f"requires npm >= {NPM_WEBSITE_VERSION} (engine-strict); "
                f"local npm is {actual or 'unknown'}"
            ),
        ))
    else:
        steps.append(Step(
            name=f"npm ci --dry-run {NPM_WEBSITE_DIR}",
            argv=(npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund",
                  "--dry-run"),
            cwd=ctx.cwd(NPM_WEBSITE_DIR),
        ))

    return JobPlan(
        name="lockfiles",
        description="npm lockfile integrity (dry-run, no node_modules)",
        opt_in=False,
        steps=tuple(steps),
        warnings=tuple(warnings),
        notes=(f"hosted npm pins: {NPM_LOCKFILE_VERSION} / website "
               f"{NPM_WEBSITE_VERSION}",),
    )


def _metadata_smoke_source() -> str:
    return (
        "import pathlib, tomllib\n"
        "root = pathlib.Path('.')\n"
        "data = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))\n"
        "project = data['project']\n"
        "assert project['name'] == 'qubit-voicebridge', project['name']\n"
        "for rel in ('qubit_runtime/qubit_cli/main.py', 'voicebridge/cli.py',\n"
        "            'frontend/qubit-web/package.json', 'scripts/release_qualify.py'):\n"
        "    assert (root / rel).is_file(), f'missing {rel}'\n"
        "print('runtime-smoke-ok', project['version'])\n"
    )


def plan_runtime_smoke(ctx: Context) -> JobPlan:
    if not _python_supported():
        return JobPlan(
            name="runtime-smoke",
            description="runtime compile + root metadata smoke",
            opt_in=False,
            steps=(),
            blocked=(
                f"Python {sys.version.split()[0]} is outside the project's "
                "requires-python >=3.12,<3.15"
            ),
        )
    if ctx.require_clean and ctx.dirty:
        return JobPlan(
            name="runtime-smoke",
            description="runtime compile + root metadata smoke",
            opt_in=False,
            steps=(),
            blocked="--require-clean was requested but the worktree is dirty",
        )

    # ``qubit_runtime/qubit_state`` is a module family, not a directory; an
    # earlier hosted instruction listed the directory and compileall silently
    # skipped it (exit 0 with "Can't list"). Use the real modules.
    state_modules = tuple(
        str(path.relative_to(ctx.repo))
        for path in sorted(ctx.repo.glob("qubit_runtime/qubit_state*.py"))
    )
    if not state_modules:
        return JobPlan(
            name="runtime-smoke",
            description="runtime compile + root metadata smoke",
            opt_in=False,
            steps=(),
            blocked="no qubit_runtime/qubit_state*.py modules found",
        )
    compile_targets = (
        "qubit_runtime/qubit_cli",
        "qubit_runtime/gateway",
        "qubit_runtime/tools",
        "qubit_runtime/agent",
        "qubit_runtime/tui_gateway",
        *state_modules,
        "voicebridge",
        "scripts",
    )
    steps = [
        Step(
            name="compile runtime and voice surface",
            argv=(ctx.python, "-m", "compileall", "-q", *compile_targets),
            cwd=ctx.repo,
        ),
        Step(
            name="parse-check runtime tests",
            argv=(ctx.python, "-m", "compileall", "-q", "qubit_runtime/tests"),
            cwd=ctx.repo,
        ),
        Step(
            name="root metadata smoke",
            argv=(ctx.python, "-c", _metadata_smoke_source()),
            cwd=ctx.repo,
        ),
    ]
    notes: list[str] = []
    if _python_tuple()[:2] != tuple(int(p) for p in HOSTED_PYTHON.split(".")):
        notes.append(
            f"local Python {sys.version.split()[0]} != hosted {HOSTED_PYTHON} "
            "(compile-only; informational)"
        )
    return JobPlan(
        name="runtime-smoke",
        description="runtime compile + root metadata smoke",
        opt_in=False,
        steps=tuple(steps),
        notes=tuple(notes),
    )


def plan_frontend(ctx: Context) -> JobPlan:
    npm = ctx.tool("npm")
    node = ctx.tool("node")
    missing = [name for name, found in (("npm", npm), ("node", node))
               if found is None]
    if missing:
        return JobPlan(
            name="frontend",
            description="frontend typecheck, tests, and build",
            opt_in=False,
            steps=(),
            blocked=f"missing on PATH: {', '.join(missing)}",
        )
    assert npm is not None
    steps = (
        Step(
            name="install dashboard dependencies",
            argv=(npm, "ci", "--legacy-peer-deps", "--no-audit", "--no-fund"),
            cwd=ctx.cwd("frontend/qubit-web"),
        ),
        Step(
            name="install shared package dependencies",
            argv=(npm, "install", "--include=dev", "--legacy-peer-deps",
                  "--no-audit", "--no-fund", "--no-package-lock"),
            cwd=ctx.cwd("frontend/qubit-shared"),
        ),
        Step(name="typecheck", argv=(npm, "run", "typecheck"),
             cwd=ctx.cwd("frontend/qubit-web")),
        Step(name="tests", argv=(npm, "run", "test"),
             cwd=ctx.cwd("frontend/qubit-web")),
        Step(name="build", argv=(npm, "run", "build"),
             cwd=ctx.cwd("frontend/qubit-web")),
    )
    warnings: list[str] = []
    if ctx.node_version and ctx.node_version.split(".")[0] != HOSTED_NODE:
        warnings.append(
            f"local Node {ctx.node_version} != hosted {HOSTED_NODE}; "
            "the bundle may still be valid but parity is not exact"
        )
    return JobPlan(
        name="frontend",
        description="frontend typecheck, tests, and build",
        opt_in=False,
        steps=steps,
        warnings=tuple(warnings),
    )


def plan_bootstrap(ctx: Context) -> JobPlan:
    bash = ctx.tool("bash")
    if bash is None:
        return JobPlan(
            name="bootstrap-tests",
            description="bootstrap shell syntax + hermetic installer tests",
            opt_in=False,
            steps=(),
            blocked="bash not found on PATH",
        )
    if not (ctx.repo / "scripts/bootstrap.sh").is_file():
        return JobPlan(
            name="bootstrap-tests",
            description="bootstrap shell syntax + hermetic installer tests",
            opt_in=False,
            steps=(),
            blocked="scripts/bootstrap.sh is missing",
        )
    try:
        import pytest  # noqa: F401
    except Exception:  # noqa: BLE001 - report as a typed blocker
        return JobPlan(
            name="bootstrap-tests",
            description="bootstrap shell syntax + hermetic installer tests",
            opt_in=False,
            steps=(),
            blocked="pytest is not importable by this interpreter",
        )
    steps = (
        Step(name="shell syntax", argv=(bash, "-n", "scripts/bootstrap.sh"),
             cwd=ctx.repo),
        Step(name="hermetic bootstrap tests",
             argv=(ctx.python, "-m", "pytest", "-q",
                   "tests/test_bootstrap_installer.py"),
             cwd=ctx.repo),
    )
    return JobPlan(
        name="bootstrap-tests",
        description="bootstrap shell syntax + hermetic installer tests",
        opt_in=False,
        steps=steps,
    )


def plan_release_qualification(ctx: Context) -> JobPlan:
    work = ctx.work_dir or Path(tempfile.gettempdir()) / "qubit-local-ci"
    out = work / "release"
    argv = [
        ctx.python, "scripts/release_qualify.py",
        "--build", "--install", "--reproduce", "--unsigned-ok",
        "--json", "--out", str(out),
    ]
    if ctx.tag:
        argv.extend(["--tag", ctx.tag])
    notes = (
        "never signs and never publishes; --unsigned-ok records a draft policy",
        f"artifacts/report under {out}",
    )
    return JobPlan(
        name="release-qualification",
        description="release qualification (build, install, reproduce)",
        opt_in=True,
        steps=(Step(name="release qualification", argv=tuple(argv),
                    cwd=ctx.repo),),
        notes=notes,
    )


def plan_install_e2e(ctx: Context) -> JobPlan:
    tooling_missing = ctx.tool("bash") is None or ctx.tool("npm") is None
    if tooling_missing:
        return JobPlan(
            name="install-e2e",
            description="artifact install E2E (build wheel, isolated install)",
            opt_in=True,
            steps=(),
            blocked="bash and npm are required to build the dashboard bundle",
        )
    has_uv = ctx.tool("uv") is not None
    has_build = False
    try:
        import build  # noqa: F401
        has_build = True
    except Exception:  # noqa: BLE001
        has_build = False
    if not (has_uv or has_build):
        return JobPlan(
            name="install-e2e",
            description="artifact install E2E (build wheel, isolated install)",
            opt_in=True,
            steps=(),
            blocked=(
                "no wheel builder available: install 'uv' or the 'build' "
                "module (the hosted job installs uv)"
            ),
        )
    work = ctx.work_dir or Path(tempfile.gettempdir()) / "qubit-local-ci"
    report = work / "artifact-install-e2e.json"
    steps = (
        Step(name="build dashboard bundle",
             argv=(ctx.tool("bash") or "bash", "scripts/build_dashboard.sh"),
             cwd=ctx.repo),
        Step(
            name="artifact install E2E",
            argv=(ctx.python, "scripts/artifact_install_e2e.py",
                  "--build", "--require-network", "--report", str(report)),
            cwd=ctx.repo,
        ),
    )
    return JobPlan(
        name="install-e2e",
        description="artifact install E2E (build wheel, isolated install)",
        opt_in=True,
        steps=steps,
        notes=(
            "requires network for dependency install; the installed wheel is "
            "probed in a throwaway HOME/venv",
            f"report under {report}",
        ),
    )


BUILDERS: dict[str, Callable[[Context], JobPlan]] = {
    "workflow-lint": plan_workflow_lint,
    "lockfiles": plan_lockfiles,
    "runtime-smoke": plan_runtime_smoke,
    "frontend": plan_frontend,
    "bootstrap-tests": plan_bootstrap,
    "release-qualification": plan_release_qualification,
    "install-e2e": plan_install_e2e,
}

JOB_ORDER = tuple(BUILDERS)

GROUPS: dict[str, tuple[str, ...]] = {
    "all": JOB_ORDER,
    "fast": ("workflow-lint", "lockfiles", "runtime-smoke", "bootstrap-tests"),
}
DEFAULT_JOBS = ("workflow-lint", "lockfiles", "runtime-smoke", "frontend",
                "bootstrap-tests")


def job_catalog() -> list[dict]:
    rows = []
    for name in JOB_ORDER:
        # Descriptions are static; build against a no-op context.
        spec = BUILDERS[name](Context(which=lambda _n: None))
        rows.append({
            "name": name,
            "description": spec.description,
            "opt_in": spec.opt_in,
            "required_tools": sorted(_required_tools(spec)),
        })
    return rows


def _required_tools(plan: JobPlan) -> set[str]:
    tools: set[str] = set()
    for step in plan.steps:
        if not step.argv:
            continue
        first = Path(step.argv[0]).name
        if first in {"python", "python3", "python3.12"}:
            continue
        if first == "bash":
            tools.add("bash")
        elif first == "npm":
            tools.add("npm")
        elif first == "node":
            tools.add("node")
        elif first == "actionlint":
            tools.add("actionlint")
        elif first == "uv":
            tools.add("uv")
    return tools


# ---------------------------------------------------------------------------
# Selection and execution
# ---------------------------------------------------------------------------

def expand_jobs(requested: Iterable[str]) -> tuple[list[str], list[str]]:
    """Expand group names; return (expanded names, unknown names)."""
    unknown: list[str] = []
    selected: list[str] = []
    for raw in requested:
        for name in str(raw).split(","):
            name = name.strip()
            if not name:
                continue
            if name in GROUPS:
                for expanded in GROUPS[name]:
                    if expanded not in selected:
                        selected.append(expanded)
            elif name in BUILDERS:
                if name not in selected:
                    selected.append(name)
            else:
                if name not in unknown:
                    unknown.append(name)
    return selected, unknown


def unsafe_reason(argv: Sequence[str]) -> str | None:
    """Return a refusal reason when an argv would publish or control services."""
    joined = " ".join(str(part) for part in argv)
    for prefix in FORBIDDEN_PREFIXES:
        if tuple(str(part) for part in argv[:len(prefix)]) == prefix:
            return f"forbidden local-CI operation: {' '.join(prefix)}"
    if "npm publish" in joined or "git push" in joined:
        return f"forbidden local-CI operation in argv: {joined[:120]}"
    return None


@dataclass
class StepResult:
    name: str
    argv: list[str]
    cwd: str | None
    state: str
    exit_code: int | None = None
    detail: str | None = None
    output_tail: str | None = None
    duration_s: float | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "argv": self.argv,
            "cwd": self.cwd,
            "state": self.state,
            "exit_code": self.exit_code,
            "detail": self.detail,
            "output_tail": self.output_tail,
            "duration_s": (round(self.duration_s, 3)
                           if self.duration_s is not None else None),
        }


@dataclass
class JobResult:
    name: str
    description: str
    opt_in: bool
    state: str
    steps: list[StepResult] = field(default_factory=list)
    detail: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "opt_in": self.opt_in,
            "state": self.state,
            "detail": self.detail,
            "notes": self.notes,
            "steps": [step.to_dict() for step in self.steps],
        }


_SEVERITY = {"pass": 0, "planned": 0, "degraded": 1, "fail": 2,
             "blocked": 3}


def _worst(states: Iterable[str]) -> str:
    worst = "pass"
    for state in states:
        if _SEVERITY.get(state, 2) > _SEVERITY.get(worst, 0):
            worst = state
    return worst


def _tail(text: str | None, lines: int = 40) -> str | None:
    if not text:
        return None
    chunks = text.strip().splitlines()
    if len(chunks) <= lines:
        return "\n".join(chunks)
    return "\n".join(chunks[-lines:])


def run_step(
    step: Step,
    *,
    cwd: Path,
    runner: Callable[[Sequence[str], Path, dict[str, str] | None], tuple[int, str]],
    dry_run: bool,
    verbose: bool,
) -> StepResult:
    argv = list(step.argv)
    cwd_text = str(step.cwd or cwd)
    reason = unsafe_reason(argv)
    if reason is not None:
        return StepResult(step.name, argv, cwd_text, "blocked", detail=reason)
    if step.unavailable:
        state = "planned" if dry_run else "degraded"
        detail = (f"would be skipped: {step.unavailable}" if dry_run
                  else step.unavailable)
        return StepResult(step.name, argv, cwd_text, state, detail=detail)
    if dry_run:
        return StepResult(step.name, argv, cwd_text, "planned")

    started = time.monotonic()
    try:
        code, output = runner(argv, step.cwd or cwd, step.env)
    except FileNotFoundError as exc:
        return StepResult(step.name, argv, cwd_text, "blocked",
                          detail=f"tool not found: {exc}")
    except OSError as exc:
        return StepResult(step.name, argv, cwd_text, "fail",
                          detail=f"could not execute: {exc}")
    duration = time.monotonic() - started

    if code == 0:
        state = "pass"
        detail = None
    elif code == 4:
        state = "degraded"
        detail = "tool reported DEGRADED"
    elif code == 8:
        state = "blocked"
        detail = "tool reported BLOCKED"
    else:
        state = "fail"
        detail = f"exit {code}"
    if code != 0 and step.optional and state == "fail":
        state = "degraded"
        detail = f"optional step failed (exit {code})"

    return StepResult(
        step.name,
        argv,
        cwd_text,
        state,
        exit_code=code,
        detail=detail,
        output_tail=_tail(output, 200 if verbose else 40),
        duration_s=duration,
    )


def run_job(
    plan: JobPlan,
    *,
    runner: Callable[[Sequence[str], Path, dict[str, str] | None], tuple[int, str]],
    dry_run: bool = False,
    verbose: bool = False,
) -> JobResult:
    result = JobResult(
        name=plan.name,
        description=plan.description,
        opt_in=plan.opt_in,
        state="pass",
        detail=plan.blocked,
        notes=list(plan.notes) + list(plan.warnings),
    )
    if plan.blocked:
        result.state = "blocked"
        return result

    states: list[str] = []
    for step in plan.steps:
        step_result = run_step(step, cwd=plan.steps[0].cwd or REPO,
                               runner=runner, dry_run=dry_run,
                               verbose=verbose)
        result.steps.append(step_result)
        states.append(step_result.state)
        if step_result.state in {"fail", "blocked"}:
            # Fail closed: do not run later steps after a real failure or a
            # refusal. The remaining steps are reported as skipped.
            for remaining in plan.steps[len(result.steps):]:
                result.steps.append(StepResult(
                    remaining.name, list(remaining.argv),
                    str(remaining.cwd or REPO), "skipped",
                    detail="not run after an earlier non-pass step",
                ))
                states.append("skipped")
            break

    if plan.warnings and all(state == "pass" for state in states):
        result.state = "degraded"
    elif dry_run and all(state == "planned" for state in states):
        result.state = "planned"
    else:
        result.state = _worst(states)
    return result


def _subprocess_runner() -> Callable[
        [Sequence[str], Path, dict[str, str] | None], tuple[int, str]]:
    def run(argv: Sequence[str], cwd: Path,
            env: dict[str, str] | None) -> tuple[int, str]:
        child_env = dict(os.environ)
        if env:
            child_env.update(env)
        completed = subprocess.run(
            list(argv),
            cwd=str(cwd),
            env=child_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        return completed.returncode, completed.stdout or ""

    return run


def _print_step(step: StepResult, verbose: bool) -> None:
    label = step.state.upper()
    print(f"  [{label}] {step.name}")
    if step.detail:
        print(f"         {step.detail}")
    if step.output_tail and (verbose or step.state not in {"pass", "planned"}):
        for line in step.output_tail.splitlines():
            print(f"         | {line}")


# ---------------------------------------------------------------------------
# Reporting and CLI
# ---------------------------------------------------------------------------

def build_report(
    *,
    repo: Path,
    jobs: list[JobResult],
    selected: list[str],
    unknown: list[str],
    dry_run: bool,
    context: Context,
) -> dict:
    if unknown or any(job.state == "blocked" for job in jobs):
        verdict = "BLOCKED"
    elif dry_run:
        verdict = "PLANNED"
    elif any(job.state == "fail" for job in jobs):
        verdict = "FAIL"
    elif any(job.state == "degraded" for job in jobs):
        verdict = "DEGRADED"
    else:
        verdict = "PASS"
    return {
        "schema": SCHEMA,
        "execution": "local",
        "hosted_equivalent": False,
        "claim": (
            "local reproduction of the actionable hosted jobs; this is not "
            "GitHub-hosted execution and not a release verdict"
        ),
        "repo": str(repo),
        "selected": selected,
        "unknown": unknown,
        "dry_run": dry_run,
        "toolchain": {
            "python": sys.version.split()[0],
            "hosted_python": HOSTED_PYTHON,
            "node": context.node_version,
            "hosted_node": HOSTED_NODE,
            "npm": context.npm_version,
            "hosted_npm_lockfiles": NPM_LOCKFILE_VERSION,
            "hosted_npm_website": NPM_WEBSITE_VERSION,
            "actionlint": context.tool("actionlint"),
        },
        "tree": {
            "dirty": context.dirty,
            "require_clean": context.require_clean,
        },
        "jobs": [job.to_dict() for job in jobs],
        "verdict": verdict,
    }


def _detect_context(args: argparse.Namespace) -> Context:
    repo = Path(args.repo).resolve()
    which = shutil.which

    npm_version = None
    npm_path = which("npm")
    if npm_path:
        try:
            completed = subprocess.run(
                [npm_path, "--version"], text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                check=False, timeout=20,
            )
            npm_version = (completed.stdout or "").strip() or None
        except Exception:  # noqa: BLE001
            npm_version = None
    node_version = None
    node_path = which("node")
    if node_path:
        try:
            completed = subprocess.run(
                [node_path, "--version"], text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                check=False, timeout=20,
            )
            node_version = (completed.stdout or "").strip().lstrip("v") or None
        except Exception:  # noqa: BLE001
            node_version = None

    dirty = False
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain"], cwd=str(repo), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, check=False, timeout=30,
        )
        dirty = bool((completed.stdout or "").strip())
    except Exception:  # noqa: BLE001 - unknown tree state is not clean
        dirty = True

    return Context(
        repo=repo,
        python=args.python or sys.executable or "python3",
        which=which,
        npm_version=npm_version,
        node_version=node_version,
        dirty=dirty,
        require_clean=args.require_clean,
        work_dir=Path(args.work_dir).resolve() if args.work_dir else None,
        tag=args.tag,
        verbose=args.verbose,
    )


def _print_catalog() -> None:
    rows = job_catalog()
    width = max(len(row["name"]) for row in rows)
    print("local CI jobs (hosted CI is the release authority; this is local):")
    for row in rows:
        flag = "opt-in" if row["opt_in"] else "default"
        print(f"  {row['name']:<{width}}  {flag:<7}  {row['description']}")
    print("\ngroups:")
    for name, members in GROUPS.items():
        print(f"  {name:<8}  {', '.join(members)}")
    print("\nopt-in jobs also require --allow-opt-in.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Deterministic local reproduction of the actionable GitHub-hosted "
            "CI jobs (never publishes, never controls services)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--job", "-j", action="append", default=None,
                        metavar="NAME",
                        help="job or group name; repeatable/comma-separated "
                             f"({', '.join(JOB_ORDER)}; groups: "
                             f"{', '.join(GROUPS)}). Defaults to the "
                             "non-opt-in hosted jobs.")
    parser.add_argument("--list", action="store_true",
                        help="print the job catalog and exit")
    parser.add_argument("--json", action="store_true",
                        help="print the machine-readable report")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and run nothing")
    parser.add_argument("--allow-opt-in", action="store_true",
                        help="permit release-qualification / install-e2e")
    parser.add_argument("--require-clean", action="store_true",
                        help="BLOCK compile jobs on a dirty worktree")
    parser.add_argument("--repo", default=str(REPO),
                        help="checkout to operate on (default: this repo)")
    parser.add_argument("--python", default=None,
                        help="interpreter for Python steps (default: current)")
    parser.add_argument("--work-dir", default=None,
                        help="artifact/report directory for opt-in jobs")
    parser.add_argument("--tag", default=None,
                        help="tag passed to release qualification (never "
                             "creates or moves a tag)")
    parser.add_argument("--report", default=None,
                        help="write the JSON report to this path")
    parser.add_argument("--verbose", action="store_true",
                        help="print the full step output")
    args = parser.parse_args(argv)

    if args.list:
        if args.json:
            print(json.dumps(job_catalog(), indent=2, sort_keys=True))
        else:
            _print_catalog()
        return EXIT_PASS

    context = _detect_context(args)
    requested = args.job if args.job else list(DEFAULT_JOBS)
    selected, unknown = expand_jobs(requested)

    plans: list[JobPlan] = []
    selection_errors: list[str] = []
    for name in selected:
        plan = BUILDERS[name](context)
        if plan.opt_in and not args.allow_opt_in:
            plans.append(JobPlan(
                name=plan.name,
                description=plan.description,
                opt_in=True,
                steps=(),
                blocked=(
                    f"{plan.name} is opt-in; re-run with --allow-opt-in "
                    "to execute it (it may build/install/use the network)"
                ),
                notes=plan.notes,
            ))
        else:
            plans.append(plan)
    for name in unknown:
        selection_errors.append(f"unknown job or group: {name}")

    if context.work_dir is None:
        opt_in_selected = any(name in BUILDERS and BUILDERS[name](
            context).opt_in for name in selected)
        if opt_in_selected and args.allow_opt_in and not args.dry_run:
            context.work_dir = Path(
                tempfile.mkdtemp(prefix="qubit-local-ci-"))
            # Rebuild plans now that a real artifact dir exists.
            plans = []
            for name in selected:
                plan = BUILDERS[name](context)
                if plan.opt_in and not args.allow_opt_in:
                    plan = JobPlan(
                        name=plan.name,
                        description=plan.description,
                        opt_in=True,
                        steps=(),
                        blocked=(f"{plan.name} is opt-in; re-run with "
                                 "--allow-opt-in to execute it (it may "
                                 "build/install/use the network)"),
                        notes=plan.notes,
                    )
                plans.append(plan)

    runner = _subprocess_runner()
    results: list[JobResult] = []
    quiet = args.json
    for plan in plans:
        if not quiet:
            print(f"== {plan.name} ==")
        job_result = run_job(plan, runner=runner, dry_run=args.dry_run,
                             verbose=args.verbose)
        results.append(job_result)
        if not quiet:
            for step in job_result.steps:
                _print_step(step, args.verbose)
            for note in job_result.notes:
                print(f"   note: {note}")
            print(f"   -> {job_result.state.upper()}"
                  + (f" ({job_result.detail})" if job_result.detail else ""))

    report = build_report(
        repo=context.repo,
        jobs=results,
        selected=selected,
        unknown=unknown,
        dry_run=args.dry_run,
        context=context,
    )
    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))

    if selection_errors:
        for error in selection_errors:
            print(f"BLOCKED: {error}", file=sys.stderr)
    if not quiet:
        print(f"local-ci verdict: {report['verdict']} "
              f"(local execution; not GitHub-hosted)")

    return {
        "PASS": EXIT_PASS,
        "PLANNED": EXIT_PASS,
        "FAIL": EXIT_FAIL,
        "DEGRADED": EXIT_DEGRADED,
        "BLOCKED": EXIT_BLOCKED,
    }[report["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
