"""Canonical Qubit update **channel / ref / repository** policy.

This is the single authority for *where* Qubit updates come from and *what*
they track. It is a pure-stdlib leaf module: the CLI/updater can import it
before the full config machinery loads, and it never imports the vendored fork.

What it decides
---------------
* **Repository identity.** :data:`QUBIT_REPOSITORY` (``cgraph-dev/qubit-voicebridge``)
  is the intended public Qubit repo — the *default destination*, never a
  hard requirement. A user's own clone may track its own ``origin`` (which for
  a public user *is* the Qubit repo), a mirror, or a private fork.
* **Source.** :func:`resolve_source` resolves the effective git remote/branch/
  tag/ref from, in order: an explicit override (``--source``), the configured
  full URL (``update.remote_url``), a configured remote *name* whose actual URL
  (``git remote get-url``, injectable / no shell) is honored when non-legacy,
  and otherwise the canonical Qubit repository URL (:data:`QUBIT_UPDATE_URL`) —
  the clone's own ``origin`` is used when it already points at the Qubit repo
  and overridden by the canonical URL when it is legacy-branded. Plus the
  configured branch (``update.branch``, default ``main``) and the optional
  version tag (``update.tag``).
* **Channel.** :func:`resolve_channel` selects ``stable`` / ``beta`` /
  ``canary``; :func:`set_channel` persists it **under QUBIT_HOME** (never in
  the source tree, never a secret). Default: ``stable``.
* **Tag vs branch follow.** :func:`release_tag_support` says whether a channel
  follows a version tag (stable/beta) or the branch head (canary).
* **URL safety.** :func:`validate_remote_url` does strict parsing;
  :func:`redact_url` strips embedded credentials from any URL this module
  returns, so a token can never be logged or printed. A configured update
  *source URL* that carries legacy assistant branding (``hermes``,
  ``nousresearch``, ``nous``, ``openclaw``) is refused with a typed result
  (``UPDATE_LEGACY_SOURCE_URL``). Using the clone's own ``origin`` remote
  *name* stays allowed — a public user's ``origin`` IS the Qubit repo; a
  legacy-branded remote **URL** (configured or the default ``origin``) is
  overridden by the canonical Qubit URL, never refused.
* **Anonymous vs authenticated.** :func:`anonymous_update_supported` is the
  documented heuristic: a public forge (HTTP(S), no embedded credentials) is
  pulled anonymously; a private/self-hosted/SSH source needs the user's own git
  credential helper or a token in the environment. The updater never embeds a
  token and never prints one.

Policy vs mechanism
-------------------
This module says *what* the source/channel should be. The actual git verbs
(``ls-remote``, ``pull --ff-only``, ``reset --hard`` on rollback, snapshot
bundles) live in :mod:`voicebridge.update_manager`; the CLI wiring lives in
``qubit update --check|--apply|--rollback``. See ``docs/UPDATE.md``.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit

# ---------------------------------------------------------------------------
# Public constants (the documented defaults)
# ---------------------------------------------------------------------------
#: The intended public Qubit repository identity. Default destination only —
#: overridable per install via ``update.remote`` / ``update.remote_url``.
QUBIT_REPOSITORY = "cgraph-dev/qubit-voicebridge"
QUBIT_REPOSITORY_URL = f"https://github.com/{QUBIT_REPOSITORY}.git"
#: The canonical public Qubit update URL. :func:`resolve_source` prefers this
#: when no explicit Qubit source is configured, or when the configured clone's
#: origin is legacy-branded. Its value is exactly :data:`QUBIT_REPOSITORY_URL`;
#: callers must never point the updater at Hermes or OpenClaw.
QUBIT_UPDATE_URL = "https://github.com/cgraph-dev/qubit-voicebridge.git"

DEFAULT_REMOTE_NAME = "origin"
DEFAULT_BRANCH = "main"
DEFAULT_CHANNEL = "stable"
CHANNELS = ("stable", "beta", "canary")

#: Typed code returned when an explicitly configured update *source URL* names
#: a legacy assistant.
LEGACY_SOURCE_URL_CODE = "UPDATE_LEGACY_SOURCE_URL"
INVALID_SOURCE_URL_CODE = "UPDATE_SOURCE_URL_INVALID"
INVALID_CHANNEL_CODE = "UPDATE_CHANNEL_INVALID"

#: The legacy-branding guard. Applied ONLY to an explicit update *source URL*;
#: a git remote *name* (the clone's own ``origin``) is always allowed. A
#: legacy-branded remote *URL* is not refused — it is overridden by the
#: canonical Qubit URL in :func:`resolve_source`.
LEGACY_URL_TOKENS = ("hermes", "nousresearch", "nous", "openclaw")

LEGACY_SOURCE_URL_POLICY = (
    "update source URLs must point at Qubit-owned code; a URL naming legacy "
    "assistant branding (matched {token!r}) is refused. Configure "
    "update.remote_url to the Qubit repository "
    f"({QUBIT_REPOSITORY}) or leave update.remote at the default "
    f"'{DEFAULT_REMOTE_NAME}' remote — the clone's own origin is always allowed."
)

#: Stable release tag: strict SemVer ``vMAJOR.MINOR.PATCH`` with no suffix.
#: (The major is capped at three digits so a CalVer tag can never pass as a
#: stable release; mirrors the upstream reference's stable-tag authority.)
STABLE_TAG_PATTERN = r"^v(?:0|[1-9]\d{0,2})\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$"
STABLE_TAG_RE = re.compile(STABLE_TAG_PATTERN)

#: Beta release tag: SemVer incl. an optional pre-release/build suffix
#: (e.g. ``v1.2.0-rc.1``, ``v1.2.0-beta.2``, ``v1.2.0+build.7``).
BETA_TAG_PATTERN = (
    r"^v(?:0|[1-9]\d{0,2})\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
BETA_TAG_RE = re.compile(BETA_TAG_PATTERN)

#: Accepted git URL schemes for an explicit update source URL.
ALLOWED_SCHEMES = ("https", "http", "ssh", "git", "file")

#: Public source forges where a public repository clones anonymously.
PUBLIC_HOSTS = frozenset({
    "github.com", "gitlab.com", "codeberg.org", "bitbucket.org",
    "git.sr.ht", "hg.sr.ht", "gitea.com", "notabug.org", "git.disroot.org",
})
PUBLIC_HOST_SUFFIXES = (".github.com", ".gitlab.com", ".codeberg.org")

_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")
_SCP_RE = re.compile(
    r"^(?P<user>[^@/:\s]+)@(?P<host>[^/:\s]+):(?P<path>[^\s]+)$")

#: Channel record filename (canonical / write-new).
CHANNEL_FILE = "qubit_update_channel.json"
#: Read-old candidates, tried only when the canonical file is absent/invalid.
#: ``update_channel.json`` is the pre-canonical generic name; both are
#: Qubit-owned (no legacy branding) and live under QUBIT_HOME.
READ_OLD_CHANNEL_FILES = ("update_channel.json",)


# ---------------------------------------------------------------------------
# Config access (tolerant: works with Config objects and plain dicts)
# ---------------------------------------------------------------------------
def _get(cfg: Any, *path: str, default: Any = None) -> Any:
    """Nested lookup over a ``Config`` (``.get(*path)``) or a plain dict."""
    if cfg is None:
        return default
    try:
        if isinstance(cfg, dict):
            node: Any = cfg
            for p in path:
                if not isinstance(node, dict) or p not in node:
                    return default
                node = node[p]
            return node
        getter = getattr(cfg, "get", None)
        if callable(getter):
            return getter(*path, default=default)
    except Exception:  # noqa: BLE001 — policy lookup must never raise
        pass
    return default


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


# ---------------------------------------------------------------------------
# QUBIT_HOME resolution (read/write target for the channel record)
# ---------------------------------------------------------------------------
def _external_default_home() -> Path:
    """The operator's external reference home — never written by Qubit."""
    return Path.home() / ".hermes"


