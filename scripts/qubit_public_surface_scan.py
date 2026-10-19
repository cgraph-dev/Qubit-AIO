#!/usr/bin/env python3
"""Deterministic, read-only PUBLIC-SURFACE branding scan (wave 22).

Owner directive: Qubit must expose **zero** legacy brand (``Hermes`` /
``OpenClaw`` / ``NousResearch`` / ``NousPortal`` / ``Nous Research``) on any
public surface. This scanner checks only surfaces a user/operator can actually
reach and prints a single JSON report with per-surface findings and a final
``public_leaks`` count.

Surfaces checked (all read-only; nothing in this repo or in the live services is
mutated):

``cli``            rendered ``qubit --help`` / ``qubit update --help`` output.
``routes``         the served compat route table + the generated /openapi.json
                   document (a pure function, no server needed).
``update_receipt`` a hermetic ``update_manager.full_apply`` against a disposable
                   tmp git repo, scanning the returned receipt, the persisted
                   receipt JSON, and every captured log line.
``voice``          the update voice intents' spoken strings / aliases / help.
``bundles``        ``frontend/qubit-dist`` + ``frontend/qubit-app`` built assets.
``source_strings`` every public desktop/web source file
                   (``qubit_runtime/apps/desktop/src/**``,
                   ``qubit_runtime/apps/desktop/electron/**``,
                   ``frontend/qubit-web/src/**``) plus the update modules and
                   the CLI / voice update blocks. Each hit is classified:
                   user-facing URL/route/prose is a leak; internal
                   wire/filesystem/module identifiers, guards, comments and
                   test fixtures are allowlisted and reported separately.

Anything in the allowlist (legal attribution, frozen records, generated
artifacts, wire identifiers, documented safety guards) is reported under
``allowlisted`` and is **not** counted as a public leak.

Usage::

    .venv/bin/python scripts/qubit_public_surface_scan.py
    .venv/bin/python scripts/qubit_public_surface_scan.py --json
    .venv/bin/python scripts/qubit_public_surface_scan.py --skip-receipt
"""
from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

QUBIT = REPO_ROOT / ".venv" / "bin" / "qubit"
PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

SCHEMA = "qubit-public-surface-scan/1"

#: Help invocations scanned by the ``cli`` surface. ``qubit agent|uninstall|auth|
#: gateway|proxy --help`` were added in Task 14: the original scan only rendered
#: ``--help`` / ``update --help``, which is exactly what let the remaining CLI
#: prose leaks (S2–S6) report as zero.
CLI_HELP_ARGS: Tuple[Tuple[str, ...], ...] = (
    ("--help",),
    ("agent", "--help"),
    ("auth", "--help"),
    ("gateway", "--help"),
    ("proxy", "--help"),
    ("uninstall", "--help"),
    ("update", "--help"),
)
#: Wire/contract identifiers that legitimately render in help text (env vars,
#: session-token header, IPC channels, module specifiers). Preserved as-is per
#: BRAND.md, so a rendered occurrence is allowlisted, never counted as a leak.
CLI_WIRE_IDENTIFIER_RE = re.compile(
    r"\bHERMES_[A-Z0-9_]*\b"
    r"|\bX-Hermes-Session-Token\b"
    r"|\bhermes:[A-Za-z0-9_.:-]+\b"
    r"|@hermes/[A-Za-z0-9_./-]+")

#: The legacy public-brand tokens (case-insensitive).
LEGACY_RE = re.compile(
    r"hermes|openclaw|nousresearch|nousportal|nous\s+research", re.IGNORECASE)
#: Standalone prose product words (not glued into an identifier). Case-sensitive
#: on purpose: a capitalized ``Hermes``/``Nous``/``OpenClaw`` in a string is
#: user-facing prose; lowercase ``hermes`` in ``hermes-agent`` / ``.hermes`` /
#: ``hermes-icon`` is an internal identifier.
PROSE_RE = re.compile(
    r"(?<![A-Za-z0-9_\-./@:\\#])(Hermes|Nous|OpenClaw|NousResearch|NousPortal)"
    r"(?![A-Za-z0-9_\-./@:\\#])")
#: Upstream URLs.
UPSTREAM_URL_RE = re.compile(
    r"https?://[^\s\"'`<>)\]]*(?:nousresearch\.com|hermes-agent\.)"
    r"[^\s\"'`<>)\]]*", re.IGNORECASE)
