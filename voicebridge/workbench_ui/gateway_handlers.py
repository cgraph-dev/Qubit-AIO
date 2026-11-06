"""Gateway targets for the ported client's call set.

Each function exported here is what the gateway's ``_HANDLERS`` table names. It
is a one-line delegation to the handler that OWNS the behaviour — in
:mod:`voicebridge.workbench_ui.handlers_extra`, in :mod:`workbench_ui.chat_stream`
or in the ``workbench_shim`` families — and a typed refusal if the owner is
missing from the tree.

WHY THE INDIRECTION
---------------------------------------------------------------------------
The gateway resolves a handler by NAME so the compat table stays one pattern row
per method. Two different owners answer these paths, and the gateway must not
care which. This module is that seam; it implements nothing.

THE ONE THING IT DOES DO
---------------------------------------------------------------------------
Normalise the verb out of the sub-path. The gateway passes ``sub`` (everything
after ``/api/wb/api/``); a family handler needs ``action`` = the trailing
segment with the family prefix intact for nested verbs
(``session/title/regenerate`` -> ``title/regenerate``).
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from .handlers_extra import HANDLERS as _EXTRA


def _extra(name: str, cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
           p: Dict[str, Any]) -> Tuple[int, Any]:
    return _EXTRA[name](cfg, q, b, p)


def _tail(p: Dict[str, Any], prefix: str) -> str:
    """The verb segment(s) after ``prefix``, keeping nested verbs intact."""
    sub = str((p or {}).get("sub") or "").strip("/")
    if sub.startswith(prefix):
        return sub[len(prefix):].strip("/")
    return str((p or {}).get("action") or "")


def _with_action(p: Dict[str, Any], action: str) -> Dict[str, Any]:
    params = dict(p or {})
    params["action"] = action
    return params


# --------------------------------------------------------------------- chat
def chat_start(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
               p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("chat_start", cfg, q, b, p)


def chat_steer(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
               p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("chat_steer", cfg, q, b, p)


def chat_stop(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
              p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("chat_stop", cfg, q, b, p)


def chat_stream_status(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                       p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("chat_stream_status", cfg, q, b, p)


# ------------------------------------------------------------------ sessions
def session_detail(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                   p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("session_detail", cfg, q, b, p)


def sessions_search(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                    p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("sessions_search", cfg, q, b, p)


def session_verb(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                 p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("session_verb", cfg, q, b,
                  _with_action(p, _tail(p, "session/")))


def session_stream(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                   p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("session_stream", cfg, q, b, p)


def session_flag(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                 p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("session_flag", cfg, q, b, p)


def sessions_events(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                    p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("sessions_events", cfg, q, b, p)


# ------------------------------------------------------------------- cards
def approval_pending(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                     p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("approval_pending", cfg, q, b, p)


def approval_respond(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                     p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("approval_respond", cfg, q, b, p)


def clarify_pending(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                    p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("clarify_pending", cfg, q, b, p)


def clarify_respond(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                    p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("clarify_respond", cfg, q, b, p)


# -------------------------------------------------------------------- crons
def crons_recent(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                 p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("crons_recent", cfg, q, b, p)


def cron_verb(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
              p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("cron_verb", cfg, q, b, _with_action(p, _tail(p, "crons/")))


# ---------------------------------------------------------------- workspace
def workspace_list(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                   p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("workspace_list", cfg, q, b, p)


def workspaces(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
               p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("workspaces", cfg, q, b, p)


# ------------------------------------------------------------ small polls
def profile_active(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                   p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("profile_active", cfg, q, b, p)


def health_agent(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                 p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("health_agent", cfg, q, b, p)


def dashboard_config(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                     p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("dashboard_config", cfg, q, b, p)


def dashboard_status(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                     p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("dashboard_status", cfg, q, b, p)


def onboarding_status(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                      p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("onboarding_status", cfg, q, b, p)


def client_events_log(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                      p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("client_events_log", cfg, q, b, p)


def transcribe_capability(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                          p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("transcribe_capability", cfg, q, b, p)

def updates_check(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                  p: Dict[str, Any]) -> Tuple[int, Any]:
    return _extra("updates_check", cfg, q, b, p)