def _is_external_home(path: Any) -> bool:
    try:
        return (Path(path).expanduser().resolve(strict=False) ==
                _external_default_home().resolve(strict=False))
    except OSError:
        return False


def qubit_home(cfg: Any = None) -> Path:
    """Resolve the Qubit-owned runtime home for channel persistence.

    Order: an explicit non-external ``QUBIT_HOME`` (a profile/test sandbox) ->
    a custom ``logging.jsonl_path`` on *cfg* (its ``runtime`` child) -> the
    canonical XDG runtime home. The operator's external ``~/.hermes`` is never
    returned. This never creates a directory (``set_channel`` does, on write).
    """
    env = _clean(os.environ.get("QUBIT_HOME"))
    if env and not _is_external_home(env):
        return Path(env).expanduser()
    base = Path(_clean(_get(cfg, "logging", "jsonl_path", default=""))).expanduser() \
        if _clean(_get(cfg, "logging", "jsonl_path", default="")) else None
    if base is not None:
        return base if base.name == "runtime" else base / "runtime"
    try:
        from .config import canonical_runtime_home

        return canonical_runtime_home()
    except Exception:  # noqa: BLE001
        root = os.environ.get("XDG_STATE_HOME") or str(
            Path.home() / ".local" / "state")
        return Path(root) / "qubit-voicebridge" / "runtime"


