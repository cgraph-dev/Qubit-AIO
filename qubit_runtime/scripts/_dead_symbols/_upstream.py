"""Upstream tree at the pinned revision: discovery + one-process blob read."""
from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

from _consts import OUR_TO_UP, UPSTREAM_CANDIDATES


def _pin_file(script_dir: Path) -> Path:
    """`scripts/frozen_target_progress.py` — the pin's single declared home.

    It lives at the REPOSITORY's `scripts/`, three levels above this module
    (`<repo>/qubit_runtime/scripts/_dead_symbols/`), so that is tried first, then
    the two shallower layouts (package-local `scripts/`, and a `scripts/` beside
    this file) that a relocated or scratch copy can present.
    """
    for cand in (script_dir.parents[2] / "scripts" / "frozen_target_progress.py",
                 script_dir.parent / "scripts" / "frozen_target_progress.py",
                 script_dir / "frozen_target_progress.py"):
        if cand.is_file():
            return cand
    raise SystemExit(
        "cannot locate scripts/frozen_target_progress.py (the pin's single declared "
        f"home); looked in {script_dir.parent} and {script_dir}. "
        "Refusing to audit against an unknown revision -- pass --rev to override.")


def frozen_rev() -> str:
    """The frozen pin, from scripts/frozen_target_progress.py::FROZEN_REV.

    Parsed with the AST rather than a text prefix scan, so a docstring that
    merely MENTIONS FROZEN_REV cannot be mistaken for the assignment. Fails
    loudly -- it never returns "" for a missing pin, which downstream would read
    as "no upstream".
    """
    src = _pin_file(Path(__file__).resolve().parent)
    try:
        tree = ast.parse(src.read_text(), filename=str(src))
    except (OSError, SyntaxError) as exc:
        raise SystemExit(f"cannot read the frozen pin from {src}: {exc}")
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "FROZEN_REV":
                    value = getattr(node.value, "value", None)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
    raise SystemExit(f"FROZEN_REV is not a module-level string literal in {src}")


def find_upstream(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        if not (p / ".git").exists():
            raise SystemExit(f"--upstream {p} is not a git clone")
        return p
    for cand in UPSTREAM_CANDIDATES:
        if (cand / ".git").exists():
            return cand
    raise SystemExit(
        "cannot locate the upstream clone (tried "
        + ", ".join(str(c) for c in UPSTREAM_CANDIDATES)
        + "); refusing to run -- every symbol would look upstream-unused")


def route(module: str) -> str:
    """Our repo-relative module path -> the upstream path hosting the same code."""
    segs = module.split("/")
    out = []
    for seg in segs:
        if seg in OUR_TO_UP:
            out.append(OUR_TO_UP[seg])
        elif seg.startswith("qubit_"):
            out.append("hermes_" + seg[len("qubit_"):])
        elif seg.startswith("qubit"):
            out.append("hermes" + seg[len("qubit"):])
        else:
            out.append(seg)
    return "/".join(out)


def _git(root: Path, *args: str, timeout: int = 180) -> str:
    env = dict(os.environ, GIT_NO_LAZY_FETCH="1")
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                       text=True, env=env, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()[:300]}")
    return p.stdout


def read_pin(up: Path, rev: str) -> dict[str, str]:
    """{path: source} for every upstream .py at `rev`, through ONE cat-file process.

    `git show rev:path` per file and `ls-tree -r rev` per path both re-walk the
    tree; on this partial clone (promisor, filter=tree:0) that is slow and can
    reach for the network. Resolve paths to OIDs once, then stream OIDs.
    """
    try:
        _git(up, "cat-file", "-e", f"{rev}^{{commit}}")
    except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
        raise SystemExit(f"FATAL: pin {rev} is not readable in {up}: {exc}")
    tree = _git(up, "ls-tree", "-r", rev)
    oid_of: dict[str, str] = {}
    for line in tree.split("\n"):
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob" and path.endswith(".py"):
            oid_of[path] = parts[2]
    if not oid_of:
        raise SystemExit(f"FATAL: pin {rev} carries no .py files -- wrong clone?")

    env = dict(os.environ, GIT_NO_LAZY_FETCH="1")
    proc = subprocess.Popen(["git", "-C", str(up), "cat-file", "--batch"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=env)
    out: dict[str, str] = {}
    missing: list[str] = []
    try:
        assert proc.stdin is not None and proc.stdout is not None
        for path in sorted(oid_of):
            proc.stdin.write((oid_of[path] + "\n").encode())
            proc.stdin.flush()
            header = proc.stdout.readline().decode("utf-8", "replace").rstrip("\n")
            parts = header.rsplit(" ", 2)
            if len(parts) != 3 or not parts[2].isdigit():
                missing.append(path)      # uncached blob on a partial clone
                continue
            size = int(parts[2])
            data = proc.stdout.read(size)
            proc.stdout.read(1)
            out[path] = data.decode("utf-8", "replace")
    finally:
        for stream in (proc.stdin, proc.stdout):
            try:
                stream.close()  # type: ignore[union-attr]
            except Exception:
                pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    read_pin.last_missing = missing  # type: ignore[attr-defined]
    return out


read_pin.last_missing = []  # type: ignore[attr-defined]
