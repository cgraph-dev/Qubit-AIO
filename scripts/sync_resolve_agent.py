#!/usr/bin/env python3
"""Resolve the agent/ conflicts left by the sync by taking upstream's incoming
change while preserving Qubit-local edits.

The sync (scripts/sync_upstream.py) leaves a file at PRE-sync state whenever its
3-way merge conflicts. These 17 files then break the synced test suite (missing
symbols that upstream moved/added, e.g. agent.lsp.servers.UNTRUSTED_SAFE_SERVERS,
agent.chat_completion_helpers.cap_to_run_budget).

Resolution rule per conflict hunk:
  * take the NEW side (upstream's incoming change);
  * restore the Qubit-local spelling for any identifier the sync FALSELY
    translated. Ground truth: docs/QUBIT_REBRAND_MANIFEST.json env_map has ONE
    entry (HERMES_HOME -> QUBIT_HOME); every other HERMES_* env var is unchanged
    in Qubit's runtime. The sync's context-free HERMES_ -> QUBIT_ rule invented
    QUBIT_STREAM_STALE_TIMEOUT / QUBIT_CODEX_HARD_TIMEOUT_SECONDS / QUBIT_LANGUAGE
    where Qubit's own tree independently still uses the HERMES_* names.

Read-only against upstream; writes only the listed Qubit files.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_upstream as su  # noqa: E402

# False renames the sync's identifier rule invented. Qubit's runtime keeps the
# HERMES_* spelling (env_map canonical entry is HERMES_HOME -> QUBIT_HOME only).
FALSE_RENAME_FIX = {
    "QUBIT_STREAM_STALE_TIMEOUT": "HERMES_STREAM_STALE_TIMEOUT",
    "QUBIT_LOCAL_STREAM_STALE_TIMEOUT": "HERMES_LOCAL_STREAM_STALE_TIMEOUT",
    "QUBIT_CODEX_HARD_TIMEOUT_SECONDS": "HERMES_CODEX_HARD_TIMEOUT_SECONDS",
    "QUBIT_CODEX_TTFB_TIMEOUT_SECONDS": "HERMES_CODEX_TTFB_TIMEOUT_SECONDS",
    "QUBIT_LANGUAGE": "HERMES_LANGUAGE",
}

AGENT_FILES = [
    "agent_init.py", "anthropic_message_convert.py", "chat_completion_helpers.py",
    "codex_runtime.py", "file_safety.py", "i18n.py", "lsp/install.py",
    "lsp/servers.py", "relay_runtime.py", "runtime_cwd.py",
    "secret_sources/command.py", "skill_utils.py", "system_prompt.py",
    "tool_dispatch_helpers.py", "transports/chat_completions.py",
    "turn_explainers.py", "compression_marker.py",
]


def merged_text(up_path: str) -> tuple[str, int]:
    """Re-run the sync merge for one upstream path; return (merged, hunks_left)."""
    qpath = su.route(up_path)
    target = su.QR / qpath
    raw_base = su.git_show(su.PIN_SHA, up_path)
    raw_new = su.git_show(su.BASE_SHA, up_path)
    base = raw_base if raw_base is not None else ""
    t_base, _ = su.translate(base)
    t_new, _ = su.translate(raw_new)
    cur = target.read_text(encoding="utf-8", errors="replace")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "cur").write_text(cur)
        (d / "base").write_text(t_base)
        (d / "new").write_text(t_new)
        r = subprocess.run(
            ["git", "merge-file", "-p", "--diff3",
             str(d / "cur"), str(d / "base"), str(d / "new")],
            capture_output=True, text=True, check=False)
    merged = r.stdout
    if r.returncode != 0:
        merged = re.sub(
            r"<<<<<<<[^\n]*\n(.*?)\|\|\|\|\|\|\|[^\n]*\n(.*?)=======\n(.*?)>>>>>>>[^\n]*\n",
            lambda m: m.group(3), merged, flags=re.S)
    return merged, merged.count("<<<<<<<")


# Per-file overrides applied AFTER taking the NEW side:
#   str replacements that keep genuine Qubit-local product de-brands the sync did
#   not carry (evidence: docs QUBIT_REBRAND_MANIFEST.phrase_map + prose_words).
PER_FILE = {
    # compression_marker.py: the marker is MODEL-VISIBLE product text (the file's
    # own docstring says so); Qubit deliberately de-branded the literal to
    # ⟪QUBIT-CONTEXT-COMPRESSION: / "Qubit's context compressor". Preserve it.
    "agent/compression_marker.py": [
        ("⟪HERMES-CONTEXT-COMPRESSION:", "⟪QUBIT-CONTEXT-COMPRESSION:"),
        ("Hermes's context compressor", "Qubit's context compressor"),
    ],
    # anthropic_message_convert.py: canonical phrase_map is Nous Portal -> Qubit Labs.
    "agent/anthropic_message_convert.py": [
        ("Nous Portal", "Qubit Labs"),
    ],
}


def fix_false_renames(text: str) -> str:
    for bad, good in FALSE_RENAME_FIX.items():
        text = text.replace(bad, good)
    return text



def main() -> int:
    apply = "--apply" in sys.argv
    rc = 0
    for f in AGENT_FILES:
        up = "agent/" + f
        merged, hunks = merged_text(up)
        assert hunks == 0, f"{up}: {hunks} conflict hunks unresolved"
        merged = fix_false_renames(merged)
        for bad, good in PER_FILE.get(up, ()):
            merged = merged.replace(bad, good)
        try:
            compile(merged, up, "exec")
        except SyntaxError as e:
            print(f"  SYNTAX ERROR {up}: {e}")
            rc = 1
            continue
        if apply:
            (su.QR / su.route(up)).write_text(merged, encoding="utf-8")
            print(f"  wrote {su.route(up)}")
        else:
            print(f"  ok    {su.route(up)}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