# ---------------------------------------------------------------------------
# URL classification / redaction / validation
# ---------------------------------------------------------------------------
def _looks_like_scp(value: str) -> bool:
    return bool(_SCP_RE.match(value)) and "://" not in value


def _looks_like_url(value: str) -> bool:
    if "://" in value:
        return bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value))
    return _looks_like_scp(value)


def _looks_like_path(value: str) -> bool:
    if not value:
        return False
    if value.startswith(("/", "./", "../", "~")):
        return True
    return (os.sep in value or "/" in value) and not _looks_like_url(value)


def classify_source(value: Any) -> str:
    """Classify a source value as ``"remote-name"`` / ``"url"`` / ``"path"``."""
    v = _clean(value)
    if not v:
        return "remote-name"
    if _looks_like_url(v):
        return "url"
    if _looks_like_path(v):
        return "path"
    return "remote-name"


def redact_url(url: Any) -> str:
    """Return *url* with any embedded userinfo (``user:token@``) stripped.

    Never returns or logs credentials. Query ``token=``/``password=`` values
    are redacted too. On any parse failure a conservative regex strips a
    ``scheme://userinfo@`` prefix.
    """
    raw = _clean(url)
    if not raw:
        return ""
    redacted = raw
    try:
        parts = urlsplit(raw)
        if parts.scheme and parts.netloc:
            netloc = parts.netloc
            if "@" in netloc:
                netloc = "***@" + netloc.rsplit("@", 1)[1]
            redacted = parts._replace(netloc=netloc).geturl()
    except ValueError:
        redacted = raw
    if "@" in redacted and "://" in redacted:
        head, _, tail = redacted.partition("://")
        if "@" in tail and "/" not in tail.split("@", 1)[0]:
            rest = tail.split("@", 1)[1]
            redacted = f"{head}://***@{rest}"
    redacted = re.sub(r"(?i)([?&](?:token|access_token|password|apikey|"
                      r"api_key)=)[^&#\s]+", r"\1***", redacted)
    return redacted


def _well_formed_host(host: str) -> bool:
    if not host:
        return False
    if host in ("localhost",) or host.endswith(".local"):
        return True
    if ":" in host:  # IPv6 literal
        return True
    return bool(_HOST_RE.match(host))


def validate_remote_url(url: Any) -> Tuple[bool, str]:
    """Strictly validate an explicit update *source URL*.

    Returns ``(ok, reason)``. ``reason`` always contains the URL only in
    redacted form; credentials are never echoed. This accepts git URL shapes
    (``https``/``http``/``ssh``/``git``/``file`` and scp-style
    ``user@host:path``) and rejects anything that is not an absolute URL
    (a bare remote name or a local path is not a URL — see
    :func:`classify_source`).
    """
    raw = _clean(url)
    if not raw:
        return False, "empty update source URL"
    if _looks_like_scp(raw):
        m = _SCP_RE.match(raw)
        if not m or not m.group("host") or not m.group("path"):
            return False, f"malformed scp-style git URL ({redact_url(raw)})"
        return True, "valid scp-style git URL (SSH; requires a key)"
    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        return False, f"unparseable update source URL: {exc}"
    if not parts.scheme:
        return False, ("not an absolute URL (no scheme); configure a git "
                       "remote name or a local path instead")
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        return False, (f"unsupported update source scheme {parts.scheme!r}; "
                       f"allowed: {', '.join(ALLOWED_SCHEMES)}")
    if parts.scheme.lower() == "file":
        if not parts.path:
            return False, "file:// URL has no path"
        return True, "valid file:// URL (local source)"
    host = parts.hostname or ""
    if not host:
        return False, f"update source URL has no host ({redact_url(raw)})"
    if not _well_formed_host(host):
        return False, f"update source URL has a malformed host: {host!r}"
    return True, f"valid {parts.scheme.lower()} update source URL"


