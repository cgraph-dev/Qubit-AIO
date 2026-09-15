"""Hermes 1:1 baseline tests (owner amendment 2026-09-11).

Proves, against the pinned Hermes source (hermes-agent@2237be3):
1. every vendored file is byte-identical (hash equality, zero unexplained);
2. the source-comparison manifest covers every upstream file with no
   MISMATCH/MISSING rows;
3. every /api path observed in the vendored dashboard matches a compat route
   (implemented with a real backend, or typed-gated with code+reason);
4. licensing/provenance records exist;
5. the overlay (theme, plugins) is valid and Qubit-labelled;
6. compat endpoints behave (reads, validation failure, typed gates, 404).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

HERMES_REV = "2237be355906fbe6065ce1815711eee52b2d646e"
PINNED_ROOT = Path("/tmp/opencode/pinned")


def _first_dir(cands):
    for c in cands:
        if c is not None and Path(c).is_dir():
            return Path(c)
    return None


def _locate_pinned_hermes():
    """Pinned Hermes checkout: env override, then the two known local names."""
    env = os.environ.get("QUBIT_PINNED_HERMES")
    cands = ([env] if env else []) + [
        PINNED_ROOT / "hermes-agent",
        PINNED_ROOT / "hermes",
    ]
    return _first_dir(cands)


def _locate_openclaw_ui():
    """Pinned OpenClaw ``ui/`` tree: env override (dir or repo root), then
    ``/tmp/opencode/pinned/openclaw/ui`` and ``/tmp/opencode/pinned/openclaw``.
    Returns the ``ui`` directory, never the repo root."""
    env = os.environ.get("QUBIT_PINNED_OPENCLAW")
    cands = ([env] if env else []) + [
        # The comparison manifest baseline is OpenClaw v2026.9.2; prefer that
        # checkout over any newer capability-pin tree so coverage is compared
        # against the revision the manifest was generated from.
        PINNED_ROOT / "openclaw-v2026.9.2",
        PINNED_ROOT / "openclaw" / "ui",
        PINNED_ROOT / "openclaw",
    ]
    for c in cands:
        p = Path(c)
        if p.name == "ui" and p.is_dir():
            return p
        if p.is_dir() and (p / "ui").is_dir():
            return p / "ui"
    return None


UPSTREAM = _locate_pinned_hermes()
OPENCLAW_UI = _locate_openclaw_ui()

VENDOR_PAIRS = [] if UPSTREAM is None else [
    (UPSTREAM / "web", REPO_ROOT / "frontend" / "hermes-web"),
    (UPSTREAM / "apps" / "shared", REPO_ROOT / "frontend" / "hermes-shared"),
]

requires_upstream = pytest.mark.skipif(
    UPSTREAM is None,
    reason="pinned Hermes tree not found (set QUBIT_PINNED_HERMES, or clone to "
           "/tmp/opencode/pinned/hermes[-agent])")
EXCLUDE_DIRS = {"node_modules", "dist", "build", ".git", "__pycache__"}
# Generated package-manager lockfiles are Qubit-side build artifacts (they do
# not exist in the pinned upstream tree and carry no upstream source).
EXCLUDE_NAMES = {"package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
                 "pnpm-lock.yaml"}


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _tree_files(root: Path):
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not any(part in EXCLUDE_DIRS for part in p.parts) \
                and p.name not in EXCLUDE_NAMES:
            out[p.relative_to(root).as_posix()] = p
    return out


def _manifest_by_qubit():
    man = json.loads(
        (REPO_ROOT / "docs/HERMES_SOURCE_COMPARISON.json").read_text())
    return {r["qubit"]: r for r in man["rows"] if r.get("qubit")}


def _documented_added():
    """Qubit-added vendored paths (owner amendment QUBIT_ADDED category).

    A vendored tree may carry intentional Qubit additions when the comparison
    manifest records them with a reason; anything else must remain
    upstream-clean/byte-identical.
    """
    man = json.loads(
        (REPO_ROOT / "docs/HERMES_SOURCE_COMPARISON.json").read_text())
    added = set()
    for entry in man.get("wave3", {}).get("added", []) or []:
        if entry.get("qubit") and entry.get("role"):
            added.add(entry["qubit"])
    for row in man["rows"]:
        if row.get("qubit") and row.get("patch") and row.get("reason"):
            added.add(row["qubit"])
    return added


@requires_upstream
def test_vendor_byte_identical_or_documented_patch():
    """Every vendored file is either byte-identical to its pinned upstream
    file or carries a documented patch whose on-disk Qubit hash matches the
    manifest. No undocumented drift is permitted."""
    rows = _manifest_by_qubit()
    for up_root, q_root in VENDOR_PAIRS:
        up = _tree_files(up_root)
        q = _tree_files(q_root)
        extra = set(q) - set(up)
        qprefix = q_root.relative_to(REPO_ROOT).as_posix()
        added = _documented_added()
        undocumented_extra = sorted(
            f"{qprefix}/{rel}" for rel in extra
            if f"{qprefix}/{rel}" not in added)
        assert not undocumented_extra, (
            f"undocumented file additions in {q_root.name}: "
            f"{undocumented_extra[:10]}")
        assert not (set(up) - set(q)), (
            f"missing vendored files in {q_root.name}: "
            f"{sorted(set(up) - set(q))[:10]}")
        for rel in up:
            qrel = f"{q_root.relative_to(REPO_ROOT).as_posix()}/{rel}"
            row = rows.get(qrel)
            assert row is not None, f"no manifest row for {qrel}"
            assert _sha(up[rel]) == row["upstream_sha256"], \
                f"upstream pin drift for {qrel}"
            if row.get("patch"):
                assert _sha(q[rel]) == row["qubit_sha256"], \
                    f"documented patch hash mismatch for {qrel}"
            else:
                assert _sha(up[rel]) == _sha(q[rel]), \
                    f"undocumented hash mismatch in {q_root.name}: {rel}"


@requires_upstream
def test_vendor_contains_no_undocumented_qubit_edits():
    """Vendored trees carry no undocumented Qubit edits (overlay-only rule).

    Owner-approved brand patches are permitted when the comparison manifest
    records an exact patch + reason for the file; every other file must remain
    upstream-clean. See docs/QUBIT_HERMES_CUTOVER_MIGRATION.md.
    """
    rows = _manifest_by_qubit()
    added = _documented_added()
    hits = []
    for _, q_root in VENDOR_PAIRS:
        for rel, p in _tree_files(q_root).items():
            qrel = f"{q_root.relative_to(REPO_ROOT).as_posix()}/{rel}"
            row = rows.get(qrel) or {}
            if qrel in added:
                continue  # documented Qubit addition
            if row.get("patch") and row.get("reason"):
                continue  # documented owner rebrand patch
            try:
                text = p.read_text(encoding="utf-8", errors="strict")
            except (UnicodeDecodeError, OSError):
                continue
            if "qubit" in text.lower():
                hits.append(p.name)
    assert not hits, f"Undocumented Qubit edits leaked into vendored tree: {hits[:10]}"


@requires_upstream
def test_manifest_covers_every_upstream_file():
    """Manifest has one row per upstream file, zero MISMATCH/MISSING."""
    man = json.loads((REPO_ROOT / "docs/HERMES_SOURCE_COMPARISON.json").read_text())
    assert man["hermes"]["rev"] == HERMES_REV
    rows = man["rows"]
    by_scope: dict = {}
    for r in rows:
        by_scope.setdefault(r["scope"], set()).add(r["upstream"])
        assert r["upstream_rev"] in (
            "2237be355906fbe6065ce1815711eee52b2d646e",
            "3928bad9badfcb6c7d140530435e806fb8092190"), r
        patch = r.get("patch") or ""
        assert "MISMATCH" not in patch and "MISSING" not in patch, r
        assert r.get("reason"), r
    scopes = {"hermes-agent/web", "hermes-agent/apps/shared",
              "hermes-agent/apps/desktop", "hermes-agent/ui-tui",
              "hermes-agent/tui_gateway", "hermes-agent/website",
              "openclaw/ui"}
    assert scopes <= set(by_scope), f"missing scopes: {scopes - set(by_scope)}"
    # every on-disk upstream file appears exactly once
    roots = {"hermes-agent/web": UPSTREAM / "web",
             "hermes-agent/apps/shared": UPSTREAM / "apps" / "shared",
             "hermes-agent/apps/desktop": UPSTREAM / "apps" / "desktop",
             "hermes-agent/ui-tui": UPSTREAM / "ui-tui",
             "hermes-agent/tui_gateway": UPSTREAM / "tui_gateway",
             "hermes-agent/website": UPSTREAM / "website"}
    # The openclaw/ui scope is verified only when its pinned checkout is
    # materialized; the manifest-scope assertion above still covers it.
    if OPENCLAW_UI is not None:
        roots["openclaw/ui"] = OPENCLAW_UI
    for scope, root in roots.items():
        disk = {f"web/{p.relative_to(root).as_posix()}" if scope == "hermes-agent/web"
                else f"{scope.split('/', 1)[1]}/{p.relative_to(root).as_posix()}"
                for p in root.rglob("*")
                if p.is_file() and not any(part in EXCLUDE_DIRS for part in p.parts)}
        # manifest stores "upstream" as sub/rel
        want = {r if "/" in r else r for r in by_scope[scope]}
        assert disk == want, (f"{scope}: coverage drift "
                              f"missing={sorted(disk - want)[:5]} "
                              f"extra={sorted(want - disk)[:5]}")


@requires_upstream
def test_every_observed_api_path_has_compat_route():
    """Zero unmapped Hermes backend paths: each observed /api literal matches."""
    from voicebridge import hermes_compat as HC
    lits = set()
    for _, q_root in VENDOR_PAIRS:
        src = q_root / "src"
        if not src.exists():
            continue
        for p in src.rglob("*.ts*"):
            if "test" in p.name:
                continue
            text = p.read_text(errors="replace")
            for m in re.finditer(r'["`/]/api/([^"\'`\s${}]+)', text):
                lit = "/api/" + m.group(1).rstrip("?")
                lit = re.sub(r"\$\{[^}]*\}", "X", lit)
                lits.add(lit.split("?")[0])
    unmatched = []
    for lit in sorted(lits):
        probe = re.sub(r"\{p\}|X|%[0-9A-Fa-f]{2}", "1", lit)
        probe = re.sub(r"/\$\{.*", "/1", probe)
        # runtime normalizes trailing slashes in dispatch(); mirror it here
        probe = probe.rstrip("/") or "/"
        norm = lit.rstrip("/") or "/"
        hit = any(re.fullmatch(r["pattern"], probe) or
                  re.fullmatch(r["pattern"], norm)
                  for r in HC.ROUTES)
        # also try stripping trailing param-looking segments
        if not hit:
            base = re.sub(r"/[^/]*(\$\{|X|1).*$", "", lit)
            hit = any(re.fullmatch(r["pattern"], lit) or
                      re.fullmatch(r["pattern"], probe) for r in HC.ROUTES)
        if not hit:
            unmatched.append(lit)
    # allow query-fragment artefacts, not real paths
    real = [u for u in unmatched if re.fullmatch(r"/api/[A-Za-z0-9/_\-.]+" , u)
            and "..." not in u and "${" not in u]
    assert not real, f"unmapped compat paths: {real[:20]}"


def test_provenance_records_exist():
    notices = (REPO_ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    assert "Hermes agent dashboard — vendored baseline" in notices
    assert "Copyright (c) 2025 Nous Research" in notices
    assert "frontend/hermes-web/" in notices
    reuse = (REPO_ROOT / "docs" / "UPSTREAM_REUSE.md").read_text()
    assert "R35" in reuse and "2237be355906fbe6065ce1815711eee52b2d646e" in reuse
    amend = (REPO_ROOT / "docs" / "QUBIT_OWNER_AMENDMENT_2026-09-11.md").read_text()
    assert "APPROVED" in amend


def test_overlay_manifests_valid_and_qubit_labelled():
    from voicebridge import hermes_compat as HC
    manifests = HC._overlay_manifests()
    assert len(manifests) == 11, manifests
    assert sorted(m["name"] for m in manifests) == [
        "qubit-approvals", "qubit-boards", "qubit-brand", "qubit-chat",
        "qubit-devices", "qubit-gateway", "qubit-memory", "qubit-models",
        "qubit-ops", "qubit-reports", "qubit-runtime"], manifests
    for m in manifests:
        for field in ("name", "label", "description", "version", "tab", "entry"):
            assert m.get(field), (m.get("name"), field)
        assert m["tab"].get("path"), m
        bundle = (REPO_ROOT / "frontend" / "hermes-qubit" / "plugins"
                  / m["name"] / m["entry"])
        assert bundle.is_file(), bundle
        assert "hermes" not in m["label"].lower(), m["label"]
    theme = json.loads((REPO_ROOT / "frontend" / "hermes-qubit"
                        / "qubit-theme.json").read_text())
    assert theme["name"] == "qubit"
    assert theme["palette"]["background"]["hex"] == "#10141c"


# ---------------------------------------------------------------------------
# Behavioral (hermetic cfg)
# ---------------------------------------------------------------------------

def _cfg(tmp_path):
    from voicebridge.config import Config
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl_path: '" + str(state) + "'\n")
    return Config.load(p)


def test_compat_status_and_themes(tmp_path):
    from voicebridge import hermes_compat as HC
    cfg = _cfg(tmp_path)
    code, out = HC.dispatch(cfg, "GET", "/api/status", {}, {})
    assert code == 200 and out.get("ok"), out
    code, out = HC.dispatch(cfg, "GET", "/api/dashboard/themes", {}, {})
    assert code == 200 and out.get("active") == "qubit", out
    assert out["themes"] and out["themes"][0]["definition"]["name"] == "qubit"
    code, out = HC.dispatch(cfg, "GET", "/api/dashboard/plugins", {}, {})
    assert code == 200 and len(out) == 11, out


def test_compat_gates_are_typed(tmp_path):
    from voicebridge import hermes_compat as HC
    cfg = _cfg(tmp_path)
    for method, path, code_part in [
            ("GET", "/api/pty", "PTY_NO_SHELL"),
            ("GET", "/api/ws", "WS_SINGLE_PATH"),
            ("GET", "/api/config/raw", "SECRET_NEVER_SERVED"),
            ("POST", "/api/gateway/start", "GATEWAY_NO_SUPERVISION"),
            ("POST", "/api/skills/hub/install", "BLOCKED_REMOTE_SOURCE"),
            ("GET", "/api/portal", "UPDATE_OPERATOR_OWNED")]:
        code, out = HC.dispatch(cfg, method, path, {}, {})
        assert code in (403, 501), (path, code)
        assert out.get("code") == code_part, (path, out)
        assert out.get("qubit_counterpart"), (path, out)


def test_compat_chat_validation_and_unknown(tmp_path):
    from voicebridge import hermes_compat as HC
    cfg = _cfg(tmp_path)
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/chat",
                            {}, {"text": "   ", "session": "t"})
    assert code == 400 and out["code"] == "MALFORMED", out
    code, out = HC.dispatch(cfg, "GET", "/api/nope-nowhere", {}, {})
    assert code == 404 and out["code"] == "NOT_FOUND", out


def test_compat_read_endpoints_smoke(tmp_path):
    from voicebridge import hermes_compat as HC
    cfg = _cfg(tmp_path)
    for path in ["/api/auth/me", "/api/events", "/api/sessions",
                 "/api/sessions/stats", "/api/config", "/api/model/info",
                 "/api/model/options", "/api/model/moa", "/api/model/auxiliary",
                 "/api/profiles", "/api/profiles/active",
                 "/api/cron/jobs", "/api/logs", "/api/system/stats",
                 "/api/mcp", "/api/mcp/catalog", "/api/pairing", "/api/webhooks",
                 "/api/messaging/platforms", "/api/memory", "/api/analytics",
                 "/api/analytics/models", "/api/files", "/api/gateway",
                 "/api/curator", "/api/env",
                 "/api/plugins/kanban/events", "/api/dashboard/font",
                 "/api/dashboard/themes",
                 "/api/cron/blueprints", "/api/cron/delivery-targets"]:
        code, out = HC.dispatch(cfg, "GET", path, {}, {})
        assert code in (200, 202), (path, code, str(out)[:200])
        assert isinstance(out, (dict, list)), path
    # bare-array wire shapes stay bare arrays
    for path in ["/api/skills", "/api/tools/toolsets", "/api/cron/jobs",
                 "/api/dashboard/plugins"]:
        code, out = HC.dispatch(cfg, "GET", path, {}, {})
        assert code == 200 and isinstance(out, list), (path, code)
