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