#: Bare upstream domain references (no scheme).
UPSTREAM_DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9-]+\.)*nousresearch\.com\b", re.IGNORECASE)
#: The upstream social handle.
HANDLE_RE = re.compile(r"@NousResearch\b")
#: A legacy-token-bearing identifier (module specifier, env var, path, scheme,
#: IPC channel, storage key, exe name, ...). Lazy head, greedy tail.
TS_TOKEN_RE = re.compile(
    r"[A-Za-z0-9_./:@$#<>\\-]*?(?:hermes|openclaw|nousresearch|nousportal)"
    r"[A-Za-z0-9_./:@$#<>\\-]*", re.IGNORECASE)

DIST = REPO_ROOT / "frontend" / "qubit-dist"
APP_OVERLAY = REPO_ROOT / "frontend" / "qubit-app"

#: Public desktop/web source roots. Everything under these is scanned for
#: user-facing strings; internal wire/filesystem/module identifiers are
#: allowlisted, and test/E2E/story files are reported as ``test_fixture``.
DESKTOP_SRC = REPO_ROOT / "qubit_runtime" / "apps" / "desktop" / "src"
DESKTOP_ELECTRON = REPO_ROOT / "qubit_runtime" / "apps" / "desktop" / "electron"
WEB_SRC = REPO_ROOT / "frontend" / "qubit-web" / "src"
TS_SOURCE_DIRS = (DESKTOP_SRC, DESKTOP_ELECTRON, WEB_SRC)
TS_SOURCE_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
#: Test / E2E / story paths are not shipped product surfaces.
TS_NON_PUBLIC_PATH_RE = re.compile(
    r"(\.test\.|\.spec\.|\.stories\.|/__tests__/|/e2e/|/tests?/)", re.IGNORECASE)
#: A legacy URL/identifier in an explicit refusal/guard context is allowlisted.
TS_GUARD_RE = re.compile(
    r"(refus\w*|forbid\w*|deny|reject\w*|reference[- ]service|operator[- ]owned)",
    re.IGNORECASE)
#: A legacy-named API route literal in source/bundles is a public leak.
TS_API_LEAK_RE = re.compile(r"/api/(?:plugins/)?hermes", re.IGNORECASE)
#: A bare upstream domain used as a host-classifier predicate (``.endsWith``,
#: ``.includes``, ``host ===`` ...) is an internal transport identifier, not a
#: user-facing URL.
TS_HOST_MATCH_RE = re.compile(
    r"(?:\.(?:endsWith|startsWith|includes|match|test)\s*\(\s*"
    r"|host(?:name)?\s*===?\s*)['\"`]\.?$")

#: The generated /openapi.json alias marker: omit legacy paths (already branded).
OPENAPI_LEGACY_BRAND = re.compile(r"hermes|nous|openclaw", re.IGNORECASE)

#: Documented safety guards whose legacy literal is an internal identifier, not
#: product copy. Keyed by repo-relative path.
PY_GUARD_PATTERNS: Dict[str, Tuple[str, ...]] = {
    "voicebridge/update_manager.py": (
        r"hermes-dashboard\.service",
    ),
    "voicebridge/update_channel.py": (
        r"LEGACY_URL_TOKENS\s*=",
        r"Path\.home\(\)\s*/\s*['\"]\.hermes['\"]",
        r"external\s+``?~/\.hermes",
    ),
    "voicebridge/update_receipt.py": (
        r"_LEGACY_BRAND_RE\s*=",
    ),
}

#: The documented allowlist (reported, never counted).
ALLOWLISTED_CATEGORIES: Dict[str, List[str]] = {
    "legal_attribution": [
        "LICENSE", "THIRD_PARTY_NOTICES.md",
    ],
    "frozen_records": [
        "docs/archive/**", "docs/differential/INTERFACE.md",
    ],
    "generated_artifacts": [
        "docs/generated/**",
    ],
    "wire_identifiers": [
        "HERMES_HOME", "X-Hermes-Session-Token", "HERMES_SUPERVISED_CHILD",
        "HERMES_UPDATE_* / HERMES_DESKTOP_* / HERMES_DASHBOARD_* env names",
        "window.__HERMES_* / __HERMES_PLUGINS__ / "
        "__HERMES_PLUGIN_SDK__ globals",
        "hermes:* IPC channel names", "com.nousresearch.hermes app id",
        "@hermes/plugin-sdk / @hermes/shared / @/hermes / @/types/hermes / "
        "./windows-hermes-path module specifiers",
        "python module/package specifiers (hermes_home, ensure_hermes_home)",
        "filesystem/executable names (.hermes, hermes-home, hermes.exe, "
        "hermes-setup.exe, hermes-agent venv, .hermes-update-*)",
        "custom URL schemes (hermes://, hermes-media://)",
        "DOM ids / CSS classes / data-* attrs / localStorage keys "
        "(hermes-bots, data-hermes-*, hermes.desktop.*)",
        "plugin/module ids (hermes-bots, hermes-achievements dir names)",
    ],
    "source_guards": [
        "update_channel.LEGACY_URL_TOKENS",
        "update_receipt._LEGACY_BRAND_RE",
        "update_manager hermes-dashboard.service refusal",
        "external ~/.hermes reference-home refusal",
        "TS legacy URL / reference-service refusal (refuse|forbid|deny|"
        "reference-service|operator-owned)",
    ],
    "comments": [
        "code comments referencing upstream issues/attribution",
    ],
    "test_fixtures": [
        "test/E2E/story file strings (not shipped product surfaces)",
    ],
    "third_party_attribution": [
        "bundled plugin author/license comments",
    ],
}

