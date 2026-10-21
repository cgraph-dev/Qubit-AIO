"""``update_cmd_check.select_apply_target`` is the one pre-write target resolver.

The apply path must not inline channel resolution: it has to pass the default
channel's forward-only rule and carry ``target_is_head`` so the fetch can be
skipped when the checkout already contains the release.
"""
from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from qubit_cli import update_cmd, update_cmd_check


def _args(**values):
    return SimpleNamespace(**values)


def _patch(monkeypatch, *, target, rides=True):
    from qubit_cli import config, release_channels, source_releases, update_channel, update_cmd_common

    calls = {"forward_only": [], "stops": []}
    monkeypatch.setattr(update_cmd_check, "_uc", lambda: update_cmd)
    monkeypatch.setattr(update_cmd, "_m", lambda: SimpleNamespace(PROJECT_ROOT="__root__"))
    monkeypatch.setattr(update_cmd, "_update_run_channel", lambda _args: "stable")
    monkeypatch.setattr(config, "require_readable_config_before_write", lambda path: path)
    monkeypatch.setattr(update_channel, "channel_record", lambda path, root: {"channel": "main"})
    monkeypatch.setattr(update_channel, "rides_default_channel",
                        lambda record, selected, root: rides)
    monkeypatch.setattr(release_channels, "retrying_reads", lambda: __import__("contextlib").nullcontext())
    monkeypatch.setattr(update_channel, "channel_record", lambda path, root: {"channel": "main"})
    monkeypatch.setattr(update_cmd_common, "_record_stop", lambda reason: calls["stops"].append(reason))

    def resolve(selected, git_cmd, root, *, forward_only=False):
        calls["forward_only"].append(forward_only)
        if isinstance(target, Exception):
            raise target
        return target

    monkeypatch.setattr(source_releases, "resolve_source_target", resolve)
    return calls


def test_explicit_branch_is_the_target_without_resolving_a_channel(monkeypatch):
    calls = _patch(monkeypatch, target=AssertionError("must not resolve"))
    request = {"home": "/tmp/home"}

    assert update_cmd_check.select_apply_target(
        _args(branch="feature"), "feature", request,
        git_cmd=["git"], stop=lambda: None) == ("origin/feature", None, False, None)
    assert calls["forward_only"] == []


def test_default_channel_forwards_its_forward_only_rule(monkeypatch):
    from qubit_cli.source_releases import SourceTarget

    calls = _patch(monkeypatch, target=SourceTarget(requested_channel="stable", channel="stable",
                                                    repository="o/r", branch="main"), rides=True)
    request = {"home": "/tmp/home"}

    assert update_cmd_check.select_apply_target(
        _args(), "main", request, git_cmd=["git"], stop=lambda: None) == \
        ("origin/main", None, False, "o/r")
    assert calls["forward_only"] == [True]
    assert request["branch"] == "main"


def test_release_past_the_default_head_targets_head_without_a_sha(monkeypatch):
    from qubit_cli.source_releases import SourceTarget

    _patch(monkeypatch, target=SourceTarget(requested_channel="stable", channel="stable",
                                            repository="o/r", commit="a" * 40,
                                            version="1.2.3", ahead=True))
    request = {"home": "/tmp/home"}

    assert update_cmd_check.select_apply_target(
        _args(), "main", request, git_cmd=["git"], stop=lambda: None) == \
        ("a" * 40, "a" * 40, True, "o/r")
    assert request["expected_sha"] == "a" * 40


def test_a_named_release_target_is_not_treated_as_head(monkeypatch):
    from qubit_cli.source_releases import SourceTarget

    _patch(monkeypatch, target=SourceTarget(requested_channel="stable", channel="stable",
                                            repository="o/r", commit="b" * 40,
                                            version="1.2.3", ahead=False))
    request = {"home": "/tmp/home"}

    assert update_cmd_check.select_apply_target(
        _args(), "main", request, git_cmd=["git"], stop=lambda: None) == \
        ("b" * 40, "b" * 40, False, "o/r")


def test_channel_retirement_is_recorded_only_for_an_unchosen_default(monkeypatch):
    from qubit_cli.source_releases import SourceTarget

    _patch(monkeypatch, target=SourceTarget(requested_channel="stable", channel="release",
                                            repository="o/r", branch="release"))
    from qubit_cli import update_channel
    monkeypatch.setattr(update_channel, "channel_record",
                        lambda path, root: {"channel": "stable"})
    request = {"home": "/tmp/home"}
    update_cmd_check.select_apply_target(
        _args(), "main", request, git_cmd=["git"], stop=lambda: None)
    assert request["channel_retirement"]["destination"] == "release"
    assert request["branch"] == "release"


def test_unresolvable_channel_stops_before_any_write(monkeypatch):
    calls = _patch(monkeypatch, target=ValueError("no channel"))
    request = {"home": "/tmp/home"}
    stopped = []

    with pytest.raises(SystemExit):
        update_cmd_check.select_apply_target(
            _args(), "main", request, git_cmd=["git"], stop=lambda: stopped.append(True))
    assert stopped == [True]
    assert calls["stops"] == ["channel_unresolved"]
