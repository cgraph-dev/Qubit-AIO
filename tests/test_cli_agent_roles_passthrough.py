"""`qubit agent roles|run` must reach the runtime parser intact.

The historical `qubit agent <args>` passthrough strips `agent` before calling
the vendored runtime (`qubit agent chat` -> runtime `chat`). The Qubit-native
role surface lives at `agent roles|run` inside that parser, so stripping the
token turned it into an invalid top-level `roles` command.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _capture(monkeypatch):
    from voicebridge import cli, qubit_runtime

    seen = []
    monkeypatch.setattr(qubit_runtime, "run",
                        lambda argv=None: (seen.append(list(argv or [])), 0)[1])
    return cli, seen


def test_agent_roles_and_run_keep_the_agent_token(monkeypatch):
    cli, seen = _capture(monkeypatch)
    assert cli.main(["agent", "roles", "--json"]) == 0
    assert cli.main(["agent", "run", "simple", "--prompt", "hi"]) == 0
    assert seen == [
        ["agent", "roles", "--json"],
        ["agent", "run", "simple", "--prompt", "hi"],
    ]


def test_legacy_agent_passthrough_still_strips_agent(monkeypatch):
    cli, seen = _capture(monkeypatch)
    assert cli.main(["agent", "chat"]) == 0
    assert cli.main(["hermes", "status"]) == 0
    assert seen == [["chat"], ["status"]]


def test_connectors_status_is_forwarded_to_the_runtime():
    from voicebridge import cli_qubit_forward

    route = cli_qubit_forward.route(["connectors", "status"])
    assert route is not None
    assert route.kind == "forward"
    assert route.argv[:2] == ["connectors", "status"]
