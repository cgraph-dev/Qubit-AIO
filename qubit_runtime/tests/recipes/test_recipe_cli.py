import argparse
import io
import json
from contextlib import redirect_stdout

from agent.runtime_port import RuntimeCapabilities, TurnEvent, UnknownRuntimeError
from qubit_cli import recipe_cmd
from qubit_cli.recipe_cmd import cmd_recipe
from recipes.runner import RecipeRun
from recipes.sections import available


class _FakeBackend:
    name = "fake"
    capabilities = RuntimeCapabilities(True, True, False, False)

    def __init__(self, events=()):
        self.events = events

    def run_turn(self, request, cancel=None):
        yield from self.events


class _Registry:
    def __init__(self, backends):
        self._backends = backends

    def get(self, name):
        if name not in self._backends:
            raise UnknownRuntimeError(name, sorted(self._backends))
        return self._backends[name]


def _args(**kw):
    base = dict(recipe_action="run", recipe_id="morning_digest", engine="fake",
                dry_run=False, json=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _sections():
    return (
        available("weather", "Weather", ["Sunny"]),
        available("calendar", "Calendar", ["No events scheduled today."]),
        available("unread_mail", "Unread mail", ["No unread mail."]),
        available("health", "Health", []),
    )


def _patch_runner(monkeypatch, result):
    monkeypatch.setattr(recipe_cmd, "run_recipe", lambda *a, **k: result)


def test_list_prints_ids():
    out = io.StringIO()
    with redirect_stdout(out):
        assert cmd_recipe(argparse.Namespace(recipe_action="list", json=False)) == 0
    assert "morning_digest" in out.getvalue()


def test_list_json_is_machine_readable():
    out = io.StringIO()
    with redirect_stdout(out):
        assert cmd_recipe(argparse.Namespace(recipe_action="list", json=True)) == 0
    payload = json.loads(out.getvalue())
    ids = [row["id"] for row in payload]
    assert ids == ["deep_research", "monitor_operative", "morning_digest"]
    morning = next(row for row in payload if row["id"] == "morning_digest")
    assert morning["sections"] == ["weather", "calendar", "unread_mail", "health"]
    monitor = next(row for row in payload if row["id"] == "monitor_operative")
    assert monitor["sections"] == ["cron", "kanban", "memory", "compression"]


def test_run_prints_the_brief(monkeypatch):
    _patch_runner(monkeypatch, RecipeRun(
        recipe_id="morning_digest", engine="fake", brief="Good morning.",
        sections=_sections(), terminal="done"))
    out = io.StringIO()
    with redirect_stdout(out):
        assert cmd_recipe(_args(), backend_registry=_Registry({"fake": _FakeBackend()})) == 0
    assert out.getvalue().strip() == "Good morning."


def test_run_json_carries_sections_and_events(monkeypatch):
    _patch_runner(monkeypatch, RecipeRun(
        recipe_id="morning_digest", engine="fake", brief="Good morning.",
        sections=_sections(), terminal="done",
        events=(TurnEvent("done", text="Good morning."),)))
    out = io.StringIO()
    with redirect_stdout(out):
        assert cmd_recipe(_args(json=True),
                          backend_registry=_Registry({"fake": _FakeBackend()})) == 0
    payload = json.loads(out.getvalue())
    assert payload["recipe"] == "morning_digest"
    assert payload["sections"][0]["facts"] == ["Sunny"]
    assert payload["events"][-1]["kind"] == "done"


def test_unknown_recipe_exits_two(capsys):
    assert cmd_recipe(_args(recipe_id="nope")) == 2
    err = capsys.readouterr().err
    assert "nope" in err and "morning_digest" in err


def test_unknown_engine_exits_two(capsys):
    assert cmd_recipe(_args(engine="nope"), backend_registry=_Registry({})) == 2
    assert "nope" in capsys.readouterr().err


def test_refusal_exits_two(monkeypatch, capsys):
    _patch_runner(monkeypatch, RecipeRun(
        recipe_id="morning_digest", engine="openjarvis", brief="", sections=(),
        terminal="error", refusal="openjarvis_unavailable", hint="set the pin"))
    assert cmd_recipe(_args(), backend_registry=_Registry({"fake": _FakeBackend()})) == 2
    err = capsys.readouterr().err
    assert "openjarvis_unavailable" in err and "set the pin" in err


def test_failed_turn_exits_one(monkeypatch, capsys):
    _patch_runner(monkeypatch, RecipeRun(
        recipe_id="morning_digest", engine="fake", brief="", sections=(),
        terminal="error", failed=True))
    assert cmd_recipe(_args(), backend_registry=_Registry({"fake": _FakeBackend()})) == 1
    assert "failed" in capsys.readouterr().err


def test_cancelled_turn_exits_130(monkeypatch, capsys):
    _patch_runner(monkeypatch, RecipeRun(
        recipe_id="morning_digest", engine="fake", brief="", sections=(),
        terminal="error", cancelled=True))
    assert cmd_recipe(_args(), backend_registry=_Registry({"fake": _FakeBackend()})) == 130
    assert "interrupted" in capsys.readouterr().err


def test_initialisation_error_exits_130(monkeypatch):
    def _interrupt(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(recipe_cmd, "run_recipe", _interrupt)
    assert cmd_recipe(_args(), backend_registry=_Registry({"fake": _FakeBackend()})) == 130


def test_dry_run_renders_fixture_facts(monkeypatch):
    from recipes.runner import run_recipe as real_run_recipe

    monkeypatch.setattr(
        recipe_cmd, "run_recipe",
        lambda spec, **kw: real_run_recipe(spec, fixture=_sections(), **kw))
    out = io.StringIO()
    with redirect_stdout(out):
        assert cmd_recipe(_args(dry_run=True),
                          backend_registry=_Registry({"fake": _FakeBackend()})) == 0
    text = out.getvalue()
    assert text.startswith("# Morning digest")
    assert "## Weather" in text and "Sunny" in text


def test_parser_registers_list_and_run():
    from qubit_cli.subcommands.recipe import build_recipe_parser

    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command")
    build_recipe_parser(subs, cmd_recipe=lambda args: 0)
    args = parser.parse_args(
        ["recipe", "run", "morning_digest", "--engine", "openjarvis", "--dry-run", "--json"])
    assert args.recipe_action == "run"
    assert args.recipe_id == "morning_digest"
    assert args.engine == "openjarvis" and args.dry_run and args.json
    listed = parser.parse_args(["recipe", "list"])
    assert listed.recipe_action == "list" and not listed.json


def test_main_cli_registers_the_recipe_family():
    from qubit_cli.main import _build_cli_parser

    parser, _subparsers = _build_cli_parser()
    actions = [a for a in parser._actions
               if isinstance(a, argparse._SubParsersAction)]
    assert actions
    assert "recipe" in actions[0].choices


def test_refusal_json_still_emits_a_machine_readable_result(monkeypatch, capsys):
    _patch_runner(monkeypatch, RecipeRun(
        recipe_id="morning_digest", engine="openjarvis", brief="", sections=(),
        terminal="error", refusal="openjarvis_unavailable", hint="set the pin"))
    out = io.StringIO()
    with redirect_stdout(out):
        assert cmd_recipe(_args(json=True),
                          backend_registry=_Registry({"fake": _FakeBackend()})) == 2
    payload = json.loads(out.getvalue())
    assert payload["refusal"] == "openjarvis_unavailable"
    assert capsys.readouterr().err == ""
