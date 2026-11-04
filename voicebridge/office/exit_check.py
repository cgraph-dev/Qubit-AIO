"""Validated, shell-free execution for an Office task's ``exit_check``.

The exit condition is a completion proof, not an arbitrary command runner. A
task can be proposed by an agent and approved by another agent, so a raw shell
string would let a prompt-injected seat execute commands merely by getting a
proposal through the chain. The supported grammar is deliberately small and
runs with ``shell=False``: ``true``/``false``, ``test``/``[``, read-only
``grep``, and a read-only ``sqlite3`` query optionally piped through ``grep``.
Anything else is refused when the task is created and again when it runs.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
from typing import List, Optional, Sequence

EXIT_CHECK_UNSAFE_CODE = "OFFICE_EXIT_CHECK_UNSAFE"
EXIT_CHECK_TIMEOUT_S = 120

_ALLOWED = {"true", "false", "test", "[", "grep", "egrep", "fgrep", "sqlite3"}
_GREP = {"grep", "egrep", "fgrep"}
_SHELL_META = re.compile(r"(;|&&|\|\||`|\$\(|[<>]|&)")
_SQL_DANGER = re.compile(
    r"(?i)\b(attach|detach|load_extension|writable_schema|pragma|vacuum)\b|"
    r"\.(shell|system|import|open|load|output|once|read|restore|save|clone|backup)\b")
_SQLITE_OPTS = {"-readonly", "-safe"}


def _tokens(command: str) -> List[str]:
    lexer = shlex.shlex(str(command or ""), posix=True, punctuation_chars="|")
    lexer.whitespace_split = True
    return list(lexer)


def _stages(tokens: Sequence[str]) -> List[List[str]]:
    stages: List[List[str]] = [[]]
    for token in tokens:
        if token == "|":
            stages.append([])
        else:
            stages[-1].append(token)
    return stages


def _binary(argv: Sequence[str]) -> str:
    if not argv:
        return ""
    raw = str(argv[0])
    if "/" in raw or "\\" in raw:
        return ""
    return os.path.basename(raw)


def _validate_test(argv: Sequence[str]) -> Optional[str]:
    if _binary(argv) == "[" and argv[-1] != "]":
        return "'[' checks must end with ']'"
    return None


def _validate_grep(argv: Sequence[str]) -> Optional[str]:
    return None if len(argv) >= 2 else "grep needs a pattern"


def _validate_sqlite(argv: Sequence[str]) -> Optional[str]:
    args = list(argv[1:])
    opts: set[str] = set()
    while args and args[0].startswith("-"):
        option = args.pop(0)
        if option not in _SQLITE_OPTS:
            return f"sqlite3 option {option!r} is not allowed"
        opts.add(option)
    if "-readonly" not in opts:
        return "sqlite3 must run with -readonly"
    if len(args) < 2:
        return "sqlite3 needs a database path and a SELECT statement"
    for part in args[1:]:
        text = str(part).strip()
        if not re.match(r"(?is)^select\b", text):
            return "only SELECT statements are allowed"
        if _SQL_DANGER.search(text):
            return "the SELECT uses a forbidden SQLite feature"
        if ";" in text.rstrip().rstrip(";"):
            return "only one SELECT statement is allowed"
    return None


def validate_exit_check(command: str) -> Optional[str]:
    """Return a refusal reason, or None when the command is in the safe grammar."""
    text = str(command or "").strip()
    if not text:
        return "an exit check is required"
    if _SHELL_META.search(text.replace("||", "")) or "||" in text:
        return "shell operators are not allowed in an exit check"
    try:
        tokens = _tokens(text)
    except ValueError as exc:
        return f"the exit check cannot be parsed: {exc}"
    stages = _stages(tokens)
    if any(not stage for stage in stages):
        return "empty pipeline stage"
    if len(stages) > 2:
        return "at most one pipeline is allowed"
    for index, stage in enumerate(stages):
        name = _binary(stage)
        if not name or name not in _ALLOWED:
            return f"command {stage[0]!r} is not allowed"
        if name in ("true", "false"):
            if len(stage) != 1:
                return f"{name} takes no arguments"
        elif name in ("test", "["):
            reason = _validate_test(stage)
            if reason:
                return reason
        elif name in _GREP:
            reason = _validate_grep(stage)
            if reason:
                return reason
        elif name == "sqlite3":
            reason = _validate_sqlite(stage)
            if reason:
                return reason
        else:
            return f"command {name!r} is not allowed"
        if index and _binary(stages[0]) != "sqlite3":
            return "a pipeline may only follow a read-only sqlite3 check"
    return None


def run_exit_check(command: str, *, cwd: Optional[str] = None,
                   timeout: int = EXIT_CHECK_TIMEOUT_S) -> bool:
    if validate_exit_check(command):
        return False
    try:
        stages = _stages(_tokens(str(command).strip()))
        first = subprocess.run(stages[0], shell=False, cwd=cwd, timeout=timeout,
                               capture_output=True, text=True)
        if len(stages) == 1:
            return first.returncode == 0
        second = subprocess.run(stages[1], shell=False, cwd=cwd, timeout=timeout,
                                input=first.stdout, capture_output=True, text=True)
        return second.returncode == 0
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return False