def _legacy_token(url: Any) -> Optional[str]:
    """The first legacy-branding token in a source URL, or None."""
    low = _clean(url).lower()
    for token in LEGACY_URL_TOKENS:
        if token in low:
            return token
    return None


def _points_at_qubit_repo(url: Any) -> bool:
    """True when *url* names the Qubit repository (any git URL shape)."""
    low = _clean(url).lower().rstrip("/")
    if not low:
        return False
    if low.endswith(".git"):
        low = low[:-4]
    return low.rstrip("/").endswith(QUBIT_REPOSITORY.lower())


def _git_remote_url(remote_name: Any, repo: Any = None) -> Optional[str]:
    """The actual URL of a git remote, via ``git remote get-url`` (no shell).

    Returns ``None`` when git is unavailable, the remote is unknown, or the
    command fails. This never runs a shell and never mutates the checkout; it
    is the default lookup used by :func:`resolve_source` when no injectable
    ``remote_url_lookup`` is supplied.
    """
    import subprocess  # local import: keep this a cheap leaf at import time

    argv = ["git"]
    if repo:
        argv.extend(["-C", str(repo)])
    argv.extend(["remote", "get-url", _clean(remote_name)])
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    text = (proc.stdout or "").strip()
    return text.splitlines()[0].strip() if text else None


# ---------------------------------------------------------------------------
# Anonymous vs authenticated updates
# ---------------------------------------------------------------------------
def _is_private_host(host: str) -> bool:
    h = (host or "").lower()
    if h in ("localhost",) or h.endswith(".local") or h.endswith(".internal"):
        return True
    if h.startswith("127.") or h.startswith("10.") or h.startswith("192.168."):
        return True
    m = re.match(r"^172\.(\d{1,3})\.", h)
    if m and 16 <= int(m.group(1)) <= 31:
        return True
    if ":" in h:  # IPv6 loopback/unique-local
        return h in ("::1",) or h.startswith("fc") or h.startswith("fd")
    return "." not in h


def anonymous_update_supported(remote_url: Any) -> bool:
    """Heuristic: will this source update without the user's credentials?

    Policy (documented in ``docs/UPDATE.md``):

    * A **public forge** (``PUBLIC_HOSTS``) over ``http(s)`` with **no embedded
      credentials** clones/pulls anonymously -> ``True``.
    * A **local path / file://** source needs no remote auth -> ``True``.
    * ``ssh``/``git``/scp sources, a URL carrying userinfo (a token), and a
      private/self-hosted/local host -> ``False``: the user must supply their
      own git credential helper or a token in the environment. The updater
      never embeds or prints that token.
    """
    raw = _clean(remote_url)
    if not raw:
        return True
    if raw.startswith(("file://", "/", "./", "../", "~")):
        return True
    if _looks_like_scp(raw):
        return False
    try:
        parts = urlsplit(raw)
    except ValueError:
        return False
    scheme = (parts.scheme or "").lower()
    if scheme in ("ssh", "git"):
        return False
    if scheme not in ("http", "https"):
        return False
    if parts.username or parts.password:
        return False
    host = (parts.hostname or "").lower()
    if not host:
        return False
    if host in PUBLIC_HOSTS or host.endswith(PUBLIC_HOST_SUFFIXES):
        return True
    if _is_private_host(host):
        return False
    return False


# ---------------------------------------------------------------------------
# Channel policy
# ---------------------------------------------------------------------------
def is_valid_channel(channel: Any) -> bool:
    return _clean(channel).lower() in CHANNELS


