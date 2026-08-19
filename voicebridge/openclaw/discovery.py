"""Discover the existing local OpenClaw gateway without changing its config.

Reads the live installation:
  * gateway port + token  -> ~/.openclaw/.env (OPENCLAW_GATEWAY_TOKEN) and
                             ~/.openclaw/openclaw.json (gateway.port)
  * default agent         -> openclaw.json agents.defaults / agents.entries
  * registered models     -> openclaw.json models.providers (for tier mapping)
Everything is read-only. The bridge never writes OpenClaw config.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..logging_setup import log_event

OPENCLAW_DIR = Path("~/.openclaw").expanduser()


@dataclass
class GatewayInfo:
    url: str
    token: str
    port: int
    agent: str = "main"
    version: str = ""
    default_model: str = ""
    models: Dict[str, str] = field(default_factory=dict)  # tier alias -> provider/model


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return {}


def read_gateway_token(env_path: Optional[Path] = None) -> str:
    """Read the gateway token from ~/.openclaw/.env (or gateway.systemd.env)."""
    prefix = "OPENCLAW_GATEWAY_" + "TOKEN"
    for candidate in (env_path, OPENCLAW_DIR / ".env", OPENCLAW_DIR / "gateway.systemd.env"):
        if not candidate:
            continue
        try:
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if line.startswith(prefix + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


def discover_gateway(config) -> GatewayInfo:
    """Auto-discover gateway URL/token/agent/models from the local install."""
    cfg = config.section("openclaw")
    token = read_gateway_token(Path(cfg.get("token_file")) if cfg.get("token_file") else None)

    port = int(config.get("openclaw", "gateway_port") or 18789)
    openclaw_json = OPENCLAW_DIR / "openclaw.json"
    if openclaw_json.exists():
        data = _read_json(openclaw_json)
        gw = data.get("gateway", {})
        if gw.get("port"):
            port = int(gw["port"])
        defaults = data.get("agents", {}).get("defaults", {})
        model_cfg = defaults.get("model", {})
        default_model = model_cfg.get("primary", "")
        agent = cfg.get("agent") or defaults.get("defaultAgentId") or "main"

        # tier aliases from agents.defaults.models
        tier_map: Dict[str, str] = {}
        for mid, meta in (defaults.get("models") or {}).items():
            if isinstance(meta, dict) and meta.get("alias"):
                tier_map[meta["alias"]] = mid
        models = tier_map
    else:
        agent = cfg.get("agent") or "main"
        default_model = ""
        models = {}

    url = cfg.get("gateway_url") or f"ws://127.0.0.1:{port}"
    info = GatewayInfo(url=url, token=token, port=port, agent=agent,
                       default_model=default_model, models=models)
    log_event("openclaw.discovery", url=url, port=port, agent=agent,
              default_model=default_model, token_found=bool(token),
              tier_aliases=sorted(models.keys()))
    return info


def discover_cuda_libs() -> Optional[str]:
    """Locate a directory containing libcublas.so.12 (see asr.whisper)."""
    for cand in ["/usr/local/lib/ollama/cuda_v12", "/usr/lib/x86_64-linux-gnu"]:
        if os.path.exists(os.path.join(cand, "libcublas.so.12")):
            return cand
    return None


def current_vram_mb() -> int:
    """Free VRAM in MiB via nvidia-smi (best-effort)."""
    try:
        import subprocess
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return int(out.splitlines()[0])
    except Exception:
        return -1
