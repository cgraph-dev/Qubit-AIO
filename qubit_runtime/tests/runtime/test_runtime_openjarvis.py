"""OpenJarvis backend: pinned CLI, refusal paths, cancellation, CLI wiring."""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
import subprocess
import threading
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from agent.runtime_openjarvis import (
    PINNED_COMMIT,
    OpenJarvisRuntime,
    ResolvedOpenJarvis,
)
from agent.runtime_port import RuntimeRefusalError, TurnRequest
from agent.runtime_registry import RuntimeRegistry, build_registry
from qubit_cli.runtime_cmd import cmd_runtime

_FAKE_CLI = '''#!/usr/bin/env python3
import json
import os
import sys
import time

argv = sys.argv[1:]
if not argv or argv[0] != "ask":
    print("usage: jarvis ask", file=sys.stderr)
    sys.exit(2)
if argv[-2] != "--":
    print("prompt must follow --", file=sys.stderr)
    sys.exit(2)
prompt = argv[-1]
if "--json" not in argv:
    print("missing --json", file=sys.stderr)
    sys.exit(2)
if "--no-stream" not in argv or "--no-context" not in argv:
    print("Error: no such option: --no-stream", file=sys.stderr)
    sys.exit(2)
home = os.environ.get("OPENJARVIS_HOME", "")
if home:
    try:
        with open(os.path.join(home, "child.pid"), "w") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass
if "SLEEP" in prompt:
    time.sleep(60)
if "FAIL" in prompt:
    print("boom failure", file=sys.stderr)
    sys.exit(1)
if "LEAK" in prompt:
    print("provider key " + os.environ.get("QUBIT_TEST_API_KEY", ""), file=sys.stderr)
    sys.exit(1)
if "MALFORMED" in prompt:
    print("not json at all")
    sys.exit(0)
if "AGENT" in prompt:
    print(json.dumps({
        "content": "agent answer",
        "turns": 2,
        "tool_results": [{"tool_name": "calculator", "content": "4", "success": True}],
    }))
    sys.exit(0)
print(json.dumps({
    "content": "OJ_OK",
    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    "model": "fake-model",
    "finish_reason": "stop",
}))
'''


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