def release_tag_support(channel: Any = DEFAULT_CHANNEL) -> Dict[str, Any]:
    """Whether a channel follows a version **tag** or the **branch head**.

    * ``stable`` — follows version **tags** matching ``STABLE_TAG_PATTERN``
      (strict ``vMAJOR.MINOR.PATCH``); a stable subscriber never receives an
      untagged commit.
    * ``beta`` — follows version **tags** matching ``BETA_TAG_PATTERN``
      (SemVer incl. an ``-rc.N`` / ``-beta.N`` / ``+build`` suffix).
    * ``canary`` — follows the **branch head** (tip of ``update.branch``),
      tag-gating is intentionally disabled.

    Raises ``ValueError`` for an unknown channel (fail closed; the caller
    persists only values from :data:`CHANNELS`).
    """
    ch = _clean(channel).lower() or DEFAULT_CHANNEL
    if ch not in CHANNELS:
        raise ValueError(f"unknown update channel: {channel!r} "
                         f"(valid: {', '.join(CHANNELS)})")
    if ch == "canary":
        return {
            "channel": ch,
            "follow": "branch",
            "branch": DEFAULT_BRANCH,
            "tag_pattern": None,
            "detail": (f"canary tracks the branch head "
                       f"({DEFAULT_BRANCH}); every commit is eligible and no "
                       f"version tag is required."),
        }
    if ch == "beta":
        return {
            "channel": ch,
            "follow": "tag",
            "branch": DEFAULT_BRANCH,
            "tag_pattern": BETA_TAG_PATTERN,
            "detail": ("beta tracks version tags with an optional "
                       "pre-release/build suffix (e.g. v1.2.0-rc.1)."),
        }
    return {
        "channel": ch,
        "follow": "tag",
        "branch": DEFAULT_BRANCH,
        "tag_pattern": STABLE_TAG_PATTERN,
        "detail": ("stable tracks strict vMAJOR.MINOR.PATCH release tags; "
                   "untagged branch commits are never delivered."),
    }


def is_stable_tag(tag: Any) -> bool:
    return bool(STABLE_TAG_RE.fullmatch(_clean(tag)))


def is_beta_tag(tag: Any) -> bool:
    return bool(BETA_TAG_RE.fullmatch(_clean(tag)))


def _prerelease_key(pre: str) -> tuple:
    """SemVer pre-release ordering key (build metadata is ignored)."""
    parts = []
    for ident in str(pre or "").split("."):
        if ident.isdigit():
            parts.append((0, int(ident), ""))
        else:
            parts.append((1, 0, ident))
    return tuple(parts)


def tag_sort_key(tag: Any) -> tuple:
    """A total-order key for a version tag.

    ``vMAJOR.MINOR.PATCH`` numerically, then a release sorts above a
    pre-release of the same core (SemVer §11); build metadata is ignored.
    """
    t = _clean(tag)
    if t[:1].lower() == "v":
        t = t[1:]
    core = t.split("+", 1)[0]
    pre = ""
    if "-" in core:
        core, pre = core.split("-", 1)
    nums = core.split(".")

    def _int(i: int) -> int:
        return int(nums[i]) if len(nums) > i and nums[i].isdigit() else 0

    return (_int(0), _int(1), _int(2), 0 if pre else 1, _prerelease_key(pre))


def parse_remote_tags(output: Any) -> list:
    """Tag names from ``git ls-remote --tags`` output.

    Strips the ``refs/tags/`` prefix and an annotated-tag ``^{}`` peel marker,
    de-duplicates, and preserves the remote's ordering. Non-tag refs (for
    example a ``refs/heads/`` line a tolerant runner returns) are ignored.
    """
    tags: list = []
    seen = set()
    for line in str(output or "").splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        ref = parts[1]
        if not ref.startswith("refs/tags/"):
            continue
        name = ref[len("refs/tags/"):]
        if name.endswith("^{}"):
            name = name[:-3]
        if name and name not in seen:
            seen.add(name)
            tags.append(name)
    return tags


