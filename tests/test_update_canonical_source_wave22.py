"""Wave 22 — canonical Qubit update source resolution.

The canonical public repository is ``cgraph-dev/qubit-voicebridge``
(``https://github.com/cgraph-dev/qubit-voicebridge.git``), channel ``stable``,
branch ``main``. This suite proves, hermetically (injected git runners and
injected remote-URL lookups — no network, no real checkout, no ``~/.hermes``):

* a fresh install with no configured source resolves to the canonical URL;
* a clone whose ``origin`` is the legacy OpenClaw repo is overridden by the
  canonical URL (never ``origin``/openclaw);
* an explicit ``hermes``/``openclaw`` *source URL* is a typed
  ``UPDATE_LEGACY_SOURCE_URL`` refusal;
* a configured ``update.remote_url`` (canonical or another non-legacy Qubit
  URL) is respected;
* ``update_manager.check`` on a checkout whose origin is a legacy path reports
  the canonical URL as the update source.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

LOCAL_SHA = "a" * 40
REMOTE_SHA = "b" * 40
CANONICAL = "https://github.com/cgraph-dev/qubit-voicebridge.git"
OPENCLAW_ORIGIN = "https://github.com/cgraph-dev/openclaw-voice-bridge.git"
HERMES_ORIGIN = "https://github.com/NousResearch/hermes-agent.git"


class _Cfg:
    """Minimal nested-dict config implementing ``.get(*path, default=)``."""

    def __init__(self, data=None):
        self._d = data or {}

    def get(self, *path, default=None):
        node = self._d
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def _lookup(mapping):
    """An injectable ``remote_url_lookup`` returning ``mapping.get(name)``."""
    return lambda name: mapping.get(name)


# ---------------------------------------------------------------------------
# resolve_source — canonical default
# ---------------------------------------------------------------------------
def test_resolve_source_no_config_is_canonical():
    from voicebridge import update_channel as UC

    out = UC.resolve_source(_Cfg({}), remote_url_lookup=_lookup({}))
    assert out["ok"] is True
    assert out["remote"] == UC.QUBIT_UPDATE_URL == CANONICAL
    assert out["url"] == CANONICAL
    assert out["source_kind"] == "canonical"
    assert out["channel"] == "stable"
    assert out["branch"] == "main"
    assert out["follow"] == "tag"


def test_resolve_source_legacy_openclaw_origin_is_overridden():
    from voicebridge import update_channel as UC

    out = UC.resolve_source(_Cfg({}),
                            remote_url_lookup=_lookup({"origin": OPENCLAW_ORIGIN}))
    assert out["ok"] is True
    assert out["remote"] == CANONICAL, out
    assert out["source_kind"] == "canonical"
    assert "openclaw" not in out["remote"].lower()
    assert out["remote"] != "origin"


def test_resolve_source_configured_legacy_remote_name_is_overridden():
    """A configured remote *name* with a legacy actual URL is overridden."""
    from voicebridge import update_channel as UC

    cfg = _Cfg({"update": {"remote": "origin"}})
    out = UC.resolve_source(cfg,
                            remote_url_lookup=_lookup({"origin": HERMES_ORIGIN}))
    assert out["ok"] is True
    assert out["remote"] == CANONICAL, out
    assert out["source_kind"] == "canonical"


def test_resolve_source_origin_already_qubit_is_honored():
    from voicebridge import update_channel as UC

    out = UC.resolve_source(_Cfg({}),
                            remote_url_lookup=_lookup({"origin": CANONICAL}))
    assert out["ok"] is True
    assert out["remote"] == "origin"
    assert out["source_kind"] == "origin_qubit"
    assert out["url"] == CANONICAL


# ---------------------------------------------------------------------------
# resolve_source — explicit / configured URL guard
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("legacy", [
    "https://github.com/NousResearch/hermes-agent.git",
    "https://github.com/cgraph-dev/openclaw-voice-bridge.git",
    "git@github.com:openclaw/openclaw.git",
])
def test_resolve_source_explicit_legacy_url_is_typed_refusal(legacy):
    from voicebridge import update_channel as UC

    out = UC.resolve_source(_Cfg({}), legacy, remote_url_lookup=_lookup({}))
    assert out["ok"] is False
    assert out["code"] == UC.LEGACY_SOURCE_URL_CODE
    assert out["fallback_remote"] == UC.DEFAULT_REMOTE_NAME
    assert out["policy"], "the exact refusal policy must be reported"


def test_resolve_source_remote_name_is_never_refused():
    """A bare remote NAME is not a URL, so the URL guard never applies."""
    from voicebridge import update_channel as UC

    out = UC.resolve_source(_Cfg({}), "origin", remote_url_lookup=_lookup({}))
    assert out["ok"] is True
    assert out["remote"] == "origin"
    assert out["kind"] == "remote-name"


def test_resolve_source_configured_canonical_remote_url_is_respected():
    from voicebridge import update_channel as UC

    cfg = _Cfg({"update": {"remote_url": CANONICAL}})
    out = UC.resolve_source(cfg, remote_url_lookup=_lookup({}))
    assert out["ok"] is True
    assert out["remote"] == CANONICAL
    assert out["source_kind"] == "remote_url"


def test_resolve_source_configured_non_legacy_qubit_url_is_respected():
    from voicebridge import update_channel as UC

    fork = "https://github.com/my-org/qubit-voicebridge.git"
    cfg = _Cfg({"update": {"remote_url": fork}})
    out = UC.resolve_source(cfg, remote_url_lookup=_lookup({}))
    assert out["ok"] is True
    assert out["remote"] == fork
    assert out["source_kind"] == "remote_url"


# ---------------------------------------------------------------------------
# update_manager — canonical source on a legacy-origin checkout
# ---------------------------------------------------------------------------
class _FakeGit:
    """Deterministic injected git; origin's URL is always the legacy repo."""

    def __init__(self, *, local=LOCAL_SHA, remote=REMOTE_SHA, origin=OPENCLAW_ORIGIN):
        self.local = local
        self.remote = remote
        self.origin = origin
        self.calls = []

    def __call__(self, argv, timeout):
        argv = list(argv)
        self.calls.append(argv)
        args = argv[3:] if len(argv) > 3 else []
        if args[:2] == ["remote", "get-url"]:
            if self.origin is None:
                return 1, "", "no such remote"
            return 0, self.origin + "\n", ""
        if args[:1] == ["rev-parse"]:
            return 0, self.local + "\n", ""
        if args[:2] == ["status", "--porcelain"]:
            return 0, "", ""
        if args[:1] == ["ls-remote"]:
            if self.remote is None:
                return 1, "", "network unreachable"
            return 0, f"{self.remote}\trefs/heads/main\n", ""
        return 1, "", f"unexpected command {args}"