UPDATE_MODULES = (
    "voicebridge/update_manager.py",
    "voicebridge/update_channel.py",
    "voicebridge/update_lock.py",
    "voicebridge/update_receipt.py",
    "voicebridge/update_validation.py",
    "voicebridge/update_config_migration.py",
    "voicebridge/update_handoff.py",
)

UPDATE_VOICE_IDS = ("update_status", "update_check", "update_apply",
                    "update_rollback")


# ---------------------------------------------------------------------------
# finding helpers
# ---------------------------------------------------------------------------
def _leak(surface: str, token: str, *, file: str = "", line: int = 0,
          text: str = "", classification: str = "leak") -> Dict[str, Any]:
    return {"surface": surface, "classification": classification,
            "token": token, "file": file, "line": line,
            "text": " ".join(str(text).split())[:200]}


def _allow(observed: Dict[str, int], surface: str, classification: str,
           token: str) -> None:
    key = f"{surface}:{classification}:{token.lower()}"
    observed[key] = observed.get(key, 0) + 1


def _hits(text: str) -> List[str]:
    out: List[str] = [m.group(0) for m in LEGACY_RE.finditer(text)]
    out += [m.group(0) for m in UPSTREAM_URL_RE.finditer(text)]
    out += [m.group(0) for m in HANDLE_RE.finditer(text)]
    # Dedupe, keep deterministic order.
    return sorted(set(out))


# ---------------------------------------------------------------------------
# a. CLI help output
# ---------------------------------------------------------------------------
def _cli_launcher() -> List[str]:
    """Command that renders the Qubit CLI, independent of a repo-local ``.venv``.

    Production layout uses ``.venv/bin/qubit``; a source checkout without a
    virtualenv falls back to the running interpreter importing ``voicebridge.cli``
    from ``REPO_ROOT`` (which is the subprocess cwd). Both are read-only.
    """
    if QUBIT.exists():
        return [str(QUBIT)]
    return [sys.executable, "-c",
            "import sys; from voicebridge.cli import main; sys.exit(main())"]


def scan_cli() -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    findings: List[Dict[str, Any]] = []
    allow: Dict[str, int] = {}
    launcher = _cli_launcher()
    for argv in CLI_HELP_ARGS:
        try:
            proc = subprocess.run([*launcher, *argv], capture_output=True,
                                  text=True, timeout=90,
                                  cwd=str(REPO_ROOT))
        except (OSError, subprocess.TimeoutExpired) as exc:
            findings.append(_leak("cli", "<run-error>",
                                  text=f"{exc}", classification="error"))
            continue
        text = proc.stdout + proc.stderr
        label = "qubit " + " ".join(argv)
        for line in text.splitlines():
            if not (LEGACY_RE.search(line) or UPSTREAM_URL_RE.search(line)
                    or HANDLE_RE.search(line)):
                continue
            # Mask preserved wire/contract identifiers, then flag only what
            # remains — this keeps HERMES_* env vars allowlisted while catching
            # product copy like a bare ``hermes`` command or a ``~/.hermes`` path.
            masked = CLI_WIRE_IDENTIFIER_RE.sub(
                lambda m: " " * len(m.group(0)), line)
            remaining = _hits(masked)
            if remaining:
                for token in remaining:
                    findings.append(_leak("cli", token, file=label, text=line,
                                          classification="help_text"))
            else:
                for token in _hits(line):
                    _allow(allow, "cli", "wire_identifier", token)
    return findings, allow


