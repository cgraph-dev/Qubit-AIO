"""`model activate` must be able to reach the GLOBAL default, not only a session.

The defect: `pmod_activate` adds `--session` with `default="cli"` and the handler
called `set_session_profile` unconditionally. So `model activate <profile>`
ALWAYS wrote a session override, and the global setter -- the one the dashboard
uses, and the only thing that changes what a NEW session gets -- was unreachable
from the CLI. The failure was silent and looked like success: the command exited
0, printed `{"state": "OK", ...}`, and reported the profile selected for
`session: "cli"` while the office-wide default stayed on the shipped local brain.

This was hit for real while setting the owner's default to deepseek-v4.1-flash:
`model activate ... --role chat` reported OK and changed nothing globally. The
A20 note in voice_commands.py recorded it as an unwired subcommand; it was
partly wired, to the wrong setter.

These tests drive the real `main(argv)` entry and assert on the resolver's own
selection, not on our own return payload -- a handler that returns OK while
writing the wrong scope is exactly the failure being pinned.
"""
import io
import json
import contextlib

import pytest


@pytest.fixture()
def isolated_cfg(tmp_path, monkeypatch):
    """A private config dir so a test can never touch the owner's selection.

    Only `qubit-brain4` SHIPS as a profile; everything else the owner can pick
    lives in their own registry, which isolation deliberately hides. So the
    fixture seeds one extra profile into the private registry -- the test needs
    a profile that is neither the immutable default nor absent.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    from voicebridge import config as C
    from voicebridge.providers import resolver as R

    cfg = C.Config.load()
    alt = R.ModelProfile(
        id="test-alt", kind="api", provider="test-provider", model="test-model",
        locality="external", base_url="https://example.invalid/v1",
        key_ref="env:TEST_ALT_API_KEY", roles=["chat"],
        # must clear the /chat context floor (65536) or _usable_as_default
        # correctly refuses to let the stored pointer stand in as the default
        context_tokens=200000)
    R._save_profiles(cfg, R.load_profiles(cfg) + [alt])

    # The fixture is only useful if the profile is really there: a silently
    # empty registry would make every assertion below fail for the wrong reason.
    assert R.get_profile(C.Config.load(), "test-alt") is not None
    return tmp_path


def _run(argv):
    """Invoke the real CLI, returning (exit_code, parsed_json_or_None)."""
    from voicebridge import cli
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(argv)
    out = buf.getvalue().strip()
    try:
        return rc, json.loads(out)
    except Exception:
        return rc, None


def _load_cfg():
    from voicebridge import config as C
    return C.Config.load()


def test_global_flag_is_accepted_by_the_parser(isolated_cfg):
    """The flag must exist and parse.

    Before the fix, `--global` was an argparse error, so there was no way to
    express "set the default" at all.
    """
    rc, payload = _run(["model", "activate", "test-alt", "--global"])
    assert rc in (0, 1), f"unexpected exit {rc}"
    assert payload is not None, "no JSON on stdout: the flag likely failed to parse"
    # the dry run must announce the GLOBAL action, and must NOT claim a session
    assert payload.get("action") == "would set active"
    assert "session" not in payload


def test_without_the_flag_the_dry_run_still_names_a_session(isolated_cfg):
    """Unchanged behaviour: a bare activate is a session override.

    The fix is additive; every existing invocation must keep its meaning.
    """
    rc, payload = _run(["model", "activate", "test-alt"])
    assert payload is not None
    assert payload.get("session") == "cli"
    assert payload.get("action") != "would set active"


def test_the_two_forms_are_distinguishable_in_their_output(isolated_cfg):
    """The whole diagnostic: the dry runs must not look identical.

    They used to: both printed `state: OK` with a profile, so a session write
    read as a global one. Distinguishing them is what makes the bug visible to
    the next person instead of silently misleading them.
    """
    _, global_out = _run(["model", "activate", "test-alt", "--global"])
    _, session_out = _run(["model", "activate", "test-alt"])
    assert global_out != session_out
    assert "session" not in global_out
    assert "session" in session_out


def test_global_confirm_makes_the_resolver_select_it(isolated_cfg):
    """End to end: after `--global --confirm`, the resolver's DEFAULT changes.

    `resolve()` is the authority every surface consults, so asserting on it
    (rather than on the command's own output) is the only check that proves the
    write actually landed in the global scope.
    """
    from voicebridge.providers import resolver as R
    cfg = _load_cfg()

    before = R.resolve(cfg, role="chat")
    assert before.source != "active", "fixture is dirty: a global default already exists"

    rc, payload = _run(["model", "activate", "test-alt", "--global", "--confirm"])
    assert payload is not None and payload.get("state") == "OK"

    after = R.resolve(_load_cfg(), role="chat")
    assert after.source == "active", "the global setter was not reached"
    assert after.profile_id == "test-alt"
