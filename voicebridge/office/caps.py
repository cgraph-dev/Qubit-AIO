"""Budget caps — four keys, owner-dialable (D15/D16, from StarNet's budgetcaps).

| Key | Scope |
|---|---|
| ``perRun`` | one turn — the tightest, and the one that stops "one simple prompt drained a $10 wallet" |
| ``perAgent`` | one seat |
| ``perDay`` | the whole org, per day |
| ``global`` | lifetime ceiling |

The resolution rules are in the plan because they have all been violated by a
naive implementation somewhere:

* ``effective = persisted_override ?? default``, per key.
* **``0`` is a real saved value meaning "no cap".** An *absent* key means "use
  the default". These are different states and are stored differently.
* Clearing (``null`` / ``''``) reverts to the default — it does not mean zero.
* A stored override is honoured **only if it is a finite number ≥ 0**.
  Anything else is treated as not set, so *a corrupt persisted value can never
  grant unintended headroom*.
* There is a sane ceiling purely to catch a fat-fingered or overflowing value.

Every cap here is in **cents**, and every check reads the **metered** fold, so
a subscription-backed turn can never consume headroom it did not spend (D16).
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

from . import store
from .errors import BAD_INPUT_CODE, OfficeError

#: Ordered tightest-first, which is also the resolution order admission uses.
KEYS = ("perRun", "perAgent", "perDay", "global")

#: Bootstrap defaults, from `45-ECONOMY.md`. Deliberately small: the org proves
#: the loop on a few dollars before anyone raises these.
DEFAULTS: Dict[str, int] = {
    "perRun": 50,            # $0.50 — one turn
    "perAgent": 5_000,       # $50.00 — one seat
    "perDay": 5_000,         # $50.00 — the whole org, per day
    "global": 500_000,       # $5,000.00 — lifetime, a hard kill switch
}

#: StarNet's ceiling, kept for the same reason: catch an overflowing value.
CEILING_CENTS = 1_000_000_000        # $10,000,000

_META_PREFIX = "cap:"


def _coerce(value: Any) -> Optional[int]:
    """Honour a stored override only if it is a finite number >= 0.

    Returns ``None`` for "not set" (absent, cleared, or corrupt), which is
    distinct from ``0`` ("a real saved value meaning no cap").
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return None
        try:
            value = float(text)
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if value < 0:
        return None                      # a negative cap grants nothing
    value = int(value)
    if value > CEILING_CENTS:
        return CEILING_CENTS             # clamp rather than overflow
    return value


def resolve(cfg: Any) -> Dict[str, int]:
    """The effective caps: persisted override, else the default, per key."""
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        stored = {r["key"]: r["value"] for r in conn.execute(
            "SELECT key, value FROM office_meta WHERE key LIKE ?",
            (_META_PREFIX + "%",))}
    finally:
        conn.close()
    out: Dict[str, int] = {}
    for key in KEYS:
        override = _coerce(stored.get(_META_PREFIX + key))
        out[key] = DEFAULTS[key] if override is None else override
    return out


def inspect(cfg: Any) -> Dict[str, Any]:
    """Caps with their provenance, so the owner can see what is in force."""
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        stored = {r["key"]: r["value"] for r in conn.execute(
            "SELECT key, value FROM office_meta WHERE key LIKE ?",
            (_META_PREFIX + "%",))}
    finally:
        conn.close()
    rows = []
    for key in KEYS:
        raw = stored.get(_META_PREFIX + key)
        override = _coerce(raw)
        rows.append({
            "key": key,
            "effective_cents": DEFAULTS[key] if override is None else override,
            "source": ("default" if override is None else "override"),
            "stored_raw": raw,
            "stored_ok": (override is not None or raw is None),
            "meaning": {0: "no cap (a real saved value)"}.get(override, None),
        })
    return {"state": "OK", "ceiling_cents": CEILING_CENTS, "caps": rows}


def set_cap(cfg: Any, key: str, value: Any, *, confirmed: bool = False,
            requested_by: str = "cli:user") -> Dict[str, Any]:
    """Set or clear one cap.

    ``value=None``/``''``/``'default'`` clears the override and reverts to the
    default. ``0`` is stored as a real value meaning "no cap".
    """
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE, "office cap-set requires --confirm")
    if key not in KEYS:
        raise OfficeError(BAD_INPUT_CODE,
                          f"cap key must be one of {', '.join(KEYS)}")
    clear = value is None or value == "" or value == "default"
    coerced = None if clear else _coerce(value)
    if not clear and coerced is None:
        raise OfficeError(
            BAD_INPUT_CODE,
            f"{value!r} is not a usable cap. A cap is a non-negative integer "
            f"number of cents (0 means no cap); anything else grants no "
            f"headroom (D16).")
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        if clear:
            conn.execute("DELETE FROM office_meta WHERE key=?",
                         (_META_PREFIX + key,))
            conn.commit()
            store.audit("cap.clear", requested_by, {"key": key})
            return {"state": "OK", "key": key, "cleared": True,
                    "effective_cents": DEFAULTS[key], "source": "default"}
        conn.execute(
            "INSERT INTO office_meta(key, value) VALUES(?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_META_PREFIX + key, str(coerced)))
        conn.commit()
        store.audit("cap.set", requested_by, {"key": key, "cents": coerced})
        return {"state": "OK", "key": key, "effective_cents": coerced,
                "source": "override",
                "meaning": ("no cap" if coerced == 0 else None)}
    finally:
        conn.close()


def headroom(cfg: Any, key: str, spent_cents: int) -> Dict[str, Any]:
    """Remaining headroom under one cap, using the metered fold.

    ``headroom_cents`` is ``None`` when the cap is 0 ("no cap") — not a huge
    number, and not zero.
    """
    if key not in KEYS:
        raise OfficeError(BAD_INPUT_CODE,
                          f"cap key must be one of {', '.join(KEYS)}")
    cap = resolve(cfg)[key]
    if cap == 0:
        return {"state": "OK", "key": key, "cap_cents": 0,
                "headroom_cents": None, "meaning": "no cap"}
    remaining = cap - int(spent_cents)
    return {"state": "OK", "key": key, "cap_cents": cap,
            "spent_cents": int(spent_cents),
            "headroom_cents": remaining,
            "exceeded": remaining < 0}