def select_latest_tag(channel: Any, tags: Any) -> Optional[str]:
    """Newest tag for *channel* among *tags*, or ``None`` when none match.

    ``stable`` matches only strict ``vMAJOR.MINOR.PATCH`` tags; ``beta`` also
    accepts a pre-release/build suffix; ``canary`` follows the branch head, so
    this returns ``None`` by policy.
    """
    ch = _clean(channel).lower() or DEFAULT_CHANNEL
    if ch == "canary":
        return None
    pattern = BETA_TAG_RE if ch == "beta" else STABLE_TAG_RE
    matched = [str(t) for t in (tags or []) if pattern.fullmatch(_clean(t))]
    if not matched:
        return None
    return max(matched, key=tag_sort_key)


def select_ref(channel: Any = None, tag: Any = None, tags: Any = None,
               branch: Any = None) -> Dict[str, Any]:
    """Select the effective git ref for a channel (or an explicit tag).

    Precedence: an explicit *tag* always wins; ``canary`` follows the branch
    head; ``stable``/``beta`` select the newest tag matching the channel's
    pattern. When the channel follows tags but none match, the concrete ref
    falls back to *branch* (so updates remain possible until a release tag
    exists), with ``ref_kind == "branch"`` and ``resolve_latest_tag`` true.

    Pure for a valid channel; an unknown channel propagates the ``ValueError``
    from :func:`release_tag_support`.
    """
    support = release_tag_support(channel or DEFAULT_CHANNEL)
    resolved_branch = (_clean(branch) or str(support.get("branch") or "")
                       or DEFAULT_BRANCH)
    explicit = _clean(tag)
    if explicit:
        return {"ref": explicit, "ref_kind": "tag", "pinned": True,
                "channel": support["channel"], "resolve_latest_tag": False,
                "matched_tag": None,
                "detail": f"explicit tag {explicit!r} selected"}
    if support["follow"] != "tag":
        return {"ref": resolved_branch, "ref_kind": "branch", "pinned": False,
                "channel": support["channel"], "resolve_latest_tag": False,
                "matched_tag": None,
                "detail": (f"channel {support['channel']!r} follows the branch "
                           f"head {resolved_branch!r}")}
    latest = select_latest_tag(support["channel"], tags)
    if latest:
        return {"ref": latest, "ref_kind": "tag", "pinned": False,
                "channel": support["channel"], "resolve_latest_tag": True,
                "matched_tag": latest,
                "detail": (f"channel {support['channel']!r} selected the newest "
                           f"matching tag {latest!r}")}
    return {"ref": resolved_branch, "ref_kind": "branch", "pinned": False,
            "channel": support["channel"], "resolve_latest_tag": True,
            "matched_tag": None,
            "detail": (f"channel {support['channel']!r} found no matching tag; "
                       f"falling back to the branch head {resolved_branch!r}")}


def _channel_paths(cfg: Any) -> Tuple[Path, list]:
    home = qubit_home(cfg)
    return home, [home / CHANNEL_FILE,
                  *[home / n for n in READ_OLD_CHANNEL_FILES]]


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def channel_record(cfg: Any = None) -> Dict[str, Any]:
    """This install's persisted channel record, or ``{}`` when unset.

    Returns ``{"channel", "path", "source", "updated_at"}``. The canonical
    file (``qubit_update_channel.json``) is read first (write-new); the
    read-old candidate is consulted only when the canonical file is absent or
    invalid. Falls back to a configured ``update.channel`` key. Never reads or
    writes anything outside QUBIT_HOME or the config mapping.
    """
    _home, paths = _channel_paths(cfg)
    for path in paths:
        if not path.is_file():
            continue
        data = _load_json(path)
        ch = _clean(data.get("channel")).lower()
        if is_valid_channel(ch):
            return {"channel": ch, "path": str(path), "source": "file",
                    "updated_at": data.get("updated_at")}
    cfg_ch = _clean(_get(cfg, "update", "channel", default="")).lower()
    if is_valid_channel(cfg_ch):
        return {"channel": cfg_ch, "path": None, "source": "config"}
    return {}


def resolve_channel(cfg: Any = None) -> str:
    """The effective channel: persisted record -> ``update.channel`` -> stable."""
    record = channel_record(cfg)
    ch = _clean(record.get("channel")).lower()
    if is_valid_channel(ch):
        return ch
    return DEFAULT_CHANNEL