@pytest.fixture(autouse=True)
def _estop_clear(monkeypatch):
    from voicebridge import update_manager as UM

    monkeypatch.setattr(UM, "_estop", lambda _cfg: False)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "install"
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    return repo


def test_check_reports_canonical_source_on_legacy_origin(tmp_path):
    from voicebridge import update_channel as UC
    from voicebridge import update_manager as UM

    repo = _repo(tmp_path)
    cfg = _Cfg({"update": {"repo_root": str(repo), "branch": "main"}})
    fake = _FakeGit()
    out = UM.check(cfg, git=fake)

    assert out["state"] == "OK", out
    assert out["remote"] == UC.QUBIT_UPDATE_URL == CANONICAL, out
    assert out["source_kind"] == "canonical"
    assert out["source_url"] == CANONICAL
    assert "openclaw" not in out["remote"].lower()
    assert out["remote_revision"] == REMOTE_SHA


def test_check_explicit_legacy_source_url_is_typed_refusal(tmp_path):
    from voicebridge import update_channel as UC
    from voicebridge import update_manager as UM

    repo = _repo(tmp_path)
    cfg = _Cfg({"update": {"repo_root": str(repo)}})
    out = UM.check(cfg, source=HERMES_ORIGIN, git=_FakeGit())
    assert out["state"] == "DEGRADED"
    assert out["code"] == UC.LEGACY_SOURCE_URL_CODE


def test_git_argv_accepts_a_url_source(tmp_path):
    from voicebridge import update_manager as UM

    argv = UM._git_argv(tmp_path, ["ls-remote", "--heads", CANONICAL, "main"])
    assert argv[0] == "git"
    assert CANONICAL in argv
    assert argv[-1] == "main"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
