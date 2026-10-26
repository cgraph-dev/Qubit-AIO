import argparse
import io
import json
from contextlib import redirect_stdout

from agent.runtime_port import RuntimeCapabilities, TurnEvent, UnknownRuntimeError
from qubit_cli.runtime_cmd import cmd_runtime


class _Fake:
    name = "fake"
    capabilities = RuntimeCapabilities(False, False, False, False)

    def run_turn(self, request, cancel=None):
        yield TurnEvent("done", text="PORT_OK", data={"completed": True})


class _Registry:
    def __init__(self):
        self._b = {"fake": _Fake()}

    def get(self, name):
        if name not in self._b:
            raise UnknownRuntimeError(name, sorted(self._b))
        return self._b[name]

    def available(self):
        return sorted(self._b)


def _args(**kw):
    base = dict(runtime_action="run", engine="fake", prompt="hi", query_file=None, json=True)
    base.update(kw)
    return argparse.Namespace(**base)


def test_engines_lists_registered_backends():
    out = io.StringIO()
    with redirect_stdout(out):
        assert cmd_runtime(argparse.Namespace(runtime_action="engines"), registry=_Registry()) == 0
    assert "fake" in out.getvalue()


def test_run_emits_json_events():
    out = io.StringIO()
    with redirect_stdout(out):
        assert cmd_runtime(_args(), registry=_Registry()) == 0
    payload = json.loads(out.getvalue())
    assert payload["engine"] == "fake"
    assert payload["events"][-1]["kind"] == "done"
    assert payload["events"][-1]["text"] == "PORT_OK"


def test_unknown_engine_exits_nonzero_with_the_list(capsys):
    assert cmd_runtime(_args(engine="nope"), registry=_Registry()) == 2
    err = capsys.readouterr().err
    assert "nope" in err and "fake" in err


def test_missing_prompt_is_refused(capsys):
    assert cmd_runtime(_args(prompt=None), registry=_Registry()) == 2
    assert "prompt" in capsys.readouterr().err.lower()
