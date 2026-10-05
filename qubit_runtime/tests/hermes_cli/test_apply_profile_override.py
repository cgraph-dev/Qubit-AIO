"""Regression tests for _apply_profile_override QUBIT_HOME guard (issue #22502).

When QUBIT_HOME is set to the qubit root (e.g. systemd hardcodes
QUBIT_HOME=/root/.qubit), _apply_profile_override must still read
active_profile and update QUBIT_HOME to the profile directory.

When QUBIT_HOME is already a profile directory (.../profiles/<name>),
_apply_profile_override must trust it and return without re-reading
active_profile (child-process inheritance contract).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest


@pytest.fixture(autouse=True)
def _platform_home(tmp_path, monkeypatch):
    monkeypatch.setattr("qubit_constants._get_platform_default_qubit_home", lambda: tmp_path / ".qubit")


def _run_apply_profile_override(
    tmp_path, monkeypatch, *, qubit_home: str | None, active_profile: str | None,
    argv: list[str] | None = None, extra_env: dict[str, str] | None = None,
    create_active_profile: bool = True,
):
    """Run _apply_profile_override in isolation.

    Returns the value of os.environ["QUBIT_HOME"] after the call,
    or None if unset.
    """
    qubit_root = tmp_path / ".qubit"
    qubit_root.mkdir(parents=True, exist_ok=True)

    if active_profile is not None:
        (qubit_root / "active_profile").write_text(active_profile, encoding="utf-8")

    if create_active_profile and active_profile and active_profile != "default":
        (qubit_root / "profiles" / active_profile).mkdir(parents=True, exist_ok=True)
        (qubit_root / "profiles" / active_profile / "config.yaml").write_text(
            "{}\n", encoding="utf-8")  # identity marker

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    if qubit_home is not None:
        monkeypatch.setenv("QUBIT_HOME", qubit_home)
    else:
        monkeypatch.delenv("QUBIT_HOME", raising=False)

    monkeypatch.setattr(sys, "argv", argv or ["qubit", "gateway", "start"])

    # Scrub supervisor markers the host environment may carry (systemd-run
    # CI runners export INVOCATION_ID) so each test controls them explicitly.
    for var in (
        "QUBIT_SUPERVISED_CHILD",
        "QUBIT_S6_SUPERVISED_CHILD",
        "INVOCATION_ID",
        "QUBIT_GATEWAY_EXTERNAL_SUPERVISOR",
    ):
        monkeypatch.delenv(var, raising=False)

    for key, value in (extra_env or {}).items():
        monkeypatch.setenv(key, value)

    from qubit_cli.main import _apply_profile_override
    _apply_profile_override()

    return os.environ.get("QUBIT_HOME")


@pytest.mark.parametrize("argv", [
    ["qubit", "profile", "list"],
    ["qubit", "profile", "use", "default"],
    ["qubit", "uninstall"],
    ["qubit", "uninstall", "--dry-run"],
    ["qubit", "uninstall", "--help"],
])
@pytest.mark.parametrize("exported_home", [False, True])
def test_missing_sticky_profile_allows_recovery_commands(
    tmp_path, monkeypatch, capsys, argv, exported_home,
):
    root = tmp_path / ".qubit"
    result = _run_apply_profile_override(
        tmp_path, monkeypatch, qubit_home=str(root) if exported_home else None,
        active_profile="ray",
        create_active_profile=False, argv=argv,
    )

    assert result == str(root)
    assert "saved profile 'ray' no longer exists; running this recovery command" in capsys.readouterr().err
    if argv[1:3] == ["profile", "use"]:
        from qubit_cli.profile_cmd import cmd_profile

        cmd_profile(SimpleNamespace(profile_action="use", profile_name="default"))
        assert not (root / "active_profile").exists()
    else:
        assert (root / "active_profile").read_text(encoding="utf-8-sig") == "ray"


@pytest.mark.parametrize("argv, expect_hint", [
    (["qubit", "chat"], True),
    (["qubit", "uninstall", "--data"], True),
    (["qubit", "uninstall", "--dat", "--yes"], True),
    (["qubit", "uninstall", "--full", "--yes"], True),
    (["qubit", "uninstall", "--fu"], True),
    (["qubit", "uninstall", "--full", "--data"], True),
    (["qubit", "-p", "ray", "uninstall"], False),  # explicit -p keeps the create hint
])
def test_missing_profile_still_blocks_other_or_explicit_commands(
    tmp_path, monkeypatch, capsys, argv, expect_hint,
):
    with pytest.raises(SystemExit) as exc:
        _run_apply_profile_override(
            tmp_path, monkeypatch, qubit_home=str(tmp_path / ".qubit"),
            active_profile="ray", create_active_profile=False, argv=argv,
        )
    assert exc.value.code == 1
    assert ("qubit profile use default" in capsys.readouterr().err) is expect_hint


class TestApplyProfileOverrideQubitHomeGuard:
    """Regression guard for issue #22502.

    Verifies that QUBIT_HOME pointing to the qubit root does NOT suppress
    the active_profile check, while QUBIT_HOME already pointing to a
    profile directory IS trusted as-is.
    """

    def test_qubit_home_at_root_with_active_profile_is_redirected(
        self, tmp_path, monkeypatch
    ):
        """QUBIT_HOME=/root/.qubit + active_profile=coder must redirect
        QUBIT_HOME to .../profiles/coder.

        Bug scenario from #22502: systemd sets QUBIT_HOME to the qubit root
        and the user switches to a profile via `qubit profile use`.
        Before the fix, the guard returned early and active_profile was ignored.
        """
        qubit_root = tmp_path / ".qubit"
        qubit_root.mkdir(parents=True, exist_ok=True)

        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            qubit_home=str(qubit_root),
            active_profile="coder",
        )

        assert result is not None, "QUBIT_HOME must be set after profile redirect"
        assert "profiles" in result, (
            f"Expected QUBIT_HOME to point into profiles/ dir, got: {result!r}"
        )
        assert result.endswith("coder"), (
            f"Expected QUBIT_HOME to end with 'coder', got: {result!r}"
        )


    @pytest.mark.platforms("posix")
    def test_sudo_explicit_profile_resolves_invoking_users_profile(self, tmp_path, monkeypatch):
        """sudo elias ... should resolve `-p elias` under SUDO_USER, not root."""
        root_home = tmp_path / "root"
        user_home = tmp_path / "home" / "qubit"
        profile_dir = user_home / ".qubit" / "profiles" / "elias"
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "config.yaml").write_text(
            "{}\n", encoding="utf-8")  # identity marker: a bare dir does not resolve
        (root_home / ".qubit").mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(Path, "home", lambda: root_home)
        monkeypatch.setenv("SUDO_USER", "qubit")
        monkeypatch.delenv("QUBIT_HOME", raising=False)
        monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
        monkeypatch.setattr(sys, "argv", ["qubit", "-p", "elias", "gateway", "install", "--system"])

        import pwd

        monkeypatch.setattr(pwd, "getpwnam", lambda name: SimpleNamespace(pw_dir=str(user_home)))

        from qubit_cli.main import _apply_profile_override, _resolve_sudo_user_profile_env
        _apply_profile_override()

        assert os.environ.get("QUBIT_HOME") == str(profile_dir)
        assert sys.argv == ["qubit", "gateway", "install", "--system"]
        # Same identity gate as ``-p`` without sudo: a marker-less shell is not a profile.
        (user_home / ".qubit" / "profiles" / "ghost" / "cron").mkdir(parents=True)
        assert _resolve_sudo_user_profile_env("ghost") is None




class TestSupervisedChildIgnoresStickyProfile:
    """The reserved default gateway s6 slot must not follow active_profile.

    Inside the Docker s6 image the ``gateway-default`` service slot runs a
    bare ``qubit gateway run`` (no ``-p``) to mean "the root QUBIT_HOME
    profile". The run-script exports ``QUBIT_S6_SUPERVISED_CHILD=1``.
    Without a guard, ``_apply_profile_override`` would read the sticky
    ``active_profile`` file (set by e.g. the dashboard profile switcher) and
    redirect the reserved default gateway into that profile — producing a
    duplicate gateway for the active profile and no real default gateway.
    """


    def test_non_supervised_run_still_follows_active_profile(
        self, tmp_path, monkeypatch
    ):
        """Without the sentinel, a normal `qubit gateway run` still honors
        active_profile — the guard is scoped strictly to supervised children."""
        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            qubit_home=None,
            active_profile="briefer",
            argv=["qubit", "gateway", "run"],
        )

        assert result is not None
        assert result.endswith("briefer")

    def test_supervised_named_profile_flag_still_wins(self, tmp_path, monkeypatch):
        """A supervised named-profile slot passes ``-p <name>`` explicitly;
        that must still resolve (the sentinel guard only skips the sticky
        active_profile fallback, never an explicit flag)."""
        qubit_root = tmp_path / ".qubit"
        qubit_root.mkdir(parents=True, exist_ok=True)
        (qubit_root / "active_profile").write_text("briefer", encoding="utf-8")
        for name in ("briefer", "coder"):
            (qubit_root / "profiles" / name).mkdir(parents=True, exist_ok=True)
            (qubit_root / "profiles" / name / "config.yaml").write_text(
                "{}\n", encoding="utf-8")  # identity marker

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("QUBIT_HOME", raising=False)
        monkeypatch.setenv("QUBIT_S6_SUPERVISED_CHILD", "1")
        monkeypatch.setattr(sys, "argv", ["qubit", "-p", "coder", "gateway", "run"])

        from qubit_cli.main import _apply_profile_override
        _apply_profile_override()

        result = os.environ.get("QUBIT_HOME")
        assert result is not None
        assert result.endswith("coder")



class TestGeneralizedSupervisorMarkers:
    """Regression tests for issue #74872.

    A systemd/launchd/Scheduled-Task supervised gateway launch pins its
    profile identity via the unit's QUBIT_HOME (root home for the default
    profile). It must NEVER follow the sticky ``active_profile`` file —
    otherwise the default-profile gateway silently assumes another profile's
    identity (logs + Telegram bot token) and double-polls that profile's
    token. Markers: QUBIT_SUPERVISED_CHILD (generalized, exported by
    generated units), INVOCATION_ID (systemd, gateway commands only), and
    QUBIT_GATEWAY_EXTERNAL_SUPERVISOR (explicit opt-in).
    """

    def _root_home(self, tmp_path):
        qubit_root = tmp_path / ".qubit"
        qubit_root.mkdir(parents=True, exist_ok=True)
        return qubit_root

    def test_supervised_child_marker_skips_active_profile(
        self, tmp_path, monkeypatch
    ):
        """QUBIT_SUPERVISED_CHILD=1 + root QUBIT_HOME must keep the
        default profile's home even when active_profile names another
        profile (the #74872 identity-assumption vector)."""
        qubit_root = self._root_home(tmp_path)
        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            qubit_home=str(qubit_root),
            active_profile="telegram_nick",
            argv=["qubit", "gateway", "run"],
            extra_env={"QUBIT_SUPERVISED_CHILD": "1"},
        )
        assert result == str(qubit_root), (
            f"supervised default gateway was redirected to {result!r}"
        )

    def test_systemd_invocation_id_skips_active_profile_for_gateway(
        self, tmp_path, monkeypatch
    ):
        """INVOCATION_ID (systemd service child) must suppress the sticky
        redirect for gateway commands — covers units installed before the
        QUBIT_SUPERVISED_CHILD marker existed."""
        qubit_root = self._root_home(tmp_path)
        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            qubit_home=str(qubit_root),
            active_profile="telegram_nick",
            argv=["qubit", "gateway", "run"],
            extra_env={"INVOCATION_ID": "deadbeef" * 4},
        )
        assert result == str(qubit_root)

    def test_invocation_id_does_not_affect_non_gateway_commands(
        self, tmp_path, monkeypatch
    ):
        """INVOCATION_ID leaks into every descendant of a systemd-launched
        process (CI runners, user services). Non-gateway commands must keep
        honoring the sticky active_profile."""
        qubit_root = self._root_home(tmp_path)
        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            qubit_home=str(qubit_root),
            active_profile="coder",
            argv=["qubit", "chat"],
            extra_env={"INVOCATION_ID": "deadbeef" * 4},
        )
        assert result is not None
        assert result.endswith("coder")

    def test_external_supervisor_marker_skips_active_profile(
        self, tmp_path, monkeypatch
    ):
        qubit_root = self._root_home(tmp_path)
        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            qubit_home=str(qubit_root),
            active_profile="telegram_nick",
            argv=["qubit", "gateway", "run"],
            extra_env={"QUBIT_GATEWAY_EXTERNAL_SUPERVISOR": "1"},
        )
        assert result == str(qubit_root)

    def test_desktop_ssh_serve_child_skips_active_profile(self, tmp_path, monkeypatch):
        """A Desktop-owned `serve --ssh-session-token-file` child names its profile explicitly
        (or none for the root home); the remote host's sticky active_profile must not re-home
        it, or Settings read one profile's config.yaml while the user edits another."""
        qubit_root = self._root_home(tmp_path)
        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            qubit_home=str(qubit_root),
            active_profile="telegram_nick",
            argv=["qubit", "serve", "--isolated", "--host", "127.0.0.1", "--port", "0",
                  "--ssh-session-token-file", "/tmp/x/y.token"],
        )
        assert result == str(qubit_root)

    def test_generated_systemd_unit_exports_supervised_marker(
        self, tmp_path, monkeypatch
    ):
        """The generated systemd unit must carry the marker so fresh installs
        are protected without relying on the INVOCATION_ID heuristic."""
        monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir()
        from qubit_cli.gateway import generate_systemd_unit

        unit = generate_systemd_unit()
        assert 'Environment="QUBIT_SUPERVISED_CHILD=1"' in unit

    def test_generated_launchd_plist_exports_supervised_marker(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir()
        from qubit_cli.gateway import generate_launchd_plist

        plist = generate_launchd_plist()
        assert "<key>QUBIT_SUPERVISED_CHILD</key>" in plist


class TestS6ContainerGatewayRun:
    """Inside the s6 image a bare ``gateway run`` (the image's CMD) redirects to the supervised
    ``gateway-default`` slot. It must keep that root identity whatever ``active_profile`` says;
    otherwise every container boot starts the named slot the reconciler registered down."""

    def test_the_redirected_run_keeps_the_root_home_despite_the_active_profile(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("qubit_cli.service_manager._s6_running", lambda: True)
        root = tmp_path / ".qubit"
        result = _run_apply_profile_override(
            tmp_path, monkeypatch, qubit_home=str(root), active_profile="coder",
            argv=["qubit", "gateway", "run"],
        )
        assert result == str(root)

    def test_a_foreground_run_and_other_verbs_still_follow_the_active_profile(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("qubit_cli.service_manager._s6_running", lambda: True)
        root = tmp_path / ".qubit"
        for argv in (["qubit", "gateway", "run", "--no-supervise"], ["qubit", "chat"]):
            result = _run_apply_profile_override(
                tmp_path, monkeypatch, qubit_home=str(root), active_profile="coder", argv=argv,
            )
            assert result == str(root / "profiles" / "coder"), argv
