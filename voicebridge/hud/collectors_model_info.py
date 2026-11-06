"""HUD model-info collector — Qubit-native port of ``backend/collectors/model_info.py``.

Vendored from ``joeynyc/hermes-hudui`` @ be83208f48b7a1c67b7816837c8df71a61ef7edf
Copyright (c) 2026 Hermes HUD Contributors. MIT licensed; the file-by-file
record is ``PROVENANCE.json`` beside this package. Rebranded and re-pointed at
QUBIT state.

What changed from upstream, and why
-----------------------------------
* **Catalog lookup** — upstream hand-parsed ``models_dev_cache.json`` and did its
  own provider/model scan with a blind fallback over every provider. Qubit ships
  a models.dev client that owns the provider→catalog-id map
  (:data:`agent.models_dev.PROVIDER_TO_MODELS_DEV`), the ``model_overrides``
  patching and the suffix/routing fallbacks. This collector asks that client
  (``allow_network=False``, the cost-guarded disk path), so the panel shows the
  same metadata the agent itself would use — not a second, drifting opinion.
* **Config read** — upstream parsed ``config.yaml`` itself. :mod:`voicebridge.hud.
  collectors_health` reads the model section through Qubit's YAML and this
  collector reuses that helper, so both tabs agree on the configured model.
* **``effective_context_length``** — kept, with the same rule as upstream
  (``max(configured, catalog)``) because that is how the runtime sizes a turn.

Pure function, JSON-ready dict, never raises: a missing catalog entry reports
``found: false`` with the config-derived facts rather than inventing capabilities.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from .collectors_health import _config_summary, _ensure_vendored


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def collect_model_info(home: Optional[Path] = None, cfg: Any = None,
                       *, model: str = "", provider: str = "") -> Dict[str, Any]:
    """The configured model and the capabilities Qubit knows for it.

    ``home`` overrides the Qubit home used for the ``config.yaml`` read; an
    explicit ``model``/``provider`` axis overrides the configured one (the panel
    uses it to inspect any model in the catalog).
    """
    out: Dict[str, Any] = {
        "model": "",
        "provider": "",
        "catalog_provider": "",
        "found": False,
        "detail": "",
        "family": "",
        "supports_tools": False,
        "supports_vision": False,
        "supports_reasoning": False,
        "supports_structured_output": False,
        "modalities": {"input": [], "output": []},
        "config_context_length": 0,
        "auto_context_length": 0,
        "effective_context_length": 0,
        "max_output_tokens": 0,
        "cost_input_per_m": None,
        "cost_output_per_m": None,
        "cost_cache_read_per_m": None,
        "cost_cache_write_per_m": None,
        "release_date": "",
        "knowledge_cutoff": "",
        "status": "",
    }
    root = Path(home) if home is not None else None
    if root is None:
        try:
            _ensure_vendored()
            from qubit_constants import get_qubit_home  # type: ignore

            root = Path(get_qubit_home())
        except Exception as exc:  # noqa: BLE001 - no home means no config facts
            out["detail"] = f"Qubit home unavailable ({exc})"
            return out

    config = _config_summary(root)
    out["model"] = (model or config["model"]).strip()
    out["provider"] = (provider or config["provider"]).strip()

    # The configured context length lives under ``model.context_length``.
    try:
        import yaml  # noqa: PLC0415

        data = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8")) or {}
        section = data.get("model") if isinstance(data, dict) else None
        if isinstance(section, dict):
            out["config_context_length"] = _int(section.get("context_length"))
    except Exception:  # noqa: BLE001 - a config without the key is normal
        pass

    if not out["model"]:
        out["detail"] = "no model configured"
        return out

    try:
        _ensure_vendored()
        from agent.models_dev import (  # type: ignore
            get_model_info, models_dev_id_for)

        info = get_model_info(out["provider"], out["model"],
                              allow_network=False, config=cfg)
        out["catalog_provider"] = models_dev_id_for(out["provider"]) or out["provider"]
    except Exception as exc:  # noqa: BLE001 - a catalog miss is not a failure
        out["effective_context_length"] = out["config_context_length"]
        out["detail"] = f"model catalog unavailable ({exc})"
        return out

    if info is None:
        out["effective_context_length"] = out["config_context_length"]
        out["detail"] = ("not in the local models.dev catalog; showing the "
                         "configured values")
        return out

    out.update(
        found=True,
        detail="models.dev catalog",
        family=str(getattr(info, "family", "") or ""),
        supports_tools=bool(getattr(info, "tool_call", False)),
        supports_vision=bool(getattr(info, "attachment", False)),
        supports_reasoning=bool(getattr(info, "reasoning", False)),
        supports_structured_output=bool(getattr(info, "structured_output", False)),
        modalities={
            "input": list(getattr(info, "input_modalities", ()) or ()),
            "output": list(getattr(info, "output_modalities", ()) or ()),
        },
        auto_context_length=_int(getattr(info, "context_window", 0)),
        max_output_tokens=_int(getattr(info, "max_output", 0)),
        cost_input_per_m=_float(getattr(info, "cost_input", None)),
        cost_output_per_m=_float(getattr(info, "cost_output", None)),
        cost_cache_read_per_m=_float(getattr(info, "cost_cache_read", None)),
        cost_cache_write_per_m=_float(getattr(info, "cost_cache_write", None)),
        release_date=str(getattr(info, "release_date", "") or ""),
        knowledge_cutoff=str(getattr(info, "knowledge_cutoff", "") or ""),
        status=str(getattr(info, "status", "") or ""),
    )
    out["effective_context_length"] = max(out["config_context_length"],
                                          out["auto_context_length"])
    return out


__all__ = ["collect_model_info"]