# ---------------------------------------------------------------------------
# b. served routes + generated openapi document
# ---------------------------------------------------------------------------
def scan_routes() -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    from voicebridge import qubit_compat as qc

    findings: List[Dict[str, Any]] = []
    allow: Dict[str, int] = {}
    for row in qc.ROUTES:
        pattern = str(row.get("pattern") or "")
        low = pattern.lower()
        if "/api/hermes" in low:
            findings.append(_leak(
                "routes", "/api/hermes", file="qubit_compat.ROUTES",
                text=f"{row.get('method')} {pattern}",
                classification="legacy_api_path"))
            continue
        if "openclaw" in low:
            findings.append(_leak(
                "routes", "openclaw", file="qubit_compat.ROUTES",
                text=f"{row.get('method')} {pattern}",
                classification="openclaw_api_path"))
            continue
        for token in _hits(pattern):
            findings.append(_leak(
                "routes", token, file="qubit_compat.ROUTES",
                text=f"{row.get('method')} {pattern}",
                classification="legacy_path_token"))

    try:
        code, doc = qc.h_openapi(None, {}, {}, {})
        blob = json.dumps(doc, sort_keys=True)
        for token in _hits(blob):
            findings.append(_leak(
                "routes", token, file="qubit_compat.h_openapi",
                text=f"status={code}", classification="openapi_document"))
    except Exception as exc:  # noqa: BLE001
        findings.append(_leak("routes", "<openapi-error>", text=str(exc),
                              classification="error"))
    return findings, allow


# ---------------------------------------------------------------------------
# c. hermetic full_apply: receipt + persisted receipt + logs
# ---------------------------------------------------------------------------
class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover
        try:
            self.lines.append(self.format(record))
        except Exception:  # noqa: BLE001
            pass


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr}")
    return proc.stdout.strip()


