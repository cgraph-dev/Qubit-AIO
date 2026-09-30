"""Packet 8R: supervised OpenCode coding workflow adapter (optional).

See voicebridge.opencode (__init__) for the contract.
"""
from __future__ import annotations

from typing import Optional, Tuple

from ..actions import (
    ADAPTER_OPENCODE, RB_IMPOSSIBLE, RB_NONE, ActionError, ActionSpec,
    _spec, register_action_spec,
)
from ..capabilities import Tier
from .policy import ACT_PLAN, ACT_RUN, MAX_TASK_CHARS
# ---------------------------------------------------------------------------
# Action specs (registered through the existing 8F surface)
# ---------------------------------------------------------------------------

def opencode_action_specs() -> Tuple[ActionSpec, ...]:
    task_field = {"type": "str", "required": False, "max": MAX_TASK_CHARS}
    return (
        _spec(ACT_PLAN, "1.0.0", ADAPTER_OPENCODE, Tier.READ_ONLY,
              "preview a bounded OpenCode coding run (no subprocess)",
              ("code",),
              {"project": {"type": "str", "required": True, "max": 512,
                            "kind": "filepath"},
               "task": task_field,
               "task_file": {"type": "str", "required": False, "max": 512,
                             "kind": "filepath"},
               "disposable": {"type": "bool", "required": False}},
              "validates scope and shows the exact argv/env budget; "
              "spawns nothing",
              RB_NONE, "no side effect; nothing to undo",
              ("project", "task")),
        _spec(ACT_RUN, "1.0.0", ADAPTER_OPENCODE, Tier.DESTRUCTIVE,
              "run a supervised bounded OpenCode coding workflow",
              ("code",),
              {"project": {"type": "str", "required": True, "max": 512,
                            "kind": "filepath"},
               "task": task_field,
               "task_file": {"type": "str", "required": False, "max": 512,
                             "kind": "filepath"},
               "disposable": {"type": "bool", "required": False},
               "timeout_s": {"type": "int", "required": False,
                             "min": 5, "max": 600}},
              "executes one bounded opencode run; may modify files under "
              "the approved root only; never commits/pushes/installs",
              RB_IMPOSSIBLE, "code changes have no automatic rollback; "
                             "review the diff and use git revert",
              ("project", "task"), timeout_s=600.0),
    )


def opencode_spec(action_id: str) -> Optional[ActionSpec]:
    for spec in opencode_action_specs():
        if spec.action_id == action_id:
            return spec
    return None


def register_opencode_specs() -> None:
    for spec in opencode_action_specs():
        try:
            register_action_spec(spec)
        except ActionError as exc:
            if "already registered" not in str(exc):
                raise


