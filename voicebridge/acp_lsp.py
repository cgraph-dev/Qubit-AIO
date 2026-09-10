"""Qubit editor integration via a bounded brokered adapter.

Qubit ``acp`` checks editor-integration protocol state and ``lsp``
manages the language-server layer behind post-write diagnostics. Qubit
ships no second agent authority and never downloads unpinned binaries, so
this module provides the Qubit-native surface:

* ``acp check`` — editor-config presence plus the typed no-server state
  (read-only probes of well-known config paths; nothing is read beyond
  existence);
* ``acp setup --editor NAME`` — prints the manual configuration snippet
  for the user to paste (guidance only, like auth login: live user config
  is never edited by Qubit);
* ``acp`` serve mode and ``--accept-hooks`` — typed refusals (a second
  event path would duplicate the single Qubit authority; model text can
  never widen grants);
* ``lsp status``/``list``/``which`` — inventory over a static registry of
  conventional server binaries probed via ``PATH`` only (presence, never
  version execution, never download);
* ``lsp restart`` — typed ``NO_CLIENTS`` (Qubit spawns no persistent LSP
  clients; diagnostics run inside supervised opencode runs);
* ``lsp install``/``install-all`` — typed ``LSP_NO_DOWNLOADS`` refusal
  (unpinned binary downloads violate the pinned-source policy).

The server-name/extension registry mirrors the conventional names shown
by observed editor-protocol behavior (factual
interoperability data; no third-party source copied).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

NO_SERVER_CODE = "ACP_NO_SERVER"
ACCEPT_HOOKS_CODE = "ACP_ACCEPT_HOOKS_REFUSED"
UNKNOWN_EDITOR_CODE = "ACP_UNKNOWN_EDITOR"
NO_DOWNLOADS_CODE = "LSP_NO_DOWNLOADS"
UNKNOWN_SERVER_CODE = "LSP_UNKNOWN_SERVER"

# (server, file extensions, conventional PATH binary)
KNOWN_LSP = (
    ("pyright", (".py", ".pyi"), "pyright"),
    ("typescript", (".ts", ".tsx", ".js", ".jsx"), "typescript-language-server"),
    ("gopls", (".go",), "gopls"),
    ("rust-analyzer", (".rs",), "rust-analyzer"),
    ("clangd", (".c", ".cpp", ".h", ".hpp"), "clangd"),
    ("bash-language-server", (".sh", ".bash"), "bash-language-server"),
    ("yaml-language-server", (".yaml", ".yml"), "yaml-language-server"),
    ("lua-language-server", (".lua",), "lua-language-server"),
    ("vue-language-server", (".vue",), "vue-language-server"),
    ("svelte-language-server", (".svelte",), "svelte-language-server"),
    ("tailwindcss", (".css",), "tailwindcss-language-server"),
    ("dockerfile-ls", ("Dockerfile",), "docker-langserver"),
    ("terraform-ls", (".tf",), "terraform-ls"),
    ("haskell-language-server", (".hs",), "haskell-language-server"),
)

KNOWN_EDITORS = ("vscode", "zed", "jetbrains")


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def _short(path: str) -> str:
    home = str(Path("~").expanduser())
    return ("~" + path[len(home):]) if home != "~" and path.startswith(home) else path


# ------------------------------------------------------------------- ACP
def acp_check() -> Dict[str, Any]:
    """Editor-integration readiness (read-only config presence probes)."""
    home = Path("~").expanduser()
    candidates = {
        "vscode": home / ".config" / "Code" / "User" / "settings.json",
        "zed": home / ".config" / "zed" / "settings.json",
        "jetbrains": home / ".config" / "JetBrains",
    }
    editors = []
    for name, path in candidates.items():
        try:
            present = path.exists()
        except OSError:
            present = False
        editors.append({"editor": name, "config_present": present})
    return {"state": "OK", "schema": "qubit-acp-check/1",
            "server": "ABSENT",
            "server_detail": "no ACP server in product; editor work flows "
                             "through the Qubit gateway/CLI under the "
                             "single broker authority",
            "editors": editors}


def acp_setup_snippet(editor: str) -> Dict[str, Any]:
    """Manual configuration snippet for one editor (printed, never written)."""
    name = (editor or "").strip().lower()
    if name not in KNOWN_EDITORS:
        return {"state": "UNKNOWN", "code": UNKNOWN_EDITOR_CODE,
                "editor": _redact(name)[:40],
                "known_editors": list(KNOWN_EDITORS),
                "owner": "qubit-policy",
                "rollback": "nothing written; nothing to roll back"}
    snippets = {
        "vscode": ("~/.config/Code/User/settings.json",
                   '{"qubit.agent": {"gateway": "ws://127.0.0.1:18789", '
                   '"note": "paste manually; Qubit never edits editor config"}}'),
        "zed": ("~/.config/zed/settings.json",
                '{"qubit_agent": {"gateway": "ws://127.0.0.1:18789"}}'),
        "jetbrains": ("~/.config/JetBrains/<product>/options/qubit.xml",
                      "<application><component name=\"QubitAgent\">"
                      "<option name=\"gateway\" value=\"ws://127.0.0.1:18789\" />"
                      "</component></application>"),
    }
    path, snippet = snippets[name]
    return {"state": "OK", "schema": "qubit-acp-setup/1", "editor": name,
            "write_it_yourself": path, "snippet": snippet,
            "handoff": "manual: paste it yourself; Qubit never edits live "
                       "editor config"}


def acp_refuse_serve() -> Dict[str, Any]:
    """Typed refusal for ACP serve mode (single event path invariant)."""
    return {"state": "REFUSED", "code": NO_SERVER_CODE,
            "explanation": "Qubit Agent cannot do this: serving ACP would "
                           "start a second agent authority beside the Qubit "
                           "broker; editor work flows through the Qubit "
                           "gateway/CLI instead.",
            "owner": "qubit-policy",
            "rollback": "nothing started; nothing to roll back"}


def acp_refuse_accept_hooks() -> Dict[str, Any]:
    """Typed refusal for hook auto-approval (policy-bypass invariant)."""
    return {"state": "REJECTED", "code": ACCEPT_HOOKS_CODE,
            "explanation": "Qubit Agent cannot do this: auto-approving "
                           "unseen hooks would let model or tool text widen "
                           "grants; every hook needs explicit review.",
            "owner": "qubit-policy",
            "rollback": "nothing approved; nothing to roll back"}


# ------------------------------------------------------------------- LSP
def lsp_list() -> Dict[str, Any]:
    """Known language servers with PATH presence (never executed)."""
    servers: List[Dict[str, Any]] = []
    for server, extensions, binary in KNOWN_LSP:
        found = shutil.which(binary)
        servers.append({"server": server,
                        "extensions": list(extensions),
                        "present": found is not None,
                        "path": _short(found) if found else None,
                        "managed": "externally: Qubit never downloads "
                                   "server binaries"})
    return {"state": "OK", "schema": "qubit-lsp-list/1",
            "layer": "ABSENT",
            "layer_detail": "no LSP layer in product; post-write semantic "
                            "diagnostics run inside supervised opencode runs",
            "servers": servers}


def lsp_status() -> Dict[str, Any]:
    """LSP layer state + inventory summary (read-only)."""
    inv = lsp_list()
    present = sum(1 for s in inv["servers"] if s["present"])
    return {"state": "OK", "schema": "qubit-lsp-status/1",
            "layer": "ABSENT", "active_clients": "none",
            "known_servers": len(inv["servers"]), "present": present,
            "detail": inv["layer_detail"]}


def lsp_which(server: str) -> Dict[str, Any]:
    """Locate one known server on PATH (presence only, never executed)."""
    name = (server or "").strip().lower()
    for known, extensions, binary in KNOWN_LSP:
        if name == known:
            found = shutil.which(binary)
            return {"state": "PRESENT" if found else "ABSENT",
                    "server": known, "extensions": list(extensions),
                    "path": _short(found) if found else None,
                    "managed": "externally: Qubit never downloads server "
                               "binaries"}
    return {"state": "UNKNOWN", "code": UNKNOWN_SERVER_CODE,
            "server": _redact(name)[:80],
            "known_servers": [k[0] for k in KNOWN_LSP],
            "owner": "qubit-policy",
            "rollback": "nothing changed; nothing to roll back"}


def lsp_restart() -> Dict[str, Any]:
    """Typed no-client report (Qubit spawns no persistent LSP clients)."""
    return {"state": "NO_CLIENTS",
            "detail": "Qubit spawns no persistent LSP clients; nothing to "
                      "tear down. Diagnostics run inside supervised "
                      "opencode runs."}


def lsp_refuse_install(server: str = "") -> Dict[str, Any]:
    """Typed refusal for server binary downloads (pinned-source policy)."""
    return {"state": "REFUSED", "code": NO_DOWNLOADS_CODE,
            "server": _redact((server or "all"))[:80],
            "explanation": "Qubit Agent cannot do this: downloading "
                           "language-server binaries would install unpinned "
                           "third-party code; install your distribution's "
                           "packaged server yourself — `lsp list` will then "
                           "report it as present.",
            "owner": "qubit-policy",
            "rollback": "nothing installed; nothing to roll back"}