def _run_runner(argv, timeout):  # injected as the updater's git runner
    try:
        proc = subprocess.run(list(argv), capture_output=True, text=True,
                              timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"{argv[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", "command timed out"


def _build_isolated_tree(base: Path) -> Dict[str, Any]:
    home = base / "home"
    qubit_home = base / "qubit-home"
    for value in (home, qubit_home, base / "xdg-config", base / "xdg-state",
                  base / "xdg-data", base / "xdg-cache"):
        value.mkdir(parents=True, exist_ok=True)

    origin = base / "origin.git"
    seed = base / "seed"
    checkout = base / "checkout"
    bump = base / "bump"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True,
                   capture_output=True)
    subprocess.run(["git", "init", "-b", "main", str(seed)], check=True,
                   capture_output=True)
    (seed / "VERSION").write_text("1\n")
    (seed / "pyproject.toml").write_text(
        "[project]\nname = \"qubit-scan\"\nversion = \"0.0.1\"\n")
    (seed / "frontend/qubit-dist").mkdir(parents=True, exist_ok=True)
    (seed / "frontend/qubit-dist/index.html").write_text("<html/>\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "v1")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "origin", "main")
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    subprocess.run(["git", "clone", "-b", "main", str(origin), str(checkout)],
                   check=True, capture_output=True)
    subprocess.run(["git", "clone", "-b", "main", str(origin), str(bump)],
                   check=True, capture_output=True)
    (bump / "VERSION").write_text("2\n")
    _git(bump, "add", "-A")
    _git(bump, "commit", "-m", "v2")
    _git(bump, "push", "origin", "main")

    cfg_file = base / "xdg-config" / "qubit-voicebridge" / "voice.yaml"
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    backup_dir = base / "backups"
    data = {
        "update": {
            "repo_root": str(checkout),
            "remote": "origin",
            "branch": "main",
            "backup_dir": str(backup_dir),
            "config_path": str(cfg_file),
            "deps_command": ["/bin/sh", "-c", "true"],
            "skip_validation": True,
            "handoff": False,
        },
        "logging": {"level": "INFO", "jsonl": True,
                    "jsonl_path": str(base / "logs" / "events.jsonl")},
    }
    import yaml

    cfg_file.write_text(yaml.safe_dump(data, sort_keys=False))
    return {"cfg_file": cfg_file, "data": data, "checkout": checkout,
            "backup_dir": backup_dir, "base": base}


def scan_update_receipt() -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    from voicebridge.config import Config
    from voicebridge import update_manager as UM
    from voicebridge import logging_setup
    from voicebridge import update_receipt as UR

    findings: List[Dict[str, Any]] = []
    allow: Dict[str, int] = {}

    env_backup = {k: os.environ.get(k) for k in (
        "HOME", "QUBIT_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME",
        "XDG_DATA_HOME", "XDG_CACHE_HOME", "QUBIT_UPDATE_DISABLED",
        "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL")}

    scan_tmp = Path("/tmp/opencode")
    if not scan_tmp.is_dir():
        scan_tmp = Path(tempfile.gettempdir())
    base = Path(tempfile.mkdtemp(prefix="qubit-public-surface-scan-",
                                 dir=str(scan_tmp)))
    logger = logging.getLogger("voicebridge")
    capture = _Capture()
    capture.setFormatter(logging.Formatter("%(levelname)s %(name)s | %(message)s"))
    logger.addHandler(capture)
    old_level = logger.level
    prev_event_file = logging_setup._EVENT_FILE
    try:
        tree = _build_isolated_tree(base)
        logging_setup.setup(level="INFO", jsonl=True,
                            jsonl_path=base / "logs" / "events.jsonl")
        for k, v in _env_overrides_for(tree).items():
            os.environ[k] = v
        cfg = Config(data=tree["data"], path=tree["cfg_file"])
        receipt = UM.full_apply(cfg, confirm=True, source="origin",
                                runner=_run_runner, actor="scan:public-surface")

        blobs: List[Tuple[str, str]] = [
            ("returned_receipt", json.dumps(receipt, default=str, sort_keys=True)),
        ]
        rdir = UR.receipt_dir(cfg)
        if rdir.is_dir():
            for path in sorted(rdir.glob("*.json")):
                try:
                    blobs.append((f"persisted_receipt:{path.name}",
                                  path.read_text(encoding="utf-8")))
                except OSError:
                    pass
        for line in capture.lines:
            blobs.append(("log_line", line))
        log_file = base / "logs" / "events.jsonl"
        if log_file.is_file():
            try:
                blobs.append(("events.jsonl", log_file.read_text("utf-8")))
            except OSError:
                pass

        for label, text in blobs:
            for token in _hits(text):
                findings.append(_leak(
                    "update_receipt", token, file=label, text=text[:400],
                    classification="receipt_or_log"))
    except Exception as exc:  # noqa: BLE001
        findings.append(_leak("update_receipt", "<run-error>", text=str(exc),
                              classification="error"))
    finally:
        logger.removeHandler(capture)
        logger.setLevel(old_level)
        logging_setup._EVENT_FILE = prev_event_file
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(base, ignore_errors=True)
    return findings, allow


def _env_overrides_for(tree: Dict[str, Any]) -> Dict[str, str]:
    base = tree["base"]
    return {
        "HOME": str(base / "home"),
        "QUBIT_HOME": str(base / "qubit-home"),
        "XDG_CONFIG_HOME": str(base / "xdg-config"),
        "XDG_STATE_HOME": str(base / "xdg-state"),
        "XDG_DATA_HOME": str(base / "xdg-data"),
        "XDG_CACHE_HOME": str(base / "xdg-cache"),
        "QUBIT_UPDATE_DISABLED": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(base / "gitconfig"),
    }


# ---------------------------------------------------------------------------
# d. voice update intents
# ---------------------------------------------------------------------------
def scan_voice() -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    from voicebridge import voice_commands as vc

    findings: List[Dict[str, Any]] = []
    allow: Dict[str, int] = {}
    for entry_id, entry in vc.REGISTRY.items():
        if entry_id not in UPDATE_VOICE_IDS:
            continue
        fields = {
            "id": getattr(entry, "id", ""),
            "title": getattr(entry, "title", ""),
            "backend": getattr(entry, "backend", ""),
            "help": getattr(entry, "help", ""),
            "summary": getattr(entry, "summary", ""),
            "aliases": list(getattr(entry, "aliases", ()) or ()),
            "examples": list(getattr(entry, "examples", ()) or ()),
        }
        blob = json.dumps(fields, sort_keys=True)
        for token in _hits(blob):
            findings.append(_leak("voice", token, file=f"voice_commands:{entry_id}",
                                  text=blob, classification="spoken_string"))
    return findings, allow


# ---------------------------------------------------------------------------
# e. built bundles
# ---------------------------------------------------------------------------
def _bundle_files(root: Path):
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.endswith(".map"):
            continue
        if path.suffix in (".js", ".html", ".css", ".json"):
            yield path


def _is_comment_line(stripped: str) -> bool:
    return (stripped.startswith("//") or stripped.startswith("/*")
            or stripped.startswith("*") or stripped.startswith("<!--"))


def scan_bundles() -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    findings: List[Dict[str, Any]] = []
    allow: Dict[str, int] = {}
    flagged_dirs: set = set()
    for root in (DIST, APP_OVERLAY):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_dir():
                continue
            name = path.name
            if not LEGACY_RE.search(name):
                continue
            if name in flagged_dirs:
                continue
            flagged_dirs.add(name)
            tok = re.search(r"[A-Za-z0-9._-]*(?:hermes|openclaw|nous)"
                            r"[A-Za-z0-9._-]*", name, re.IGNORECASE)
            findings.append(_leak(
                "bundles", tok.group(0) if tok else LEGACY_RE.search(name).group(0),
                file=str(path.relative_to(REPO_ROOT)), text=str(
                    path.relative_to(REPO_ROOT)),
                classification="plugin_path"))
        for path in _bundle_files(root):
            rel_s = str(path.relative_to(REPO_ROOT))
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                if not LEGACY_RE.search(line) \
                        and not UPSTREAM_URL_RE.search(line) \
                        and not UPSTREAM_DOMAIN_RE.search(line):
                    continue
                stripped = line.strip()
                if stripped.startswith("//") or stripped.startswith("/*") \
                        or stripped.startswith("*"):
                    # Third-party attribution / provenance comment.
                    for token in _hits(line):
                        if "http" in line:
                            _allow(allow, "bundles", "provenance_comment", token)
                        else:
                            _allow(allow, "bundles", "comment", token)
                    continue
                low = line.lower()
                # Public UI plugin id / route / asset path.
                m_path = re.search(
                    r"hermes-achievements|/plugins/hermes|/api/plugins/hermes"
                    r"|/api/hermes|openclaw", line, re.IGNORECASE)
                if m_path:
                    findings.append(_leak(
                        "bundles", m_path.group(0), file=rel_s, line=i,
                        text=line, classification="plugin_path"))
                elif UPSTREAM_URL_RE.search(line) or UPSTREAM_DOMAIN_RE.search(line) \
                        or HANDLE_RE.search(line) or PROSE_RE.search(line):
                    toks = sorted(set(
                        [x.group(0) for x in UPSTREAM_URL_RE.finditer(line)]
                        + [x.group(0) for x in UPSTREAM_DOMAIN_RE.finditer(line)]
                        + [x.group(0) for x in HANDLE_RE.finditer(line)]
                        + [x.group(0) for x in PROSE_RE.finditer(line)]))
                    for token in toks:
                        findings.append(_leak(
                            "bundles", token, file=rel_s, line=i, text=line,
                            classification="public_brand"))
                else:
                    for token in _hits(line):
                        _allow(allow, "bundles", "wire_identifier", token)
    return findings, allow


# ---------------------------------------------------------------------------
# f. source user-facing strings
# ---------------------------------------------------------------------------
def _docstring_ranges(source: str) -> List[Tuple[int, int]]:
    ranges: List[Tuple[int, int]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ranges
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)
            if not body:
                continue
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value,
                                                          ast.Constant) \
                    and isinstance(first.value.value, str):
                ranges.append((first.value.lineno, first.value.end_lineno
                               or first.value.lineno))
    return ranges


