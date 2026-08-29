"""RFC 6902 JSON Patch subset for STATE_SNAPSHOT → STATE_DELTA (Packet 2A).

Local requirement (plan Phase 2 task 10): the bridge emits a documented,
tested local subset of RFC 6902 — ``add``, ``remove``, ``replace`` — and
recovers from anything else by requesting a fresh snapshot instead of
guessing. Arrays are diffed wholesale (replace) rather than element-wise:
deterministic, order-safe, and sufficient for small bounded state objects.
JSON Pointer tokens are escaped per RFC 6901 (``~`` → ``~0``, ``/`` → ``~1``).
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List


class PatchError(ValueError):
    """Malformed patch or pointer (fail closed)."""


class PatchUnsupportedError(PatchError):
    """Op outside the supported subset — caller must request a fresh snapshot."""


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _pointer(tokens: List[str]) -> str:
    return "/" + "/".join(_escape(t) for t in tokens)


def _split_pointer(pointer: str) -> List[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise PatchError(f"pointer must start with '/': {pointer!r}")
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer[1:].split("/")]


def _get(container: Any, token: str) -> Any:
    if isinstance(container, dict):
        if token not in container:
            raise PatchError(f"member missing: {token!r}")
        return container[token]
    if isinstance(container, list):
        idx = int(token) if token != "-" else len(container)
        if not 0 <= idx <= len(container):
            raise PatchError(f"index out of range: {token!r}")
        return container[idx]
    raise PatchError(f"cannot descend into {type(container).__name__}")


def _set(container: Any, token: str, value: Any) -> None:
    if isinstance(container, dict):
        container[token] = value
        return
    if isinstance(container, list):
        idx = int(token) if token != "-" else len(container)
        if not 0 <= idx <= len(container):
            raise PatchError(f"index out of range: {token!r}")
        if idx == len(container):
            container.append(value)
        else:
            container[idx] = value
        return
    raise PatchError(f"cannot index into {type(container).__name__}")


def _del(container: Any, token: str) -> None:
    try:
        if isinstance(container, dict):
            del container[token]
        else:
            del container[int(token)]
    except (KeyError, ValueError, IndexError) as exc:
        raise PatchError(f"remove target missing: {token!r}") from exc


def _resolve(doc: Any, tokens: List[str]):
    cur = doc
    for tok in tokens[:-1]:
        cur = _get(cur, tok)
    return cur, (tokens[-1] if tokens else None)


def diff_state(base: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Ordered add/remove/replace ops transforming ``base`` into ``new``."""
    ops: List[Dict[str, Any]] = []

    def walk(b: Any, n: Any, tokens: List[str]) -> None:
        if isinstance(b, dict) and isinstance(n, dict):
            for key in sorted(set(b) - set(n)):
                ops.append({"op": "remove", "path": _pointer(tokens + [key])})
            for key in sorted(set(b) & set(n)):
                walk(b[key], n[key], tokens + [key])
            for key in sorted(set(n) - set(b)):
                ops.append({"op": "add", "path": _pointer(tokens + [key]),
                            "value": n[key]})
        elif b != n or type(b) is not type(n):  # bool/int identity matters
            ops.append({"op": "replace", "path": _pointer(tokens), "value": n})

    walk(base, new, [])
    return ops


def apply_patch(state: Dict[str, Any], ops: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Apply an ordered patch to a deep copy; unsupported ops fail closed."""
    doc: Any = copy.deepcopy(state)
    for op in ops:
        if not isinstance(op, dict):
            raise PatchError("patch op must be an object")
        kind, path = op.get("op"), op.get("path")
        if kind not in ("add", "remove", "replace"):
            raise PatchUnsupportedError(
                f"unsupported op {kind!r}: request fresh snapshot")
        if not isinstance(path, str):
            raise PatchError(f"invalid path {path!r}")
        if kind in ("add", "replace") and "value" not in op:
            raise PatchError(f"{kind} requires a value")
        tokens = _split_pointer(path)
        if not tokens:
            if kind == "remove":
                raise PatchError("cannot remove the document root")
            doc = copy.deepcopy(op["value"])
            continue
        container, token = _resolve(doc, tokens)
        if kind == "remove":
            _del(container, token)
        elif kind == "replace":
            if isinstance(container, dict) and token not in container:
                raise PatchError(f"replace target missing: {token!r}")
            if isinstance(container, list) and token != "-" and \
                    int(token) >= len(container):
                raise PatchError(f"replace target missing: {token!r}")
            _set(container, token, copy.deepcopy(op["value"]))
        else:  # add
            _set(container, token, copy.deepcopy(op["value"]))
    return doc
