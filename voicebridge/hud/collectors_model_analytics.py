"""HUD model-analytics / costs collector — Qubit-native port of
``backend/collectors/model_analytics.py``.

Vendored from ``joeynyc/hermes-hudui`` @ be83208f48b7a1c67b7816837c8df71a61ef7edf
Copyright (c) 2026 Hermes HUD Contributors. MIT licensed; the file-by-file
record is ``PROVENANCE.json`` beside this package. Rebranded and re-pointed at
QUBIT state.

What changed from upstream, and why
-----------------------------------
* **No second database opener.** Upstream opened ``<home>/state.db`` with its own
  ``sqlite3.connect`` and ran a hand-built SELECT with optional-column splicing
  against the agent schema it was verified on (v16/23/26/31). Qubit has exactly
  one chat-session authority, and its ``state.db`` is a live multi-hundred-MB
  WAL database shared with the agent — a second opener is a corruption bug, not
  an optimisation. This collector reads the published aggregate
  :func:`voicebridge.qubit_session_store.analytics_usage`, which is the
  authority's own read-only rollup of the same ``sessions`` rows (totals, daily
  series, per-model rows with last-use).
* **Per-session detail dropped** — upstream attached every session row to its
  model (``session_details``), which is what forced the direct query. The
  authority's rollup does not expose it, so the panel gets per-model totals and
  the daily series instead. A caller that needs per-session rows reads them
  through the authority's ``list``/``show`` surfaces, not through this module.
* **Capabilities** — upstream joined models.dev metadata onto each usage row.
  This collector reports the numbers only; the Models tab
  (:mod:`voicebridge.hud.collectors_model_info`) carries capabilities for a
  known model, so one panel cannot silently disagree with the other.

The Costs tab and the Model Analytics tab are the same aggregate in Qubit; both
render this collector's output. Pure function, JSON-ready dict, never raises.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

#: Days window the panel asks for by default (upstream's default too).
DEFAULT_DAYS = 30


def _iso(value: Any) -> Optional[str]:
    try:
        stamp = float(value or 0)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(stamp).isoformat() if stamp > 0 else None


def collect_model_analytics(home: Optional[Any] = None, cfg: Any = None,
                            *, days: Optional[int] = DEFAULT_DAYS,
                            ) -> Dict[str, Any]:
    """Per-model token/cost aggregates from the canonical session store.

    ``days`` is the rolling window (``None`` or ``<= 0`` means "the authority's
    maximum window", 365 days — the aggregate is bounded by the authority, never
    unbounded). ``cfg`` selects the profile island's store.
    """
    out: Dict[str, Any] = {
        "available": False,
        "detail": "",
        "period_days": None,
        "models": [],
        "total_models": 0,
        "total_sessions": 0,
        "total_tokens": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cache_read_tokens": 0,
        "total_reasoning_tokens": 0,
        "total_estimated_cost_usd": 0.0,
        "total_actual_cost_usd": 0.0,
        "daily": [],
    }
    window = days if days is not None and int(days) > 0 else 365
    try:
        from ..qubit_session_store import analytics_usage
    except Exception as exc:  # noqa: BLE001 - a fork miss is a report
        out["detail"] = f"session authority unavailable ({exc})"
        return out
    try:
        usage = analytics_usage(cfg, days=int(window))
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"usage aggregate failed ({exc})"
        return out
    if usage is None:
        out["detail"] = "no Qubit state database to aggregate"
        return out

    totals = usage.get("totals") or {}
    models: List[Dict[str, Any]] = []
    for row in usage.get("by_model") or []:
        input_tokens = int(row.get("input_tokens") or 0)
        output_tokens = int(row.get("output_tokens") or 0)
        cache_read = int(row.get("cache_read_tokens") or 0)
        reasoning = int(row.get("reasoning_tokens") or 0)
        models.append({
            "model": str(row.get("model") or "unknown"),
            "provider": str(row.get("provider") or ""),
            "sessions": int(row.get("sessions") or 0),
            "api_calls": int(row.get("api_calls") or 0),
            "tool_calls": int(row.get("tool_calls") or 0),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "reasoning_tokens": reasoning,
            "total_tokens": input_tokens + output_tokens + cache_read + reasoning,
            "avg_tokens_per_session": float(row.get("avg_tokens_per_session") or 0.0),
            "estimated_cost_usd": round(float(row.get("estimated_cost") or 0.0), 6),
            "actual_cost_usd": round(float(row.get("actual_cost") or 0.0), 6),
            "last_used_at": _iso(row.get("last_used_at")),
        })

    daily = [{
        "day": str(row.get("day") or ""),
        "sessions": int(row.get("sessions") or 0),
        "api_calls": int(row.get("api_calls") or 0),
        "input_tokens": int(row.get("input_tokens") or 0),
        "output_tokens": int(row.get("output_tokens") or 0),
        "cache_read_tokens": int(row.get("cache_read_tokens") or 0),
        "reasoning_tokens": int(row.get("reasoning_tokens") or 0),
        "estimated_cost_usd": round(float(row.get("estimated_cost") or 0.0), 6),
        "actual_cost_usd": round(float(row.get("actual_cost") or 0.0), 6),
    } for row in usage.get("daily") or []]

    total_input = int(totals.get("total_input") or 0)
    total_output = int(totals.get("total_output") or 0)
    total_cache = int(totals.get("total_cache_read") or 0)
    total_reasoning = int(totals.get("total_reasoning") or 0)
    out.update(
        available=True,
        detail=f"{len(models)} model(s) over {int(usage.get('days') or window)} day(s)",
        period_days=int(usage.get("days") or window),
        models=models,
        total_models=len(models),
        total_sessions=int(totals.get("total_sessions") or 0),
        total_tokens=(total_input + total_output + total_cache + total_reasoning),
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_cache_read_tokens=total_cache,
        total_reasoning_tokens=total_reasoning,
        total_estimated_cost_usd=round(
            float(totals.get("total_estimated_cost") or 0.0), 6),
        total_actual_cost_usd=round(
            float(totals.get("total_actual_cost") or 0.0), 6),
        daily=daily,
    )
    return out


__all__ = ["collect_model_analytics"]