def _in_ranges(line: int, ranges: List[Tuple[int, int]]) -> bool:
    return any(a <= line <= b for a, b in ranges)


def _line_ranges_for_call(source: str, func: str, ids=()) -> List[Tuple[int, int]]:
    """Line ranges of ``func(...)`` calls (optionally filtered by first arg)."""
    ranges: List[Tuple[int, int]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ranges
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == func:
            if ids:
                if not node.args or not isinstance(node.args[0], ast.Constant) \
                        or node.args[0].value not in ids:
                    continue
            ranges.append((node.lineno, node.end_lineno or node.lineno))
    return ranges


def _cli_update_ranges(source: str) -> List[Tuple[int, int]]:
    ranges: List[Tuple[int, int]] = []
    lines = source.splitlines()
    start = end = None
    for i, line in enumerate(lines, 1):
        if start is None and 'add("update"' in line:
            start = i
        elif start is not None and 'add("browser"' in line:
            end = i - 1
            break
    if start and end:
        ranges.append((start, end))
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ranges
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_cmd_update":
            ranges.append((node.lineno, node.end_lineno or node.lineno))
    return ranges


def _scan_py_source(rel: str, line_ranges: Optional[List[Tuple[int, int]]] = None
                    ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    findings: List[Dict[str, Any]] = []
    allow: Dict[str, int] = {}
    path = REPO_ROOT / rel
    if not path.is_file():
        findings.append(_leak("source_strings", "<missing>", file=rel,
                              classification="error"))
        return findings, allow
    source = path.read_text(encoding="utf-8", errors="replace")
    lines = source.splitlines()
    docstrings = _docstring_ranges(source)
    guards = PY_GUARD_PATTERNS.get(rel, ())
    for i, line in enumerate(lines, 1):
        if line_ranges is not None and not any(a <= i <= b for a, b in line_ranges):
            continue
        if not LEGACY_RE.search(line):
            continue
        stripped = line.strip()
        if stripped.startswith("#"):
            for token in _hits(line):
                _allow(allow, "source_strings", "comment", token)
            continue
        if _in_ranges(i, docstrings):
            for token in _hits(line):
                _allow(allow, "source_strings", "docstring", token)
            continue
        # A guard may span lines (e.g. ``_LEGACY_BRAND_RE = re.compile(`` on the
        # previous line), so match the pattern against a small backward window.
        window = "\n".join(lines[max(0, i - 4):i])
        if any(re.search(pat, window) for pat in guards):
            for token in _hits(line):
                _allow(allow, "source_strings", "guard", token)
            continue
        for token in _hits(line):
            findings.append(_leak("source_strings", token, file=rel, line=i,
                                  text=line, classification="py_string"))
    return findings, allow


def _merge_spans(spans: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _covered(span: Tuple[int, int], spans: List[Tuple[int, int]]) -> bool:
    return any(a <= span[0] and span[1] <= b for a, b in spans)


def _scan_ts_file(rel: str) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Scan one TS/JS product file.

    Classification (``source_strings`` surface):

    ``upstream_url`` / ``api_path`` / ``public_brand``
        user-facing URL / route / prose -> **counted public leak**.
    ``wire_identifier``
        internal module specifier, env var, filesystem/exe path, URL scheme,
        IPC channel, storage key, DOM/CSS id -> allowlisted, reported.
    ``guard``
        legacy URL / reference-service refusal -> allowlisted, reported.
    ``comment``
        comment line -> allowlisted, reported.
    ``test_fixture``
        any hit in a test/E2E/story file -> allowlisted, reported.
    """
    findings: List[Dict[str, Any]] = []
    allow: Dict[str, int] = {}
    path = REPO_ROOT / rel
    if not path.is_file():
        findings.append(_leak("source_strings", "<missing>", file=rel,
                              classification="error"))
        return findings, allow
    is_test = bool(TS_NON_PUBLIC_PATH_RE.search(rel))
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(lines, 1):
        if not LEGACY_RE.search(line) and not UPSTREAM_DOMAIN_RE.search(line):
            continue
        stripped = line.strip()
        if _is_comment_line(stripped):
            for token in _hits(line):
                _allow(allow, "source_strings", "comment", token)
            continue
        # Character spans covered by an upstream URL / bare upstream domain;
        # tokens inside them must not be double-reported.
        url_spans = [m.span() for m in UPSTREAM_URL_RE.finditer(line)]
        domain_spans = [m.span() for m in UPSTREAM_DOMAIN_RE.finditer(line)]
        spans = _merge_spans(url_spans + domain_spans)

        emitted: set = set()
        for m in UPSTREAM_URL_RE.finditer(line):
            url = m.group(0)
            if url in emitted:
                continue
            emitted.add(url)
            if is_test:
                _allow(allow, "source_strings", "test_fixture", url)
            else:
                findings.append(_leak("source_strings", url, file=rel, line=i,
                                      text=line, classification="upstream_url"))
        for m in UPSTREAM_DOMAIN_RE.finditer(line):
            dom = m.group(0)
            if _covered(m.span(), url_spans):
                continue
            if dom in emitted:
                continue
            emitted.add(dom)
            prefix = line[max(0, m.start() - 60):m.start()]
            if is_test:
                _allow(allow, "source_strings", "test_fixture", dom)
            elif TS_HOST_MATCH_RE.search(prefix):
                _allow(allow, "source_strings", "wire_identifier", dom)
            else:
                findings.append(_leak("source_strings", dom, file=rel, line=i,
                                      text=line, classification="upstream_url"))

        for m in TS_TOKEN_RE.finditer(line):
            if _covered(m.span(), spans):
                continue
            token = m.group(0)
            if TS_GUARD_RE.search(line):
                _allow(allow, "source_strings", "guard", token)
                continue
            if PROSE_RE.search(token):
                if is_test:
                    _allow(allow, "source_strings", "test_fixture", token)
                else:
                    findings.append(_leak(
                        "source_strings", token, file=rel, line=i, text=line,
                        classification="public_brand"))
                continue
            if (TS_API_LEAK_RE.search(line)
                    or "openclaw" in token.lower()):
                if is_test:
                    _allow(allow, "source_strings", "test_fixture", token)
                else:
                    findings.append(_leak(
                        "source_strings", token, file=rel, line=i, text=line,
                        classification="api_path"))
                continue
            _allow(allow, "source_strings", "wire_identifier", token)
    return findings, allow


def _iter_ts_sources() -> List[str]:
    rels: List[str] = []
    for root in TS_SOURCE_DIRS:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in TS_SOURCE_EXTS:
                rels.append(str(path.relative_to(REPO_ROOT)))
    return rels


def _voice_update_ranges() -> List[Tuple[int, int]]:
    rel = "voicebridge/voice_commands.py"
    path = REPO_ROOT / rel
    source = path.read_text(encoding="utf-8", errors="replace")
    return _line_ranges_for_call(source, "_E", ids=UPDATE_VOICE_IDS)


def scan_source_strings() -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    findings: List[Dict[str, Any]] = []
    allow: Dict[str, int] = {}
    for rel in UPDATE_MODULES:
        f, a = _scan_py_source(rel)
        findings += f
        _merge_allow(allow, a)
    for rel, ranges in (
        ("voicebridge/cli.py", _cli_update_ranges(
            (REPO_ROOT / "voicebridge/cli.py").read_text(encoding="utf-8",
                                                         errors="replace"))),
        ("voicebridge/voice_commands.py", _voice_update_ranges()),
    ):
        f, a = _scan_py_source(rel, ranges)
        findings += f
        _merge_allow(allow, a)
    for rel in _iter_ts_sources():
        f, a = _scan_ts_file(rel)
        findings += f
        _merge_allow(allow, a)
    return findings, allow


def _merge_allow(dst: Dict[str, int], src: Dict[str, int]) -> None:
    for k, v in src.items():
        dst[k] = dst.get(k, 0) + v


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------
def _sort_findings(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(findings, key=lambda f: (
        str(f.get("surface")), str(f.get("file")), int(f.get("line") or 0),
        str(f.get("token")), str(f.get("classification"))))


def run_scan(*, skip_receipt: bool = False) -> Dict[str, Any]:
    surfaces: Dict[str, Any] = {}
    allow_observed: Dict[str, int] = {}
    all_findings: List[Dict[str, Any]] = []

    for name, fn in (
        ("cli", scan_cli),
        ("routes", scan_routes),
        ("voice", scan_voice),
        ("bundles", scan_bundles),
        ("source_strings", scan_source_strings),
    ):
        findings, allow = fn()
        findings = _sort_findings(findings)
        surfaces[name] = {"leak_count": len(findings), "findings": findings}
        all_findings += findings
        _merge_allow(allow_observed, allow)

    if not skip_receipt:
        findings, allow = scan_update_receipt()
        findings = _sort_findings(findings)
        surfaces["update_receipt"] = {"leak_count": len(findings),
                                      "findings": findings}
        all_findings += findings
        _merge_allow(allow_observed, allow)
    else:
        surfaces["update_receipt"] = {"leak_count": 0, "findings": [],
                                      "skipped": True}

    all_findings = _sort_findings(all_findings)
    report = {
        "schema": SCHEMA,
        "generated_by": "scripts/qubit_public_surface_scan.py",
        "public_leaks": len(all_findings),
        "surfaces": surfaces,
        "allowlisted": {
            "categories": ALLOWLISTED_CATEGORIES,
            "observed_counts": dict(sorted(allow_observed.items())),
        },
        "leaks": all_findings,
    }
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true",
                        help="print the full JSON report (default: summary + leaks)")
    parser.add_argument("--skip-receipt", action="store_true",
                        help="skip the hermetic update full_apply surface")
    args = parser.parse_args(argv)

    report = run_scan(skip_receipt=args.skip_receipt)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for name, surface in report["surfaces"].items():
            print(f"{name}: {surface['leak_count']} public leak(s)")
        print(f"public_leaks: {report['public_leaks']}")
        if report["leaks"]:
            print("--- leaks ---")
            for item in report["leaks"]:
                loc = item["file"] + (f":{item['line']}" if item["line"] else "")
                print(f"  [{item['surface']}/{item['classification']}] "
                      f"{loc} token={item['token']!r} :: {item['text']}")
        print("--- allowlisted (reported, never counted) ---")
        for category, entries in report["allowlisted"]["categories"].items():
            print(f"  {category}:")
            for entry in entries:
                print(f"    - {entry}")
        print("--- allowlisted observed counts ---")
        for key, count in report["allowlisted"]["observed_counts"].items():
            print(f"  {count:6d}  {key}")
    return 0 if report["public_leaks"] == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
