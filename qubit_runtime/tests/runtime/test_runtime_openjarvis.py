"""OpenJarvis backend: pinned CLI, refusal paths, cancellation, CLI wiring."""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
import subprocess
import threading
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from agent.runtime_openjarvis import PINNED_COMMIT, OpenJarvisRuntime
from agent.runtime_port import RuntimeRefusalError, TurnEvent, TurnRequest
from agent.runtime_registry import RuntimeRegistry, build_registry
from qubit_cli.runtime_cmd import cmd_runtime

_FAKE_CLI = '''#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time

argv = sys.argv[1:]
if argv and argv[0] == "--version":
    if os.environ.get("FAKE_OJ_VERSION_FAIL"):
        print("version probe failed", file=sys.stderr)
        sys.exit(1)
    if os.environ.get("FAKE_OJ_VERSION_NOSTAMP"):
        print("jarvis, version 1.2.3")
        sys.exit(0)
    if os.environ.get("FAKE_OJ_VERSION_DESCENDANT"):
        marker = os.environ["FAKE_OJ_DESC_PID"]
        subprocess.Popen([
            sys.executable, "-c",
            "import os,signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(60)", marker,
        ])
        for _ in range(500):
            if os.path.exists(marker):
                break
            time.sleep(0.01)
    print("jarvis, version fake.g" + os.environ.get("FAKE_OJ_PIN", "")[:9])
    sys.exit(0)
if not argv or argv[0] != "ask":
    print("usage: jarvis ask", file=sys.stderr)
    sys.exit(2)
if argv[-2] != "--":
    print("prompt must follow --", file=sys.stderr)
    sys.exit(2)
prompt = argv[-1]
if "--json" not in argv or "--no-stream" not in argv or "--no-context" not in argv:
    print("Error: no such option: --no-stream", file=sys.stderr)
    sys.exit(2)
if "--agent" not in argv:
    print("Error: no such option: --agent", file=sys.stderr)
    sys.exit(2)
agent = argv[argv.index("--agent") + 1]
reject = os.environ.get("FAKE_OJ_REJECT_AGENT")
if reject and agent == reject:
    print("Error: unknown agent: " + agent, file=sys.stderr)
    sys.exit(2)
home = os.environ.get("OPENJARVIS_HOME", "")
if home:
    try:
        with open(os.path.join(home, "child.pid"), "w") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass
if "DESCENDANT" in prompt:
    marker = os.path.join(home, "descendant.pid")
    subprocess.Popen([
        sys.executable, "-c",
        "import os,signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(60)", marker,
    ])
    time.sleep(60)
if "SLEEP" in prompt:
    time.sleep(60)
if "FAIL" in prompt:
    print("boom failure", file=sys.stderr)
    sys.exit(1)
if "LEAKLONG" in prompt:
    print("x" * 1990 + os.environ.get("QUBIT_TEST_API_KEY", ""), file=sys.stderr)
    sys.exit(1)
if "LEAK" in prompt:
    print("provider key " + os.environ.get("QUBIT_TEST_API_KEY", ""), file=sys.stderr)
    sys.exit(1)
if "HUGE" in prompt:
    sys.stdout.write("x" * 3_000_000)
    sys.stdout.flush()
    sys.exit(0)
if "MALFORMED" in prompt:
    print("not json at all")
    sys.exit(0)
if "EMPTY_STDOUT" in prompt:
    sys.exit(0)
if "INCOMPLETE" in prompt:
    print(json.dumps({"completed": False, "content": "partial"}))
    sys.exit(0)
if "ERROR_WITH_CONTENT" in prompt:
    print(json.dumps({"error": "partial failure", "content": "partial"}))
    sys.exit(0)
if "LINGER" in prompt:
    marker = os.path.join(home, "descendant.pid")
    subprocess.Popen([
        sys.executable, "-c",
        "import os,signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(60)", marker,
    ])
    for _ in range(500):
        if os.path.exists(marker):
            break
        time.sleep(0.01)
    print(json.dumps({"content": "child done"}))
    sys.exit(0)
if "ECHO_PROMPT" in prompt:
    print(json.dumps({"content": prompt}))
    sys.exit(0)
if "ECHO_ARGV" in prompt:
    print(json.dumps({"content": " ".join(argv)}))
    sys.exit(0)
if agent:
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


def _assert_process_gone(pid_file: Path, timeout: float = 5.0) -> None:
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    raise AssertionError(f"process {pid} survived")


@pytest.fixture
def pinned(tmp_path, monkeypatch):
    script, repo, commit = _repo_cli(tmp_path)
    monkeypatch.setenv("FAKE_OJ_PIN", commit)
    return script, repo, commit


# --------------------------------------------------------------------- happy path


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
    assert events[-1].data["binary"] == str(script.resolve())
    assert backend.capabilities.streaming is False
    assert backend.capabilities.cancel is True


def test_incomplete_payload_is_an_error(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    events = _run(backend, "INCOMPLETE")

    assert [e.kind for e in events] == ["error"]


def test_agent_mode_maps_tool_events(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(
        tmp_path, binary=str(script), source=str(repo), pinned_commit=commit, agent="simple")

    events = _run(backend)

    assert [e.kind for e in events] == ["delta", "tool", "done"]
    assert events[1].text == "calculator"
    assert events[1].data == {"tool": "calculator", "success": True}
    assert events[-1].data["turns"] == 2
    assert events[-1].data["tool_calls"] == 1


def test_default_direct_mode_is_passed_as_an_empty_agent(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    argv = backend._argv(backend.resolve(), "hi")
    assert argv[argv.index("--agent") + 1] == ""
    events = _run(backend)
    assert events[-1].text == "OJ_OK"
    assert "turns" not in events[-1].data


def test_configured_model_and_tools_reach_the_cli(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(
        tmp_path, binary=str(script), source=str(repo), pinned_commit=commit,
        model="fake-model-7", tools="calculator,think")

    events = _run(backend, "ECHO_ARGV")

    argv = events[-1].text
    assert "-m fake-model-7" in argv
    assert "--tools calculator,think" in argv
    assert "--no-context" in argv
    assert events[-1].data["model"] == "fake-model-7"


def test_history_and_system_reach_the_spawned_prompt(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)
    request = TurnRequest(
        prompt="ECHO_PROMPT",
        session_id="s-oj",
        system="be terse",
        history=({"role": "user", "content": "earlier"},),
    )

    events = list(backend.run_turn(request))

    merged = events[-1].text
    assert merged.startswith("System:\nbe terse")
    assert "user: earlier" in merged
    assert merged.endswith("ECHO_PROMPT")


def test_prompt_starting_with_a_dash_is_not_parsed_as_a_flag(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    events = _run(backend, "-hello")

    assert events[-1].kind == "done"


# ----------------------------------------------------------------------- refusals


def test_missing_binary_is_a_typed_refusal(tmp_path):
    backend = _backend(tmp_path, binary=str(tmp_path / "nope"), source=str(tmp_path))

    events = _run(backend)

    assert [e.kind for e in events] == ["error"]
    assert events[0].data["refusal"] == "openjarvis_unavailable"
    assert "QUBIT_OPENJARVIS_BIN" in events[0].data["hint"]


def test_binary_build_stamp_must_match_the_pin(tmp_path, pinned, monkeypatch):
    script, repo, commit = pinned
    monkeypatch.setenv("FAKE_OJ_PIN", "0" * 40)
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    with pytest.raises(RuntimeRefusalError) as exc:
        backend.resolve()
    assert exc.value.code == "openjarvis_binary_mismatch"


def test_binary_build_stamp_matching_the_pin_is_accepted(tmp_path, pinned, monkeypatch):
    script, repo, commit = pinned
    monkeypatch.setenv("FAKE_OJ_PIN", commit)
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    assert backend.resolve().commit == commit


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


def test_dirty_checkout_refuses_to_run(tmp_path, pinned):
    script, repo, commit = pinned
    script.write_text(_FAKE_CLI + "\n# uncommitted edit\n", encoding="utf-8")
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    with pytest.raises(RuntimeRefusalError) as exc:
        backend.resolve()
    assert exc.value.code == "openjarvis_checkout_dirty"


def test_unverifiable_checkout_refuses_instead_of_guessing(tmp_path):
    script, _, _ = _repo_cli(tmp_path)
    loose = tmp_path / "loose"
    loose.mkdir()
    backend = _backend(tmp_path, binary=str(script), source=str(loose))

    with pytest.raises(RuntimeRefusalError) as exc:
        backend.resolve()
    assert exc.value.code == "openjarvis_commit_unverifiable"
    assert "QUBIT_OPENJARVIS_SOURCE" in exc.value.hint


def test_relative_binary_is_resolved_absolutely(tmp_path, pinned, monkeypatch):
    script, repo, commit = pinned
    monkeypatch.chdir(tmp_path)
    backend = _backend(tmp_path, binary="ojrepo/jarvis", source=str(repo), pinned_commit=commit)

    events = _run(backend)

    assert events[-1].kind == "done"
    assert events[-1].data["binary"] == str(script.resolve())
    assert Path(events[-1].data["binary"]).is_absolute()


def test_nul_prompt_is_a_typed_refusal(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    events = _run(backend, "a\x00b")

    assert [e.kind for e in events] == ["error"]
    assert events[0].data["refusal"] == "openjarvis_invalid_prompt"


def test_prompt_byte_budget_is_refused_before_spawn(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    events = _run(backend, "😀" * 31_000)  # ~124 KB of UTF-8

    assert [e.kind for e in events] == ["error"]
    assert events[0].data["refusal"] == "openjarvis_prompt_too_large"


def test_unwritable_home_is_an_error_event_not_a_traceback(tmp_path, pinned):
    script, repo, commit = pinned
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    backend = _backend(
        tmp_path, binary=str(script), source=str(repo), pinned_commit=commit,
        home=blocker / "home")

    events = _run(backend)

    assert [e.kind for e in events] == ["error"]
    assert events[0].data["refusal"] == "openjarvis_home_unavailable"


# ---------------------------------------------------------------- lifecycle/cancel


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
    _assert_process_gone(home / "child.pid")


def test_cancel_kills_a_sigterm_ignoring_descendant(tmp_path, pinned):
    script, repo, commit = pinned
    home = tmp_path / "ojhome"
    backend = _backend(
        tmp_path, binary=str(script), source=str(repo), pinned_commit=commit, home=home)
    cancel = threading.Event()
    holder: dict[str, object] = {}

    def run() -> None:
        holder["events"] = _run(backend, "DESCENDANT", cancel=cancel)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    deadline = time.monotonic() + 10
    while not (home / "descendant.pid").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert (home / "descendant.pid").exists(), "descendant never started"
    cancel.set()
    worker.join(timeout=20)
    assert not worker.is_alive()

    events = holder["events"]
    assert [e.kind for e in events] == ["error"]
    _assert_process_gone(home / "child.pid")
    _assert_process_gone(home / "descendant.pid")


def test_timeout_is_bounded_and_reaps_the_child(tmp_path, pinned):
    script, repo, commit = pinned
    home = tmp_path / "ojhome"
    backend = _backend(
        tmp_path, binary=str(script), source=str(repo), pinned_commit=commit, home=home,
        timeout_seconds=1)

    started = time.monotonic()
    events = _run(backend, "SLEEP")

    assert [e.kind for e in events] == ["error"]
    assert events[0].data["timeout"] is True
    assert time.monotonic() - started < 10
    _assert_process_gone(home / "child.pid")


def test_keyboard_interrupt_reaps_the_child_before_propagating(tmp_path, pinned, monkeypatch):
    script, repo, commit = pinned
    home = tmp_path / "ojhome"
    backend = _backend(
        tmp_path, binary=str(script), source=str(repo), pinned_commit=commit, home=home)

    def _boom(proc, pgid, cancel, state):
        time.sleep(0.3)
        raise KeyboardInterrupt

    monkeypatch.setattr(backend, "_communicate", _boom)
    with pytest.raises(KeyboardInterrupt):
        list(backend.run_turn(_request("SLEEP")))

    _assert_process_gone(home / "child.pid")


def test_runaway_output_is_bounded_and_the_child_reaped(tmp_path, pinned):
    script, repo, commit = pinned
    home = tmp_path / "ojhome"
    backend = _backend(
        tmp_path, binary=str(script), source=str(repo), pinned_commit=commit, home=home)

    events = _run(backend, "HUGE")

    assert [e.kind for e in events] == ["error"]
    assert events[0].data["output_overflow"] is True
    _assert_process_gone(home / "child.pid")


# ------------------------------------------------------------------ error surfacing


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


def test_redaction_happens_before_truncation(tmp_path, pinned, monkeypatch):
    script, repo, commit = pinned
    monkeypatch.setenv("QUBIT_TEST_API_KEY", "sk-super-secret-value")
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    events = _run(backend, "LEAKLONG")

    assert "sk-super" not in events[0].text
    assert "sk-su" not in events[0].text


def test_error_payload_with_content_is_still_an_error(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    events = _run(backend, "ERROR_WITH_CONTENT")

    assert [e.kind for e in events] == ["delta", "error"]
    assert "partial failure" in events[-1].text


def test_malformed_json_is_an_error(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    events = _run(backend, "MALFORMED")

    assert [e.kind for e in events] == ["error"]
    assert "malformed" in events[0].text.lower()


def test_empty_success_output_is_malformed(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    events = _run(backend, "EMPTY_STDOUT")

    assert [e.kind for e in events] == ["error"]
    assert "malformed" in events[0].text.lower()


def test_unknown_agent_is_a_nonzero_error_not_a_done(tmp_path, pinned, monkeypatch):
    script, repo, commit = pinned
    monkeypatch.setenv("FAKE_OJ_REJECT_AGENT", "nope")
    backend = _backend(
        tmp_path, binary=str(script), source=str(repo), pinned_commit=commit, agent="nope")

    events = _run(backend)

    assert [e.kind for e in events] == ["error"]
    assert events[0].data["exit_code"] == 2
    assert "unknown agent" in events[0].text


def test_binary_removed_after_resolve_is_a_typed_unavailable(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)
    backend.resolve()
    script.unlink()

    events = _run(backend)

    assert [e.kind for e in events] == ["error"]
    assert events[0].data["refusal"] == "openjarvis_unavailable"


def test_dirty_checkout_after_resolve_is_revalidated_before_the_turn(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)
    backend.resolve()
    script.write_text(_FAKE_CLI + "\n# edited after resolve\n", encoding="utf-8")

    events = _run(backend)

    assert [e.kind for e in events] == ["error"]
    assert events[0].data["refusal"] == "openjarvis_checkout_dirty"


def test_new_commit_after_resolve_is_revalidated_before_the_turn(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)
    backend.resolve()
    (repo / "after-resolve.txt").write_text("moved\n", encoding="utf-8")
    _git(repo, "add", "after-resolve.txt")
    _git(repo, "commit", "-q", "-m", "move the pin")

    events = _run(backend)

    assert [e.kind for e in events] == ["error"]
    assert events[0].data["refusal"] == "openjarvis_commit_mismatch"


def test_binary_stamp_swap_after_resolve_is_revalidated_before_the_turn(
        tmp_path, pinned, monkeypatch):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)
    backend.resolve()
    monkeypatch.setenv("FAKE_OJ_PIN", "0" * 40)

    events = _run(backend)

    assert [e.kind for e in events] == ["error"]
    assert events[0].data["refusal"] == "openjarvis_binary_mismatch"


def test_direct_resolve_reuses_a_short_cache_and_force_reprobes(tmp_path, pinned, monkeypatch):
    script, repo, commit = pinned
    backend = _backend(
        tmp_path, binary=str(script), source=str(repo), pinned_commit=commit,
        resolve_cache_seconds=30.0)
    calls = {"n": 0}
    real = backend._resolve_uncached

    def counted():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(backend, "_resolve_uncached", counted)
    backend.resolve()
    backend.resolve()
    assert calls["n"] == 1

    backend.resolve(force=True)
    assert calls["n"] == 2


def test_zero_cache_reprobes_on_every_direct_resolve(tmp_path, pinned, monkeypatch):
    script, repo, commit = pinned
    backend = _backend(
        tmp_path, binary=str(script), source=str(repo), pinned_commit=commit,
        resolve_cache_seconds=0)
    calls = {"n": 0}
    real = backend._resolve_uncached

    def counted():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(backend, "_resolve_uncached", counted)
    backend.resolve()
    backend.resolve()
    assert calls["n"] == 2


def test_resolve_ttl_expiry_reprobes_without_force(tmp_path, pinned, monkeypatch):
    script, repo, commit = pinned
    backend = _backend(
        tmp_path, binary=str(script), source=str(repo), pinned_commit=commit,
        resolve_cache_seconds=5.0)
    calls = {"n": 0}
    real = backend._resolve_uncached

    def counted():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(backend, "_resolve_uncached", counted)
    clock = {"now": 100.0}
    monkeypatch.setattr(
        "agent.runtime_openjarvis.time.monotonic", lambda: clock["now"])

    backend.resolve()
    assert calls["n"] == 1
    clock["now"] += 5.0
    backend.resolve()
    assert calls["n"] == 2


def test_json_with_trailing_status_text_is_parsed(tmp_path):
    assert OpenJarvisRuntime._parse('{"content": "ok"}\nstatus: done') == {"content": "ok"}


def test_surrogate_prompt_is_a_typed_refusal_not_a_traceback(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    events = _run(backend, "\udcff")

    assert [e.kind for e in events] == ["error"]
    assert events[0].data["refusal"] == "openjarvis_invalid_prompt"


def test_version_probe_failure_refuses(tmp_path, pinned, monkeypatch):
    script, repo, commit = pinned
    monkeypatch.setenv("FAKE_OJ_VERSION_FAIL", "1")
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    with pytest.raises(RuntimeRefusalError) as exc:
        backend.resolve()
    assert exc.value.code == "openjarvis_commit_unverifiable"


def test_version_probe_without_a_stamp_refuses(tmp_path, pinned, monkeypatch):
    script, repo, commit = pinned
    monkeypatch.setenv("FAKE_OJ_VERSION_NOSTAMP", "1")
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    with pytest.raises(RuntimeRefusalError) as exc:
        backend.resolve()
    assert exc.value.code == "openjarvis_commit_unverifiable"


def test_git_status_failure_is_fail_closed(tmp_path, pinned, monkeypatch):
    script, repo, commit = pinned
    real_git = OpenJarvisRuntime._git

    def fake_git(args, cwd):
        if args and args[0] == "status":
            return None
        return real_git(args, cwd)

    monkeypatch.setattr(OpenJarvisRuntime, "_git", staticmethod(fake_git))
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    with pytest.raises(RuntimeRefusalError) as exc:
        backend.resolve()
    assert exc.value.code == "openjarvis_commit_unverifiable"


def test_bare_binary_name_is_found_on_path(tmp_path, pinned, monkeypatch):
    script, repo, commit = pinned
    monkeypatch.setenv("PATH", str(repo) + os.pathsep + os.environ["PATH"])
    backend = _backend(tmp_path, binary="jarvis", source=str(repo), pinned_commit=commit)

    events = _run(backend)

    assert events[-1].kind == "done"


def test_sixty_thousand_character_cap_is_still_enforced(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    events = _run(backend, "x" * 60_001)

    assert [e.kind for e in events] == ["error"]
    assert events[0].data["refusal"] == "openjarvis_prompt_too_large"


def test_e2big_from_spawn_maps_to_prompt_too_large(tmp_path, pinned, monkeypatch):
    import errno

    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    import subprocess as _subprocess
    real_popen = _subprocess.Popen

    def _e2big(argv, *args, **kwargs):
        if "--no-context" not in argv:
            return real_popen(argv, *args, **kwargs)
        raise OSError(errno.E2BIG, "Argument list too long")

    monkeypatch.setattr("agent.runtime_openjarvis.subprocess.Popen", _e2big)
    events = _run(backend)

    assert [e.kind for e in events] == ["error"]
    assert events[0].data["refusal"] == "openjarvis_prompt_too_large"


def test_successful_child_with_a_lingering_descendant_completes_quickly(tmp_path, pinned):
    script, repo, commit = pinned
    home = tmp_path / "ojhome"
    backend = _backend(
        tmp_path, binary=str(script), source=str(repo), pinned_commit=commit, home=home,
        timeout_seconds=30)

    started = time.monotonic()
    events = _run(backend, "LINGER")

    assert time.monotonic() - started < 10
    assert events[-1].kind == "done"
    assert events[-1].text == "child done"
    _assert_process_gone(home / "descendant.pid")


def test_query_file_with_invalid_utf8_is_reported_not_a_traceback(tmp_path, pinned):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)
    registry = RuntimeRegistry()
    registry.register(backend)
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff\xfe")
    err = io.StringIO()

    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        code = cmd_runtime(
            _cli_args(prompt=None, query_file=str(bad), json=False), registry=registry)

    assert code == 2
    assert "cannot read" in err.getvalue()


def test_git_decode_failure_is_fail_closed(tmp_path, pinned, monkeypatch):
    script, repo, commit = pinned
    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if "status" in cmd:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr("agent.runtime_openjarvis.subprocess.run", fake_run)
    assert OpenJarvisRuntime._git(["status"], repo) is None
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)
    with pytest.raises(RuntimeRefusalError) as exc:
        backend.resolve()
    assert exc.value.code == "openjarvis_commit_unverifiable"


def test_symlink_loop_home_does_not_crash_registry(tmp_path, monkeypatch):
    a = tmp_path / "a"
    b = tmp_path / "b"
    os.symlink(b, a)
    os.symlink(a, b)
    monkeypatch.setenv("QUBIT_OPENJARVIS_HOME", str(a))

    registry = build_registry(lambda request: object())  # must not raise
    events = list(registry.get("openjarvis").run_turn(_request()))

    assert [e.kind for e in events] == ["error"]
    assert events[0].data["refusal"] == "openjarvis_home_unavailable"


def test_version_probe_kills_a_lingering_descendant(tmp_path, pinned, monkeypatch):
    script, repo, commit = pinned
    marker = tmp_path / "probe-descendant.pid"
    monkeypatch.setenv("FAKE_OJ_VERSION_DESCENDANT", "1")
    monkeypatch.setenv("FAKE_OJ_DESC_PID", str(marker))
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    assert backend.resolve().commit == commit
    _assert_process_gone(marker)


def test_wrapper_never_emits_a_second_terminal(tmp_path, pinned, monkeypatch):
    script, repo, commit = pinned
    backend = _backend(tmp_path, binary=str(script), source=str(repo), pinned_commit=commit)

    def fake_run_turn(request, cancel=None):
        yield TurnEvent("done", text="ok")
        raise RuntimeError("bug after the terminal event")

    monkeypatch.setattr(backend, "_run_turn", fake_run_turn)
    events = _run(backend)

    assert [e.kind for e in events] == ["done"]


# --------------------------------------------------------------------- CLI/registry


def test_registry_lists_all_engines_and_native_is_the_parser_default():
    registry = build_registry(lambda request: object())
    assert registry.available() == ["native", "openjarvis", "rust_local"]
    assert registry.get("native").name == "native"

    from qubit_cli.subcommands.runtime import build_runtime_parser

    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command")
    build_runtime_parser(subs, cmd_runtime=lambda args: 0)
    assert getattr(parser.parse_args(["runtime", "run", "--prompt", "x"]), "engine", "native") == "native"


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


def test_engines_marks_an_unavailable_backend_without_failing(tmp_path):
    backend = _backend(tmp_path, binary=str(tmp_path / "missing"), source=str(tmp_path))
    registry = RuntimeRegistry()
    registry.register(backend)
    out = io.StringIO()

    with redirect_stdout(out):
        assert cmd_runtime(argparse.Namespace(runtime_action="engines"), registry=registry) == 0

    assert "openjarvis (unavailable: openjarvis_unavailable)" in out.getvalue()


def test_cli_exits_two_and_prints_the_hint_on_refusal(tmp_path, capsys):
    backend = _backend(tmp_path, binary=str(tmp_path / "missing"), source=str(tmp_path))
    registry = RuntimeRegistry()
    registry.register(backend)

    with redirect_stdout(io.StringIO()):
        code = cmd_runtime(_cli_args(json=False), registry=registry)

    assert code == 2
    assert "QUBIT_OPENJARVIS_BIN" in capsys.readouterr().err


def test_cli_refusal_is_still_visible_in_json(tmp_path):
    backend = _backend(tmp_path, binary=str(tmp_path / "missing"), source=str(tmp_path))
    registry = RuntimeRegistry()
    registry.register(backend)
    out = io.StringIO()

    with redirect_stdout(out):
        code = cmd_runtime(_cli_args(), registry=registry)

    assert code == 2
    payload = json.loads(out.getvalue())
    assert payload["events"][-1]["data"]["refusal"] == "openjarvis_unavailable"


def test_cli_returns_130_on_keyboard_interrupt():
    class _Interrupting:
        name = "openjarvis"

        def run_turn(self, request, cancel=None):
            raise KeyboardInterrupt
            yield  # pragma: no cover - keeps this a generator

    registry = RuntimeRegistry()
    registry.register(_Interrupting())
    err = io.StringIO()

    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        code = cmd_runtime(_cli_args(json=False), registry=registry)

    assert code == 130
    assert "interrupted" in err.getvalue()


def test_pinned_constant_matches_the_recorded_provenance():
    assert PINNED_COMMIT == "05839014127772bd077ba9ba2a2393a007f63ac6"