def _repo_cli(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = tmp_path / "ojrepo"
    repo.mkdir()
    script = repo / "jarvis"
    script.write_text(_FAKE_CLI, encoding="utf-8")
    script.chmod(0o755)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test Pin")
    _git(repo, "config", "user.email", "pin@example.invalid")
    _git(repo, "add", "jarvis")
    _git(repo, "commit", "-q", "-m", "pin")
    return script, repo, _git(repo, "rev-parse", "HEAD")


def _backend(tmp_path: Path, **kwargs) -> OpenJarvisRuntime:
    kwargs.setdefault("home", tmp_path / "ojhome")
    return OpenJarvisRuntime(**kwargs)


def _request(prompt: str = "hello") -> TurnRequest:
    return TurnRequest(prompt=prompt, session_id="s-oj")


def _run(backend: OpenJarvisRuntime, prompt: str = "hello", cancel=None):
    return list(backend.run_turn(_request(prompt), cancel))


@pytest.fixture
def pinned(tmp_path):
    script, repo, commit = _repo_cli(tmp_path)
    return script, repo, commit


def test_success_maps_delta_usage_and_one_done(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    events = _run(backend)

    assert [e.kind for e in events] == ["delta", "usage", "done"]
    assert events[0].text == "OJ_OK"
    assert events[1].data == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    assert events[-1].text == "OJ_OK"
    assert events[-1].data["completed"] is True
    assert events[-1].data["engine"] == "openjarvis"
    assert events[-1].data["commit"] == commit
    assert backend.capabilities.streaming is False
    assert backend.capabilities.cancel is True


def test_agent_mode_maps_tool_events(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    events = _run(backend, "AGENT")

    assert [e.kind for e in events] == ["delta", "tool", "done"]
    assert events[1].text == "calculator"
    assert events[1].data == {"tool": "calculator", "success": True}
    assert events[-1].data["turns"] == 2
    assert events[-1].data["tool_calls"] == 1


def test_history_and_system_are_rendered_into_the_single_prompt(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)
    request = TurnRequest(
        prompt="hello",
        session_id="s-oj",
        system="be terse",
        history=({"role": "user", "content": "earlier"},),
    )

    events = list(backend.run_turn(request))

    assert events[-1].kind == "done"
    prompt = backend._argv(backend.resolve(), backend._render_prompt(request))[-1]
    assert prompt.startswith("System:\nbe terse")
    assert "user: earlier" in prompt
    assert prompt.endswith("hello")


def test_prompt_starting_with_a_dash_is_not_parsed_as_a_flag(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    events = _run(backend, "-hello")

    assert events[-1].kind == "done"


def test_missing_binary_is_a_typed_refusal(tmp_path):
    backend = _backend(tmp_path, binary=str(tmp_path / "nope"), source=str(tmp_path))

    events = _run(backend)

    assert [e.kind for e in events] == ["error"]
    assert events[0].data["refusal"] == "openjarvis_unavailable"
    assert "QUBIT_OPENJARVIS_BIN" in events[0].data["hint"]


def test_commit_mismatch_refuses_to_run(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(
        tmp_path, binary=str(script), source=str(repo), pinned_commit="0" * 40)

    with pytest.raises(RuntimeRefusalError) as exc:
        backend.resolve()
    assert exc.value.code == "openjarvis_commit_mismatch"
    assert commit[:12] in exc.value.message

    events = _run(backend)
    assert [e.kind for e in events] == ["error"]
    assert events[0].data["refusal"] == "openjarvis_commit_mismatch"


def test_unverifiable_checkout_refuses_instead_of_guessing(tmp_path):
    script, _, _ = _repo_cli(tmp_path)
    loose = tmp_path / "loose"
    loose.mkdir()
    backend = _backend(tmp_path, binary=str(script), source=str(loose))

    with pytest.raises(RuntimeRefusalError) as exc:
        backend.resolve()
    assert exc.value.code == "openjarvis_commit_unverifiable"
    assert "QUBIT_OPENJARVIS_SOURCE" in exc.value.hint


def test_cancel_terminates_the_child_and_emits_one_error(tmp_path, pinned):
    script, repo, commit = pinned
    home = tmp_path / "ojhome"
    backend = _backend(
        tmp_path, binary=str(script), source=str(repo), pinned_commit=commit, home=home)
    cancel = threading.Event()
    timer = threading.Timer(0.5, cancel.set)
    timer.start()
    try:
        events = _run(backend, "SLEEP", cancel=cancel)
    finally:
        timer.cancel()

    assert [e.kind for e in events] == ["error"]
    assert events[0].data["cancelled"] is True
    assert events[0].data["interrupted"] is True
    child_pid = int((home / "child.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_timeout_is_bounded_and_reaps_the_child(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(
        tmp_path, binary=str(script), source=str(repo), pinned_commit=commit,
        timeout_seconds=1)

    events = _run(backend, "SLEEP")

    assert [e.kind for e in events] == ["error"]
    assert events[0].data["timeout"] is True


def test_nonzero_exit_is_an_error_not_a_done(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    events = _run(backend, "FAIL")

    assert [e.kind for e in events] == ["error"]
    assert events[0].data["exit_code"] == 1
    assert "boom failure" in events[0].text


def test_secret_values_are_redacted_from_stderr(tmp_path, pinned, monkeypatch):
    script, repo, commit = pinned
    monkeypatch.setenv("QUBIT_TEST_API_KEY", "sk-super-secret-value")
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    events = _run(backend, "LEAK")

    assert [e.kind for e in events] == ["error"]
    assert "sk-super-secret-value" not in events[0].text
    assert "***" in events[0].text


def test_malformed_json_is_an_error(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    events = _run(backend, "MALFORMED")

    assert [e.kind for e in events] == ["error"]
    assert "malformed" in events[0].text.lower()


def test_prompt_that_exceeds_the_cli_budget_is_refused(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)
    request = TurnRequest(prompt="x" * 60_001, session_id="s-oj")

    events = list(backend.run_turn(request))

    assert [e.kind for e in events] == ["error"]
    assert events[0].data["refusal"] == "openjarvis_prompt_too_large"


def test_registry_lists_openjarvis_but_keeps_native_first(tmp_path):
    registry = build_registry(lambda request: object())
    assert registry.available() == ["native", "openjarvis"]
    assert registry.get("native").name == "native"


def test_module_never_imports_openjarvis_in_process():
    source = Path(__file__).resolve().parents[2] / "agent" / "runtime_openjarvis.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert "openjarvis" not in imported


def _cli_args(**kw):
    base = dict(
        runtime_action="run", engine="openjarvis", prompt="hello", query_file=None, json=True)
    base.update(kw)
    return argparse.Namespace(**base)


def test_cli_runs_openjarvis_and_reports_json(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)
    registry = RuntimeRegistry()
    registry.register(backend)
    out = io.StringIO()

    with redirect_stdout(out):
        code = cmd_runtime(_cli_args(), registry=registry)

    assert code == 0
    payload = json.loads(out.getvalue())
    assert payload["engine"] == "openjarvis"
    assert payload["events"][-1]["kind"] == "done"
    assert payload["events"][-1]["text"] == "OJ_OK"


def test_cli_exits_two_and_prints_the_hint_on_refusal(tmp_path, capsys):
    backend = _backend(tmp_path, binary=str(tmp_path / "missing"), source=str(tmp_path))
    registry = RuntimeRegistry()
    registry.register(backend)

    with redirect_stdout(io.StringIO()):
        code = cmd_runtime(_cli_args(json=False), registry=registry)

    assert code == 2
    assert "QUBIT_OPENJARVIS_BIN" in capsys.readouterr().err


def test_pinned_constant_matches_the_recorded_provenance():
    assert PINNED_COMMIT == "05839014127772bd077ba9ba2a2393a007f63ac6"
    assert isinstance(ResolvedOpenJarvis("jarvis", "/src", PINNED_COMMIT), ResolvedOpenJarvis)
