"""Qubit Workbench backend seam — the data layer behind ``/qubit-workbench``.

WHAT THIS IS
---------------------------------------------------------------------------
``/qubit-workbench`` is a NEW Qubit page absorbed from ``hermes-webui``: the
three-panel session browser + chat + workspace file browser. This package is
everything that page needs from the backend, and nothing else. It is a seam,
not a framework — two adapters of pure functions, each returning a JSON-ready
``dict`` and each able to say *why* it cannot answer.

It does NOT own a route table. The dashboard routes are declared beside the
adapters in ``voicebridge/workbench/workbench_routes.py`` and registered into
the compat table by the line given at the bottom of this docstring.

PROVENANCE
---------------------------------------------------------------------------
Upstream:  ``github.com/nesquena/hermes-webui``
Pin:       ``3d9488fca6bb248c77f988bfbffcca9d8cfd9561``
License:   MIT — Copyright (c) 2025 Hermes Web UI Contributors
           (full licence text and per-file record: ``PROVENANCE_UPSTREAM.md``)

WHAT CAME FROM UPSTREAM (presentation contract only, re-implemented against
Qubit's own stores — no upstream code is vendored):

* ``adapters_workspace.py`` — the entry-wire shape and the listing/filter rules
  of ``api/workspace.py::list_dir`` (upstream ``:1632-1841``): directories
  first, then files, then symlinks; 200-entry cap; ``mtime_ns`` as a string;
  broken/looping symlinks filtered; a symlink whose target escapes the root is
  shown *without* disclosing where it points (upstream ``:1730-1745``). The
  preview body shape follows ``api/workspace.py::read_file_content``
  (upstream ``:1866-1889``).
* The session-list / transcript / composer split of the three-panel layout
  (``static/index.html:171-188``) — that is a frontend concern; the backend
  contract it needs is all of ``adapters_sessions.py``.

QUBIT-AUTHORED (no upstream counterpart):

* Every line that touches data. Sessions go through
  :class:`voicebridge.qubit_session_store.QubitSessionStore` — the ONE
  chat-transcript authority (``CHAT_SESSION_AUTHORITY`` in
  ``voicebridge/qubit_compat.py``). Turns delegate to
  ``voicebridge.chat_transport.turn`` — the same persisted turn the CLI, the
  voice bridge and ``/qubit-chat`` run. Files go through
  ``voicebridge.files`` — Qubit's own approved-root confinement.
* The typed-refusal contract: a capability that cannot answer says so with a
  code and a detail naming what is missing. It never returns an empty list,
  because an empty list reads as "you have no sessions" and that is a lie.

DELIBERATELY NOT PORTED (each is a reason this seam exists)
---------------------------------------------------------------------------
* **The upstream server and its ``api/`` modules.** hermes-webui is a second
  agent stack: ``api/streaming.py:1413`` eagerly imports
  ``run_agent.AIAgent`` (10 call sites resolve that class), and
  ``api/routes.py:35146`` opens ``state.db`` for writing with a bare
  ``sqlite3.connect``. Qubit has exactly ONE chat-transcript writer
  (``QubitSessionStore``, with a second-writer refusal at
  ``voicebridge/qubit_session_store.py:61-93``). A second writer on the 826 MB
  store is data corruption, not a style preference. Nothing here imports
  ``run_agent.AIAgent`` and nothing opens ``state.db`` directly.
* **The ``hermes`` binary shell-out.** ``api/gateway_restart.py:27-35,118-124``
  resolves and spawns ``hermes gateway restart``. Qubit runs one Python event
  path; no adapter here spawns a subprocess at all.
* **The upstream streaming engine** (``api/streaming.py``, 438 ``def``s).
  Qubit already streams through ``voicebridge.chat_transport`` (``/api/qubit/
  chat/stream``); a second loop would be a second source of truth for a turn.
* **The workspace *write* surface** — ``open_anchored_create_fd``,
  ``make_anchored_dir``, ``open_anchored_write_fd``, ``unlink_anchored``,
  ``rmtree_anchored``, ``rename_anchored`` and ``/api/workspace/upload`` (all in
  upstream ``api/workspace.py``). Mutations in Qubit belong to the brokered
  confirmation journal (``voicebridge/file_patch.py`` via
  ``voicebridge/broker.py``), not to a viewer. This seam is read-only.
* **The workspace path-escape GRANT model** (``_escape_authorize`` /
  ``_escape_list_dir`` / token store, ``api/routes.py:23623+``). It is a
  security surface Qubit did not design; confinement here is Qubit's approved
  project roots (``voicebridge/files.py``) and there is no grant token.
* **The upstream per-session client-state model** (``static/``: ``ui.js`` /
  ``sessions.js`` / ``panels.js``, 84 + 72 ``localStorage``/``sessionStorage``
  touchpoints). It would fight Qubit's remount contract; the frontend lane
  owns its own state.

REGISTRATION (delivered separately — this package adds no row to the compat
table itself)::

    from .workbench_routes import workbench_routes as _wb_routes, workbench_handlers as _wb_handlers
    for _m, _p, _h in _wb_routes():
        _r(_m, _p, _h)
    HANDLERS.update(_wb_handlers())

Importing this package has no side effects: no route is registered, no store is
opened, no config is read.
"""
from __future__ import annotations

__all__ = ["adapters_sessions", "adapters_workspace"]

#: Upstream provenance, asserted readable by the two adapters' refusals so a
#: caller can always name where this page came from.
UPSTREAM_REPO = "github.com/nesquena/hermes-webui"
UPSTREAM_PIN = "3d9488fca6bb248c77f988bfbffcca9d8cfd9561"
UPSTREAM_LICENSE = "MIT"
UPSTREAM_COPYRIGHT = "Copyright (c) 2025 Hermes Web UI Contributors"

#: The single chat-transcript authority. Mirrors
#: ``voicebridge.qubit_compat.CHAT_SESSION_AUTHORITY``; asserted equal by a
#: test so this package cannot drift onto a second store.
SESSION_AUTHORITY = "voicebridge.qubit_session_store.QubitSessionStore"
