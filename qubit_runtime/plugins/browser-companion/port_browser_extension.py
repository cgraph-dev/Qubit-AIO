#!/usr/bin/env python3
"""Mechanical port of the browser extension and companion plugin into Qubit.

Reads the pinned upstream tree, applies the rename map from
``docs/BROWSER_EXT_1TO1.md`` §D, and writes both halves into the worktree.
Mechanical on purpose: the same rules run over every file, so nothing is
hand-skipped. Human-reviewed files (the companion's Qubit-adapted modules) are
written separately and are NOT produced here.

Run from the repository root:

    .venv/bin/python qubit_runtime/plugins/browser-companion/port_browser_extension.py /path/to/upstream
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EXT_DEST = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-browser-ext" / "extension"
PLUGIN_DEST = REPO / "qubit_runtime" / "plugins" / "browser-companion"
SKILL_NAME = "browser-companion"

# ── Brand tokens ─────────────────────────────────────────────────────────
# Lowercase spellings that are identifiers of OTHER products (never rebranded).
KEEP_LOWER = (
    "hermes_agent",  # upstream python package name in prose/commands
    "hermes-cli",
    "hermes_cli",
)

# File renames inside the extension tree (basename level, applied recursively).
FILE_RENAMES = {
    "hermes-web-launch.js": "qubit-web-launch.js",
    "hermes-web-launch.css": "qubit-web-launch.css",
    "hermes-web-opening.js": "qubit-web-opening.js",
    "hermes-web-opening.html": "qubit-web-opening.html",
    "hermes-web-opening.css": "qubit-web-opening.css",
    "hermes-web-opening.mjs": "qubit-web-opening.mjs",
    "hermes-client.mjs": "qubit-client.mjs",
    "hermes-webapp.mjs": "qubit-webapp.mjs",
    "hermes-webapp-dialog.mjs": "qubit-webapp-dialog.mjs",
    "hermes-account.mjs": "qubit-account.mjs",
    "hermes-context-lengths.mjs": "qubit-context-lengths.mjs",
    "hermes-context-registry-data.mjs": "qubit-context-registry-data.mjs",
    "hermes-context-sync.mjs": "qubit-context-sync.mjs",
    "hermes-context-windows.mjs": "qubit-context-windows.mjs",
    "hermes-account.test.mjs": "qubit-account.test.mjs",
}


def _asset_rename(name: str) -> str:
    if name.startswith("hermes-"):
        return "qubit-" + name[len("hermes-") :]
    return name


def rename_basename(name: str) -> str:
    if name in FILE_RENAMES:
        return FILE_RENAMES[name]
    return _asset_rename(name)


# ── Text rewriting ───────────────────────────────────────────────────────
# Ordered: the most specific forms first so a later rule never re-rewrites them.
def _camel(match) -> str:
    inner = match.group(1)
    if inner.isupper():
        return "QUBIT_" + inner
    return "Qubit" + inner


# Whole-token replacements that must run BEFORE the generic brand rules, because the
# generic bump would turn `Hermes.` into `Qubit.` in prose that is not ours.
# Upstream's PRODUCT name in user-facing prose lives in `_locales/**` and `lib/locales/**`
# only; the rules below rewrite it consistently in every locale, keys included.
TEXT_RULES = [
    # Wire + storage identifiers (lowercase dotted / camelCase).
    (re.compile(r"\bhermes\.browser\b"), "qubit.browser"),
    (re.compile(r"\bhermes\.browser_companion\b"), "qubit.browser_companion"),
    (re.compile(r"\bhermes-browser-control-v1\b"), "qubit-browser-control-v1"),
    (re.compile(r"\bhermes-browser-control-ticket\b"), "qubit-browser-control-ticket"),
    (re.compile(r"\bhermes-browser-control\b"), "qubit-browser-control"),
    # Extension plugin/global/storage identifiers.
    (re.compile(r"\bhermesBrowser"), "qubitBrowser"),
    (re.compile(r"\bhermesWebapp"), "qubitWebapp"),
    (re.compile(r"\bhermesWeb"), "qubitWeb"),
    (re.compile(r"\bhermesInline"), "qubitInline"),
    (re.compile(r"\bhermesGateway"), "qubitGateway"),
    (re.compile(r"\bhermesDesktop"), "qubitDesktop"),
    (re.compile(r"\bhermesReplay"), "qubitReplay"),
    (re.compile(r"\bhermesLastKnown"), "qubitLastKnown"),
    (re.compile(r"\bhermesLegacy"), "qubitLegacy"),
    (re.compile(r"\bhermesOnboarding"), "qubitOnboarding"),
    (re.compile(r"\bhermesPetdex"), "qubitPetdex"),
    (re.compile(r"\bhermesSessionMenu"), "qubitSessionMenu"),
    (re.compile(r"\bhermesAssist"), "qubitAssist"),
    (re.compile(r"\bhermes_web\b"), "qubit_web"),
    (re.compile(r"\bhermes_browser\b"), "qubit_browser"),
    (re.compile(r"\bhermes_bot_mode\b"), "qubit_bot_mode"),
    (re.compile(r"\bhermes-assist-"), "qubit-assist-"),
    (re.compile(r"'hermes-browser'"), "'qubit-browser'"),
    # i18n KEY names derived from English text (ask_hermes, runtime_ui_*_hermes_*).
    (re.compile(r"_hermes(?=[_.\"])"), "_qubit"),
    (re.compile(r"_hermes\b"), "_qubit"),
    # Any remaining PascalCase identifier: HermesFoo -> QubitFoo.
    (re.compile(r"\bHermes(?=[A-Z0-9])"), "Qubit"),
    # Any remaining lowerCamel identifier: askHermes -> askQubit, hermesClient -> qubitClient,
    # item.hermesContextTokens -> item.qubitContextTokens, dataset.hermesTheme -> qubitTheme.
    (re.compile(r"(?<=[a-z0-9_.$])hermes(?=[A-Z])"), "qubit"),
    (re.compile(r"(?<=[a-z0-9_.$])Hermes(?![a-z])"), "Qubit"),
    # SCREAMING ids in any position: __HERMES_FOO__ -> __QUBIT_FOO__, HERMES_FOO -> QUBIT_FOO.
    (re.compile(r"\bHERMES_([A-Z][A-Z0-9_]*)\b"), r"QUBIT_\1"),
    (re.compile(r"\bHERMES_"), "QUBIT_"),
    # Upstream module paths named in copy/diagnostics (they are core files, not ours to rename,
    # but a Qubit tree has no `hermes_*` module, so the reference follows the same rule).
    (re.compile(r"\bhermes_state_mutations\b"), "qubit_state_mutations"),
    # File names inside strings (imports, manifests, asset references).
    (re.compile(r"\bhermes-web-launch\b"), "qubit-web-launch"),
    (re.compile(r"\bhermes-web-opening\b"), "qubit-web-opening"),
    (re.compile(r"\bhermes-client\b"), "qubit-client"),
    (re.compile(r"\bhermes-webapp-dialog\b"), "qubit-webapp-dialog"),
    (re.compile(r"\bhermes-webapp\b"), "qubit-webapp"),
    (re.compile(r"\bhermes-account\b"), "qubit-account"),
    (re.compile(r"\bhermes-context-lengths\b"), "qubit-context-lengths"),
    (re.compile(r"\bhermes-context-registry-data\b"), "qubit-context-registry-data"),
    (re.compile(r"\bhermes-context-sync\b"), "qubit-context-sync"),
    (re.compile(r"\bhermes-context-windows\b"), "qubit-context-windows"),
    (re.compile(r"\bhermes-browser-extension\b"), "qubit-browser-extension"),
    (re.compile(r"\bhermes-browser-companion\b"), "qubit-browser-companion"),
    (re.compile(r"\bhermes-plugin-guid\b"), "qubit-plugin-guid"),
    (re.compile(r"\bhermes-badge\b"), "qubit-badge"),
    # Assets: any remaining hermes-<name>.<ext> path fragment.
    (re.compile(r"\bhermes-(?=[A-Za-z0-9_-]+\.(?:png|svg|webp|jpg|gif|woff2|css|js|mjs|html))"), "qubit-"),
    # Commands in copy.
    (re.compile(r"\bhermes webapp\b"), "qubit webapp"),
    (re.compile(r"\bhermes plugins\b"), "qubit plugins"),
    (re.compile(r"\bhermes tools\b"), "qubit tools"),
    (re.compile(r"\bhermes dashboard\b"), "qubit dashboard"),
    (re.compile(r"\bhermes config\b"), "qubit config"),
    (re.compile(r"~/\.hermes\b"), "~/.qubit"),
    (re.compile(r"\bhermes-agent\b"), "qubit-agent"),
    (re.compile(r"\bhermes_agent\b"), "qubit_agent"),
    (re.compile(r"\bhermes_([a-z][a-z0-9_]*)\b"), r"qubit_\1"),
    # Product prose — longest first, INCLUDING a trailing period/comma so prose
    # ("... by Hermes." / "Hermesa") is rewritten, never left as "Qubit.".
    (re.compile(r"\bHermes Browser Extension\b"), "Qubit Browser Extension"),
    (re.compile(r"\bHermes Browser\b"), "Qubit Browser"),
    (re.compile(r"\bHermes Control\b"), "Qubit Control"),
    (re.compile(r"\bHermes Assist\b"), "Qubit Assist"),
    (re.compile(r"\bHermes Web\b"), "Qubit Web"),
    (re.compile(r"\bHermes Agent\b"), "Qubit"),
    (re.compile(r"\bHERMES BROWSER EXTENSION\b"), "QUBIT BROWSER EXTENSION"),
    (re.compile(r"\bHERMES AGENT\b"), "QUBIT"),
    # Locale morphology: Slavic/agglutinating locales inflect the product name
    # (Hermesa, Hermesie, Hermesem, Hermesowi). "Qubit" declension suffixes are
    # produced with the same stem rule so copy stays grammatical.
    (re.compile(r"\bHermes(?=(?:owi|ie|em|a|u|om|ami|ach|y|ę|ą|ów|o)\b)"), "Qubit"),
    (re.compile(r"\bHERMES\b"), "QUBIT"),
    (re.compile(r"\bHermes\b"), "Qubit"),
    (re.compile(r"\bhermes\b"), "qubit"),
]

TEXT_SUFFIXES = {
    ".js", ".mjs", ".cjs", ".json", ".html", ".css", ".md", ".txt", ".svg",
    ".yml", ".yaml", ".xml", ".sh", ".cmd", ".map", ".py",
}

# Python module references in the companion's prose/docstrings: `tools.py`, `control_*.py`,
# `hermes-browser-extension/SKILL.md`. Shielded from the brand rules (see rewrite_companion).
MODULE_REF_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\.py\b")
# `tests/<file>.test.mjs` / `tests/pytools/<file>.py` — upstream-only files, referenced in prose.
TESTS_REF_RE = re.compile(r"\btests/[A-Za-z0-9_./-]+/([A-Za-z0-9_.-]+)")
BRAND_RE = re.compile(r"[Hh][Ee][Rr][Mm][Ee][Ss]")


def rewrite(text: str) -> str:
    for pattern, replacement in TEXT_RULES:
        text = pattern.sub(replacement, text)
    return text


def rewrite_companion(text: str) -> str:
    """Same brand rules, then the one exception: Python MODULE names.

    The companion imports sibling modules (``from .tools import ...``) and the
    generic rule would rewrite backticked prose like ``tools.py`` into
    ``.py``. Module-looking tokens are shielded before the brand pass and put
    back after it, so prose keeps referring to the real file names.
    """
    shields: dict[str, str] = {}

    def shield(match: re.Match) -> str:
        token = match.group(0)
        if not BRAND_RE.search(token):
            return token
        key = f"\x00MOD{len(shields)}\x00"
        shields[key] = token
        return key

    shielded = MODULE_REF_RE.sub(shield, text)
    rewritten = rewrite(shielded)
    for key, token in shields.items():
        rewritten = rewritten.replace(key, token)
    return rewritten


def copy_tree(src: Path, dest: Path, *, skip_names: tuple[str, ...] = ()) -> int:
    written = 0
    for entry in sorted(src.rglob("*")):
        rel = entry.relative_to(src)
        if any(part in skip_names for part in rel.parts):
            continue
        if entry.is_dir():
            continue
        target_rel = Path(*[rename_basename(part) for part in rel.parts])
        target = dest / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry.suffix.lower() in TEXT_SUFFIXES:
            try:
                raw = entry.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                shutil.copy2(entry, target)
            else:
                target.write_text(rewrite(raw), encoding="utf-8")
        else:
            shutil.copy2(entry, target)
        written += 1
    return written


# ── Task-shaped rules for the companion half ─────────────────────────────
# After the shared rename, the companion needs four kinds of edit that are
# mechanical *per task* rather than per token: the buffer-scoped, task-scoped
# field renames Qubit already established in this plugin. They are applied to
# the ported companion only, in this order, so a ported module compiles against
# the store it actually gets.
COMPANION_RULES = [
    # The top-level lease table is keyed (session_id, task_id) in Qubit: `turnjob` is the
    # renaming intermediate, folded below.
    (re.compile(r"\bturnjob\b"), "turn_id"),
    # Qubit passes `task_id` to tool handlers, not `job_id` (upstream's field name).
    (re.compile(r"\bjob_id\b"), "task_id"),
    (re.compile(r"\bjobId\b"), "taskId"),
    # Upstream's `data_dir` state facade is Qubit's plugin state (ctx.state.data_dir).
    (re.compile(r"\bstate\.data_dir\b"), "state.data_dir"),
]


def copy_plugin(src: Path, dest: Path) -> int:
    written = 0
    for entry in sorted(src.rglob("*")):
        rel = entry.relative_to(src)
        if entry.is_dir():
            continue
        name = rel.name
        if name == "SKILL.md" and rel.parts[0] == "skills":
            target = dest / "skills" / SKILL_NAME / "SKILL.md"
        elif name == "install.md":
            target = dest / "INSTALL.md"
        elif name == "README.md":
            target = dest / "README.md"
        elif name == "plugin.yaml":
            target = dest / "plugin.yaml"
        else:
            target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry.suffix.lower() in TEXT_SUFFIXES:
            try:
                raw = entry.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                shutil.copy2(entry, target)
                written += 1
                continue
            text = rewrite_companion(raw)

            for pattern, replacement in COMPANION_RULES:
                text = pattern.sub(replacement, text)
            # Upstream's security tests are referenced by name in the ported prose; those
            # files stay upstream (see §G.3), so a backticked reference to them is dropped
            # to its bare file name rather than to a path that no longer exists.
            text = TESTS_REF_RE.sub(r"\1", text)
            target.write_text(text, encoding="utf-8")

        else:
            shutil.copy2(entry, target)
        written += 1
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("upstream", type=Path, help="path to the pinned upstream checkout")
    upstream = parser.parse_args().upstream.expanduser().resolve()
    extension = upstream / "extension"
    companion = upstream / "companion-plugin"
    for source in (extension, companion):
        if not source.is_dir():
            parser.error(f"upstream source directory missing: {source}")
        if source == EXT_DEST or EXT_DEST in source.parents:
            parser.error(f"upstream source is inside the extension destination: {source}")
    if EXT_DEST.exists():
        shutil.rmtree(EXT_DEST)
    EXT_DEST.mkdir(parents=True, exist_ok=True)
    count = copy_tree(extension, EXT_DEST)
    print(f"extension: {count} files -> {EXT_DEST}")

    plugin_count = copy_plugin(companion, PLUGIN_DEST)
    print(f"companion-plugin: {plugin_count} files -> {PLUGIN_DEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
