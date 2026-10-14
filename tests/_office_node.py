"""Running a JavaScript program under `node` without an argv size limit.

WHY THIS EXISTS
---------------
The office tests drive the built renderer by handing a JavaScript program to
`node -e <program>`. That works until the program is large: the Office game
bundle is ~7 MB, and a program containing it exceeds the OS argument limit —

    OSError: [Errno 7] Argument list too long

Two suites hit this as soon as the game entry was promoted. The failure looks
like a broken renderer ("node exited 1"), so the real cause — a harness limit
nobody declared — is easy to misread.

`node` with NO `-e` reads its program from stdin, which has no such limit. Use
`run_node()` everywhere a program may contain a bundle, and the limit is gone
for good rather than waiting for the next growth spurt to rediscover it.
"""

from __future__ import annotations

import shutil
import subprocess


def node_binary() -> str | None:
    return shutil.which("node")


def run_node(
    program: str,
    *,
    timeout: int = 120,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run `program` under node, fed through stdin.

    Returns the CompletedProcess so a caller can keep its own assertions about
    the exit code and message. Raises RuntimeError when node is missing — a
    harness that cannot run must not be mistaken for a passing test.
    """
    node = node_binary()
    if not node:
        raise RuntimeError("node is not on PATH; cannot drive the renderer")
    return subprocess.run(
        [node], input=program, capture_output=True, text=True,
        timeout=timeout, cwd=cwd, env=env,
    )
