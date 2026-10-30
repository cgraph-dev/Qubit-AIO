"""What ``save_config`` persists when the process serves a frozen session config.

A managed worker binds its session's creation-time config (``agent.safe_worker_policy``), so its
``load_config()`` is that snapshot, not the profile file. A read-modify-write in such a worker
(an ``always`` approval, a connector enable) must land only what it changed on the LIVE file:
writing the whole snapshot back would revert every profile edit made since the session was
created. Outside a frozen worker this is ``save_config``'s ordinary ``merge_existing`` handling.
"""

from __future__ import annotations

import copy
from typing import Any


def rebase_for_save(config: dict[str, Any], live_raw: dict[str, Any], merge_existing: bool,
                    replace_keys=None) -> dict[str, Any]:
    from qubit_cli import config as _config

    if _config._worker_config_snapshot() is not None:
        # ``_three_way`` lives in THIS module; ``_config`` only re-exports the frozen-worker flag
        # and the merge helper. Calling ``_config._three_way`` raised AttributeError, and
        # ``save_permanent_allowlist`` swallowed it — a frozen worker could not write its own
        # approval back (test_managed_worker_frozen_config).
        #
        # The three-way result already carries only the caller's deltas over its frozen base;
        # merging the whole snapshot over it (the old `_merge_partial_save` step) re-applied
        # unchanged frozen values and reverted live profile edits. Apply only the caller's
        # ``replace_keys`` paths, which must be authoritative.
        rebased = _three_way(live_raw or {}, config, _config.load_config())
        for path in replace_keys or ():
            path = tuple(path)
            parent = rebased
            for part in path[:-1]:
                parent = parent.get(part) if isinstance(parent, dict) else None
                if parent is None:
                    break
            if not isinstance(parent, dict):
                continue
            source: Any = config
            for part in path:
                if not isinstance(source, dict) or part not in source:
                    source = None
                    break
                source = source[part]
            if source is None:
                parent.pop(path[-1], None)
            else:
                parent[path[-1]] = copy.deepcopy(source)
        return rebased
    if merge_existing and live_raw:
        return _config._merge_partial_save(live_raw, config, replace_keys)
    return config


def _three_way(live: Any, desired: Any, base: Any) -> Any:
    """*live* with the caller's edits (*desired* relative to the *base* it loaded) applied.
    Mappings recurse; list edits add/remove items; a key the caller dropped is left alone. A
    mapping/list branch deleted (or retyped) on *live* since *base* stays as live has it: a
    leaf edit must not resurrect the stale snapshot subtree around it."""
    if desired == base:
        return copy.deepcopy(live)
    if isinstance(desired, dict) and isinstance(base, dict) and isinstance(live, dict):
        merged = copy.deepcopy(live)
        for key, value in desired.items():
            if key not in base:
                merged[key] = copy.deepcopy(value)
            elif value != base[key]:
                kind = dict if isinstance(base[key], dict) else list if isinstance(base[key], list) else None
                if kind is not None and not isinstance(live.get(key), kind):
                    continue  # concurrently deleted/replaced live branch: live wins
                merged[key] = _three_way(live.get(key), value, base[key])
        return merged
    if isinstance(desired, list) and isinstance(base, list) and isinstance(live, list):
        removed = [item for item in base if item not in desired]
        kept = [item for item in live if item not in removed]
        return kept + [copy.deepcopy(item) for item in desired if item not in base and item not in kept]
    return copy.deepcopy(desired)


# --- ported from upstream 088f657ecf ---

def _frozen_for_deletion(snapshot: dict[str, Any]) -> Any:
    """The snapshot branches that count as operator state.

    An ordinary snapshot only contains what the profile had at creation, so an explicit
    empty allowlist is a real branch. A bypass worker (safe mode, ``--ignore-user-config``)
    freezes a copy of the schema, so a branch that is still the schema default was never
    an operator choice and must not count as a deletion."""
    from agent.safe_worker_policy import _current_policy

    policy = _current_policy()
    if policy is None or not policy.ignore_user_config:
        return snapshot
    from qubit_cli.config_defaults import DEFAULT_CONFIG

    return _schema_default_branch(snapshot, DEFAULT_CONFIG)


def _schema_default_branch(snapshot: Any, defaults: Any) -> Any:
    """*snapshot* without branches whose value is still the schema default."""
    if not isinstance(snapshot, dict) or not isinstance(defaults, dict):
        return copy.deepcopy(snapshot)
    kept: dict[str, Any] = {}
    for key, value in snapshot.items():
        if key not in defaults:
            kept[key] = copy.deepcopy(value)
            continue
        default = defaults[key]
        if value == default:
            continue
        if isinstance(value, dict) and isinstance(default, dict):
            nested = _schema_default_branch(value, default)
            if nested:
                kept[key] = nested
        else:
            kept[key] = copy.deepcopy(value)
    return kept


_SAME_AS_BASE = object()
