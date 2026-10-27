import argparse

import pytest

from qubit_cli.main import _refuse_engine_outside_oneshot


def _args(**kw):
    base = dict(command="runtime", engine="openjarvis", oneshot=None,
                runtime_action="run", recipe_action=None)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.mark.parametrize("kw", [
    dict(command="runtime", runtime_action="run"),
    dict(command="recipe", recipe_action="run"),
    dict(command="chat"),
    dict(command=None),
    dict(command="runtime", engine="native"),
    dict(command="runtime", oneshot=True),
])
def test_engine_is_allowed_on_the_commands_that_own_it(kw):
    _refuse_engine_outside_oneshot(_args(**kw))


def test_engine_is_refused_elsewhere(capsys):
    with pytest.raises(SystemExit) as exc:
        _refuse_engine_outside_oneshot(
            _args(command="cron", runtime_action=None, recipe_action=None))
    assert exc.value.code == 2
    assert "--engine is only supported" in capsys.readouterr().err


def test_top_level_engine_survives_the_runtime_subparser():
    import argparse
    from qubit_cli.subcommands.runtime import build_runtime_parser

    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", default=None)
    subs = parser.add_subparsers(dest="command")
    build_runtime_parser(subs, cmd_runtime=lambda args: 0)
    args = parser.parse_args(["--engine", "openjarvis", "runtime", "run", "--prompt", "x"])
    assert args.engine == "openjarvis"


def test_top_level_engine_survives_the_recipe_subparser():
    import argparse
    from qubit_cli.subcommands.recipe import build_recipe_parser

    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", default=None)
    subs = parser.add_subparsers(dest="command")
    build_recipe_parser(subs, cmd_recipe=lambda args: 0)
    args = parser.parse_args(
        ["--engine", "openjarvis", "recipe", "run", "morning_digest"])
    assert args.engine == "openjarvis"