def set_channel(cfg: Any, channel: Any) -> Dict[str, Any]:
    """Persist the channel for this install under QUBIT_HOME (write-new).

    Writes ``qubit_update_channel.json`` atomically (0600, no secrets) and
    records only the channel + timestamp. Raises ``ValueError`` for an unknown
    channel. The source tree is never written.
    """
    ch = _clean(channel).lower()
    if not is_valid_channel(ch):
        raise ValueError(f"unknown update channel: {channel!r} "
                         f"(valid: {', '.join(CHANNELS)})")
    home, _paths = _channel_paths(cfg)
    path = home / CHANNEL_FILE
    record = {
        "schema": 1,
        "kind": "qubit-update-channel",
        "channel": ch,
        "updated_at": _utc_iso(),
    }
    home.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    try:
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
    except OSError:
        pass
    os.replace(tmp, path)
    return {"ok": True, "channel": ch, "path": str(path), "record": record,
            "detail": f"update channel set to {ch!r} under QUBIT_HOME"}


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------
def resolve_source(cfg: Any = None, override: Any = None, *,
                   remote_url_lookup: Any = None,
                   repo: Any = None) -> Dict[str, Any]:
    """Resolve the effective update source/ref policy.

    The canonical public repository is :data:`QUBIT_UPDATE_URL`
    (``https://github.com/cgraph-dev/qubit-voicebridge.git``), channel
    ``stable``, branch ``main``. Resolution order (first non-empty wins):

    1. explicit ``override`` (a local **path**, a **URL**, or a remote **name**);
    2. ``cfg update.remote_url`` (a full URL);
    3. an explicitly configured ``cfg update.remote`` — a remote **name** that
       is honored as-is, or a full URL validated/guarded like (2). A *name*
       whose actual URL (via ``git remote get-url``, injectable and never a
       shell) carries legacy branding is overridden by the canonical URL;
    4. the default ``origin`` remote when ``update.remote`` is unset: used when
       its actual URL already points at the Qubit repo, overridden by the
       canonical URL when it is legacy-branded or not Qubit-owned;
    5. otherwise :data:`QUBIT_UPDATE_URL` (the canonical Qubit URL).

    A legacy-named ``origin`` is therefore **overridden** by the canonical URL,
    never accepted as the update source. ``cfg update.branch`` (default
    ``main``) and the optional ``cfg update.tag`` select the ref; the channel
    selects whether tags or the branch head are followed
    (:func:`release_tag_support`); canary ignores a configured tag by policy.

    Returns a dict with at least ``remote`` (the URL or remote name the git
    verbs accept), ``branch``, ``channel``, ``follow``, ``url``, and
    ``source_kind`` (one of ``explicit`` / ``remote_url`` / ``remote_name`` /
    ``origin_qubit`` / ``canonical``). ``kind`` classifies the candidate
    (``remote-name`` / ``url`` / ``path``). A refused *explicit URL* returns
    ``ok=False`` with a typed ``code`` (``UPDATE_LEGACY_SOURCE_URL`` /
    ``UPDATE_SOURCE_URL_INVALID``), the exact ``policy``, and
    ``fallback_remote`` (``origin``). URLs are always returned redacted.
    """
    channel = resolve_channel(cfg)
    support = release_tag_support(channel)
    branch = _clean(_get(cfg, "update", "branch", default=DEFAULT_BRANCH)) \
        or DEFAULT_BRANCH
    configured_tag = _clean(_get(cfg, "update", "tag", default="")) or None
    # canary follows the branch head by policy; a configured tag is ignored.
    tag = configured_tag if support["follow"] == "tag" else None

    remote_url_cfg = _clean(_get(cfg, "update", "remote_url", default=""))
    remote_name_key = _get(cfg, "update", "remote", default=None)
    remote_name_cfg = _clean(remote_name_key)
    remote_name_configured = bool(remote_name_cfg)

    def _lookup(name: str) -> Optional[str]:
        lookup = remote_url_lookup
        if lookup is None:
            return _git_remote_url(name, repo)
        try:
            return lookup(name)
        except Exception:  # noqa: BLE001 — a failed probe is "unknown"
            return None

    raw_override = _clean(override)
    candidate = ""
    source_kind = ""
    origin_url: Optional[str] = None

    if raw_override:
        candidate = raw_override
        source_kind = "explicit"
    elif remote_url_cfg:
        candidate = remote_url_cfg
        source_kind = "remote_url"
    elif remote_name_configured:
        # A configured remote NAME is authoritative; only a legacy-branded
        # actual URL is replaced by the canonical Qubit URL.
        origin_url = _lookup(remote_name_cfg)
        if origin_url and _legacy_token(origin_url):
            candidate = QUBIT_UPDATE_URL
            source_kind = "canonical"
        else:
            candidate = remote_name_cfg
            source_kind = "remote_name"
    else:
        # No configured source: the default origin is used only when it already
        # points at Qubit; a legacy origin (or an unknown/foreign one) falls
        # back to the canonical Qubit URL.
        origin_url = _lookup(DEFAULT_REMOTE_NAME)
        if origin_url and _legacy_token(origin_url):
            candidate = QUBIT_UPDATE_URL
            source_kind = "canonical"
        elif origin_url and _points_at_qubit_repo(origin_url):
            candidate = DEFAULT_REMOTE_NAME
            source_kind = "origin_qubit"
        else:
            candidate = QUBIT_UPDATE_URL
            source_kind = "canonical"

    kind = classify_source(candidate)
    known_url = ""
    if kind == "url":
        known_url = redact_url(candidate)
    elif origin_url:
        known_url = redact_url(origin_url)

    result: Dict[str, Any] = {
        "ok": True,
        "kind": kind,
        "remote": candidate,
        "remote_url": known_url if kind == "url" else None,
        "url": known_url,
        "source_kind": source_kind,
        "branch": branch,
        "tag": tag,
        "channel": channel,
        "follow": support["follow"],
        "ref": tag or branch,
        "ref_kind": "tag" if tag else "branch",
        "pinned": bool(tag),
        "resolve_latest_tag": bool(support["follow"] == "tag" and not tag),
        "code": "",
        "policy": "",
        "detail": "",
    }

    if kind == "url":
        redacted = redact_url(candidate)
        ok, reason = validate_remote_url(candidate)
        if not ok:
            result.update(
                ok=False, code=INVALID_SOURCE_URL_CODE,
                detail=(f"invalid update source URL ({redacted}): {reason}"),
                fallback_remote=DEFAULT_REMOTE_NAME)
            return result
        token = _legacy_token(candidate)
        if token:
            result.update(
                ok=False, code=LEGACY_SOURCE_URL_CODE,
                policy=LEGACY_SOURCE_URL_POLICY.format(token=token),
                detail=(f"refused update source URL ({redacted}): legacy "
                        f"assistant branding ({token!r})"),
                fallback_remote=DEFAULT_REMOTE_NAME)
            return result
        if source_kind == "canonical":
            result["detail"] = (
                f"using the canonical Qubit update source ({redacted})")
        else:
            result["detail"] = f"explicit update source URL accepted ({redacted})"
    elif kind == "path":
        result["detail"] = "local repository/bundle path source"
    else:
        # A remote NAME is intentionally never resolved to a URL here: the
        # clone's own origin may point anywhere, including a user's mirror.
        if source_kind == "origin_qubit":
            result["detail"] = (
                "the clone's own origin already points at the Qubit repository")
        else:
            result["detail"] = "git remote-name source"
    return result


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "QUBIT_REPOSITORY", "QUBIT_REPOSITORY_URL", "QUBIT_UPDATE_URL",
    "DEFAULT_REMOTE_NAME",
    "DEFAULT_BRANCH", "DEFAULT_CHANNEL", "CHANNELS",
    "LEGACY_SOURCE_URL_CODE", "INVALID_SOURCE_URL_CODE", "INVALID_CHANNEL_CODE",
    "LEGACY_SOURCE_URL_POLICY", "STABLE_TAG_PATTERN", "BETA_TAG_PATTERN",
    "CHANNEL_FILE", "READ_OLD_CHANNEL_FILES",
    "classify_source", "redact_url", "validate_remote_url",
    "anonymous_update_supported", "release_tag_support", "is_stable_tag",
    "is_beta_tag", "tag_sort_key", "parse_remote_tags", "select_latest_tag",
    "select_ref", "is_valid_channel", "qubit_home", "channel_record",
    "resolve_channel", "set_channel", "resolve_source",
]
