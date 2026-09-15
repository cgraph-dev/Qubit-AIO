"""Packet 8Q — routing policy, budgets, circuit breakers, spend ledger.

Cloud is disabled by default. Enabling requires explicit config:
enabled=true + provider/model allowlist + privacy tier + limits + budget.
Auto-escalation never triggers from page/tool/model/provider text.

The spend/rate/circuit ledger is PROCESS-LOCAL (explicit limitation): a
restart resets in-flight, per-minute, daily/monthly spend, idempotency, and
breaker state. Budgets are therefore enforced per process lifetime; the
governor never over-admits within one run but cannot carry spend across
restarts. Operators must treat reported spend as a lower bound after any
restart.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional



def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class FrontierPolicy:
    enabled: bool = False
    route: str = "local"  # local | frontier | auto (configured default)
    providers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    allowlist: List[str] = field(default_factory=list)  # "provider/model"
    privacy_tier: str = "standard"  # minimal | standard — recorded, shown
    max_tokens: int = 512
    max_context_chars: int = 4000
    max_response_chars: int = 4000
    timeout_s: float = 20.0
    max_retries: int = 1
    rpm: int = 10
    max_concurrency: int = 2
    daily_usd: float = 1.0
    monthly_usd: float = 10.0
    catalog_ttl_s: float = 24 * 3600.0
    disclosure: bool = True

    @classmethod
    def from_config(cls, cfg: Any) -> "FrontierPolicy":
        def get(*path: str, default: Any = None) -> Any:
            try:
                return cfg.get(*path, default=default)
            except Exception:
                return default
        node = get("intent", "frontier", default=None) or {}
        if not isinstance(node, dict):
            node = {}
        provs = node.get("providers") or {}
        if not isinstance(provs, dict):
            provs = {}
        allow = node.get("allowlist") or []
        if not isinstance(allow, list):
            allow = []
        def _num(node: dict, key: str, default: float) -> float:
            v = node.get(key, default)
            if v is None or (isinstance(v, str) and not v.strip()):
                return default
            try:
                return type(default)(v)
            except (TypeError, ValueError):
                return default

        return cls(
            enabled=_as_bool(node.get("enabled", False)),
            route=str(node.get("route", "local") or "local").lower(),
            providers={str(k): (v if isinstance(v, dict) else {}) for k, v in provs.items()},
            allowlist=[str(x) for x in allow],
            privacy_tier=str(node.get("privacy_tier", "standard") or "standard"),
            max_tokens=int(_num(node, "max_tokens", 512)),
            max_context_chars=int(_num(node, "max_context_chars", 4000)),
            max_response_chars=int(_num(node, "max_response_chars", 4000)),
            timeout_s=float(_num(node, "timeout_s", 20.0)),
            max_retries=int(_num(node, "max_retries", 1)),
            rpm=int(_num(node, "rpm", 10)),
            max_concurrency=int(_num(node, "max_concurrency", 2)),
            daily_usd=float(_num(node, "daily_usd", 1.0)),
            monthly_usd=float(_num(node, "monthly_usd", 10.0)),
        )

    def provider_cfg(self, provider: str) -> Dict[str, Any]:
        return self.providers.get(provider, {}) or {}

    def is_allowed(self, provider: str, model: str) -> bool:
        if not self.enabled:
            return False
        if not self.allowlist:
            return False
        return f"{provider}/{model}" in self.allowlist

    def effective_route(self, requested: str) -> str:
        req = (requested or self.route or "local").lower()
        if req not in ("local", "frontier", "auto"):
            return "local"
        if req in ("frontier", "auto") and not self.enabled:
            return "local"
        return req


class _Breaker:
    # A single transient failure must not silence a provider for a minute;
    # the circuit opens only after TRIP_AFTER consecutive failures.
    TRIP_AFTER = 3

    def __init__(self) -> None:
        self.failures = 0
        self.open_until = 0.0

    def record_fail(self, cooldown_s: float = 60.0) -> None:
        self.failures += 1
        if self.failures >= self.TRIP_AFTER:
            self.open_until = time.time() + cooldown_s

    def record_ok(self) -> None:
        self.failures = 0
        self.open_until = 0.0

    def is_open(self) -> bool:
        return time.time() < self.open_until


class Governor:
    """Rate, concurrency, spend, and circuit-break state (process-local)."""

    def __init__(self, state_path: Optional[Path] = None) -> None:
        self._lock = threading.Lock()
        self._req_times: List[float] = []
        self._inflight = 0
        self._breakers: Dict[str, str | float | _Breaker] = {}
        self._spend_day = 0.0
        self._spend_month = 0.0
        self._spend_day_key = time.strftime("%Y-%m-%d")
        self._spend_month_key = time.strftime("%Y-%m")
        self._state_path = state_path
        self._seen_idempotency: Dict[str, float] = {}

    def _breaker(self, provider: str) -> _Breaker:
        b = self._breakers.get(provider)
        if not isinstance(b, _Breaker):
            b = _Breaker()
            self._breakers[provider] = b
        return b

    def check(self, policy: FrontierPolicy, provider: str,
              est_cost: float = 0.0) -> tuple[bool, str]:
        now = time.time()
        with self._lock:
            day = time.strftime("%Y-%m-%d")
            month = time.strftime("%Y-%m")
            if day != self._spend_day_key:
                self._spend_day_key = day
                self._spend_day = 0.0
            if month != self._spend_month_key:
                self._spend_month_key = month
                self._spend_month = 0.0
            if self._breaker(provider).is_open():
                return False, "CIRCUIT_OPEN"
            if self._inflight >= max(1, policy.max_concurrency):
                return False, "RATE_LIMITED"
            window = [t for t in self._req_times if now - t < 60.0]
            self._req_times = window
            if len(window) >= max(1, policy.rpm):
                return False, "RATE_LIMITED"
            if (self._spend_day + est_cost) > policy.daily_usd:
                return False, "OVER_BUDGET"
            if (self._spend_month + est_cost) > policy.monthly_usd:
                return False, "OVER_BUDGET"
            return True, ""

    def acquire(self) -> bool:
        with self._lock:
            self._inflight += 1
            self._req_times.append(time.time())
            return True

    def release(self, provider: str, ok: bool, cost: float = 0.0) -> None:
        with self._lock:
            self._inflight = max(0, self._inflight - 1)
            b = self._breaker(provider)
            if ok:
                b.record_ok()
                self._spend_day += max(0.0, cost)
                self._spend_month += max(0.0, cost)
            else:
                b.record_fail()

    def replay_seen(self, key: str) -> bool:
        """Retries must not duplicate external actions: same key = replay."""
        if not key:
            return False
        now = time.time()
        with self._lock:
            ts = self._seen_idempotency.get(key)
            # prune
            for k in [k for k, v in self._seen_idempotency.items() if now - v > 3600]:
                self._seen_idempotency.pop(k, None)
            if ts is not None:
                return True
            self._seen_idempotency[key] = now
            return False


_GOVERNOR = Governor()


def get_governor() -> Governor:
    return _GOVERNOR


def reset_governor() -> None:
    global _GOVERNOR
    _GOVERNOR = Governor()


def disclosure_block(policy: FrontierPolicy, provider: str, model: str,
                     est_cost: float) -> str:
    return (
        f"provider={provider} model={model} privacy={policy.privacy_tier} "
        f"est_cost_usd~{est_cost:.4f} | external-data warning: only sanitized "
        f"task context leaves the machine; availability and price subject to "
        f"provider account; never free/unlimited by default"
    )
