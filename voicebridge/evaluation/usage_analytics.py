"""OC-50 — real analytics / usage accounting.

Token and cost accounting over an append-only JSONL ledger. A record is
immutable once written and carries a content-addressed ``record_id``; the
aggregations (totals, per-model, per-provider, per-day) are computed from the
records themselves, so no route can report a number that was not recorded.

No secrets, prompts, or responses are stored — only counts, model/provider
labels, and cost.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SCHEMA = "qubit-usage-analytics/1"
MAX_RECORDS = 100_000


class UsageError(Exception):
    """Typed analytics failure."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _nonneg(value: Any, name: str) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise UsageError("USAGE_BAD_RECORD", f"{name} must be numeric")
    if num < 0:
        raise UsageError("USAGE_BAD_RECORD", f"{name} must be >= 0")
    return num


class UsageRecord:
    """One metered model call (token/cost accounting; no content)."""

    __slots__ = ("product", "model", "provider", "input_tokens",
                 "output_tokens", "cost_usd", "run_id", "actor", "ts",
                 "record_id")

    def __init__(self, *, product: str, model: str, provider: str = "",
                 input_tokens: int = 0, output_tokens: int = 0,
                 cost_usd: float = 0.0, run_id: str = "", actor: str = "user",
                 ts: Optional[float] = None, record_id: str = "") -> None:
        self.product = str(product or "unknown")
        self.model = str(model or "unknown")
        self.provider = str(provider or "")
        self.input_tokens = int(_nonneg(input_tokens, "input_tokens"))
        self.output_tokens = int(_nonneg(output_tokens, "output_tokens"))
        self.cost_usd = round(_nonneg(cost_usd, "cost_usd"), 8)
        self.run_id = str(run_id or "")
        self.actor = str(actor or "user")
        self.ts = float(ts if ts is not None else time.time())
        body = self._body()
        self.record_id = record_id or _digest(body)

    def _body(self) -> Dict[str, Any]:
        return {"product": self.product, "model": self.model,
                "provider": self.provider, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "cost_usd": self.cost_usd,
                "run_id": self.run_id, "actor": self.actor, "ts": self.ts}

    def as_dict(self) -> Dict[str, Any]:
        return {"schema": SCHEMA, "record_id": self.record_id,
                **self._body()}

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UsageRecord":
        if not isinstance(data, dict):
            raise UsageError("USAGE_BAD_RECORD", "record must be an object")
        return cls(product=data.get("product", "unknown"),
                   model=data.get("model", "unknown"),
                   provider=data.get("provider", ""),
                   input_tokens=data.get("input_tokens", 0),
                   output_tokens=data.get("output_tokens", 0),
                   cost_usd=data.get("cost_usd", 0.0),
                   run_id=data.get("run_id", ""),
                   actor=data.get("actor", "user"),
                   ts=data.get("ts"))


class UsageLedger:
    """Append-only JSONL usage ledger (0600)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.rejections = 0

    def record(self, rec: UsageRecord) -> Dict[str, Any]:
        if not isinstance(rec, UsageRecord):
            raise UsageError("USAGE_BAD_RECORD", "expected a UsageRecord")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(rec.as_dict(), sort_keys=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        return rec.as_dict()

    def read(self, *, limit: int = MAX_RECORDS) -> List[UsageRecord]:
        if not self.path.exists():
            return []
        out: List[UsageRecord] = []
        for line in self.path.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                out.append(UsageRecord.from_dict(json.loads(line)))
            except (ValueError, UsageError):
                self.rejections += 1
        return out[-int(limit):]

    def analytics(self) -> Dict[str, Any]:
        return analyze(self.read())


def analyze(records: Iterable[UsageRecord]) -> Dict[str, Any]:
    """Aggregate token/cost usage by model, provider, and day."""
    records = list(records)
    total_in = total_out = 0
    total_cost = 0.0
    by_model: Dict[str, Dict[str, float]] = {}
    by_provider: Dict[str, Dict[str, float]] = {}
    by_day: Dict[str, Dict[str, float]] = {}
    for r in records:
        total_in += r.input_tokens
        total_out += r.output_tokens
        total_cost += r.cost_usd
        for bucket, key in ((by_model, r.model or "unknown"),
                            (by_provider, r.provider or "unset"),
                            (by_day, _day(r.ts))):
            b = bucket.setdefault(key, {"calls": 0, "input_tokens": 0,
                                        "output_tokens": 0, "cost_usd": 0.0})
            b["calls"] += 1
            b["input_tokens"] += r.input_tokens
            b["output_tokens"] += r.output_tokens
            b["cost_usd"] = round(b["cost_usd"] + r.cost_usd, 8)
    return {
        "schema": SCHEMA,
        "calls": len(records),
        "input_tokens": total_in,
        "output_tokens": total_out,
        "total_tokens": total_in + total_out,
        "cost_usd": round(total_cost, 8),
        "by_model": by_model,
        "by_provider": by_provider,
        "by_day": by_day,
    }


def _day(ts: float) -> str:
    import datetime as _dt

    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%d")
