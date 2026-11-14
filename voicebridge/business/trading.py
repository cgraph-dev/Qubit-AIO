"""Paper/testnet trading readiness for the Qubit Office trading department.

This module is a **simulation and readiness** boundary. It never talks to an
exchange, never holds a credential and never places a live order. Live capital
is a separate owner gate: :class:`TradingSystem` refuses ``live`` mode unless an
:class:`OwnerRiskPolicySignoff` matches the exact risk policy and every supplied
exchange key is trade-only with withdrawals disabled.

The pieces the trading department plan requires:

* :class:`MarketDataService` — trusted-clock snapshots from an injected feed,
  with a typed stale-data refusal.
* :class:`StrategyRegistry` — hypothesis/universe/timeframe/entry/exit/fees/
  slippage/risk/version/status records.
* :class:`BacktestEngine` — deterministic, no look-ahead (signal on bar *i*,
  execute on bar *i+1* only), fees/slippage, in-sample/out-of-sample split and
  per-regime metrics.
* :class:`PaperBroker` — simulated latency, slippage, fees, partial fills,
  rejection and an explicit ``unknown`` outcome.
* :class:`OrderManagementSystem` — order state machine with client-order
  idempotency and reconciliation of ``unknown`` outcomes.
* :class:`RiskEngine` — pre-trade notional/leverage/concentration/liquidity/
  stale-data checks plus portfolio drawdown and daily-loss limits; a veto stops
  execution. An execution role cannot change the limits.
* :class:`Portfolio` — double-entry ledger for positions, cost basis, fees,
  funding and realised/unrealised PnL.
* :class:`KillSwitch` — cancel-all plus injected lease/key revocation callbacks.

Every order, fill, broker receipt and risk veto is written to an append-only,
hash-chained event log; every fill and funding payment is written to a balanced
double-entry journal tied to its event. A typed :class:`TradingError` is the only
failure surface — no bare traceback is a contract.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = [
    "Actor",
    "Auditor",
    "BacktestEngine",
    "BacktestMetrics",
    "BacktestResult",
    "BacktestTrade",
    "BrokerFill",
    "BrokerResult",
    "DoubleEntryLedger",
    "EventLog",
    "EventRecord",
    "ExchangeKeyPolicy",
    "ExecutionReport",
    "JournalEntry",
    "KillSwitch",
    "KillSwitchReport",
    "MarketBar",
    "MarketDataService",
    "MarketSnapshot",
    "Order",
    "OrderManagementSystem",
    "OrderRequest",
    "OwnerRiskPolicySignoff",
    "PaperBroker",
    "PaperBrokerConfig",
    "Portfolio",
    "Posting",
    "Position",
    "RiskCheck",
    "RiskDecision",
    "RiskEngine",
    "RiskPolicy",
    "Role",
    "StrategyRegistry",
    "StrategySpec",
    "TradingError",
    "TradingMode",
    "TradingSystem",
]

# ---------------------------------------------------------------------------
# Typed refusals
# ---------------------------------------------------------------------------

BAD_INPUT = "TRADING_BAD_INPUT"
BAD_STATE = "TRADING_BAD_STATE"
STALE_DATA = "TRADING_STALE_DATA"
FEED_UNAVAILABLE = "TRADING_FEED_UNAVAILABLE"
LIVE_NOT_AUTHORIZED = "TRADING_LIVE_NOT_AUTHORIZED"
MODE_REFUSED = "TRADING_MODE_REFUSED"
RISK_VETO = "TRADING_RISK_VETO"
RISK_LIMIT_IMMUTABLE = "TRADING_RISK_LIMIT_IMMUTABLE"
ROLE_REFUSED = "TRADING_ROLE_REFUSED"
KEY_WITHDRAWAL_REFUSED = "TRADING_KEY_WITHDRAWAL_REFUSED"
KILL_SWITCH_ENGAGED = "TRADING_KILL_SWITCH_ENGAGED"
ORDER_NOT_FOUND = "TRADING_ORDER_NOT_FOUND"
ORDER_STATE = "TRADING_ORDER_STATE"
IDEMPOTENCY_CONFLICT = "TRADING_IDEMPOTENCY_CONFLICT"
STRATEGY_UNKNOWN = "TRADING_STRATEGY_UNKNOWN"
STRATEGY_EXISTS = "TRADING_STRATEGY_EXISTS"
UNKNOWN_OUTCOME = "TRADING_UNKNOWN_OUTCOME"
BROKER_REJECTED = "TRADING_BROKER_REJECTED"


class TradingError(Exception):
    """A typed trading refusal. ``code`` is stable API, ``detail`` is human."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail

    def as_dict(self, operation: str = "") -> dict[str, Any]:
        return {
            "state": "REFUSED",
            "code": self.code,
            "operation": operation or None,
            "explanation": self.detail,
            "live_capital_touched": False,
        }


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

_CENT = Decimal("100")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool):
        raise TradingError(BAD_INPUT, f"{label} must be numeric, not a bool")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise TradingError(BAD_INPUT, f"{label} is not a valid decimal") from exc
    if not result.is_finite():
        raise TradingError(BAD_INPUT, f"{label} must be finite")
    return result


def _positive_decimal(value: Any, label: str) -> Decimal:
    result = _decimal(value, label)
    if result <= _ZERO:
        raise TradingError(BAD_INPUT, f"{label} must be greater than zero")
    return result


def _non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TradingError(BAD_INPUT, f"{label} must be a non-negative integer")
    return value


def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TradingError(BAD_INPUT, f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len:
        raise TradingError(
            BAD_INPUT, f"{label} is longer than {max_len} characters"
        )
    return clean


def _minor(amount: Decimal) -> int:
    """Convert an amount in major units to integer minor units (half-up)."""
    return int((amount * _CENT).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _canonical(value: Any) -> str:
    return json.dumps(
        _jsonable(value), sort_keys=True, separators=(",", ":"), default=str
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc(ts: float) -> str:
    return (
        datetime.fromtimestamp(float(ts), tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _day(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def _new_id(prefix: str, *, at: float, seed: str) -> str:
    """Deterministic, sortable id derived from a trusted timestamp and seed."""
    millis = int(float(at) * 1000)
    body = hashlib.sha256(
        f"{prefix}:{millis}:{seed}".encode("utf-8")
    ).hexdigest()[:26].upper()
    return f"{prefix}_{millis:013d}_{body}"


# ---------------------------------------------------------------------------
# Roles and modes
# ---------------------------------------------------------------------------


class TradingMode(str, Enum):
    PAPER = "paper"
    TESTNET = "testnet"
    LIVE = "live"


class Role(str, Enum):
    EXECUTION = "execution"
    RISK_MANAGER = "risk_manager"
    AUDITOR = "auditor"
    OWNER = "owner"


@dataclass(frozen=True, slots=True)
class Actor:
    actor_id: str
    role: Role

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "actor_id", _text(self.actor_id, "actor_id", max_len=200)
        )
        if not isinstance(self.role, Role):
            object.__setattr__(self, "role", Role(self.role))


# ---------------------------------------------------------------------------
# Exchange key capability policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExchangeKeyPolicy:
    """A trade-only capability record; withdrawals are never permitted."""

    key_id: str
    venue: str
    trade_only: bool = True
    withdrawals_enabled: bool = False
    scopes: tuple[str, ...] = ("trade",)

    def __post_init__(self) -> None:
        object.__setattr__(self, "key_id", _text(self.key_id, "key_id"))
        object.__setattr__(self, "venue", _text(self.venue, "venue"))
        object.__setattr__(
            self,
            "scopes",
            tuple(str(item).strip().lower() for item in self.scopes),
        )

    def assert_trade_only(self) -> None:
        if (
            not self.trade_only
            or self.withdrawals_enabled
            or "withdraw" in self.scopes
            or "withdrawal" in self.scopes
        ):
            raise TradingError(
                KEY_WITHDRAWAL_REFUSED,
                f"exchange key {self.key_id!r} on {self.venue!r} is not"
                " trade-only; withdrawals must be disabled",
            )


# ---------------------------------------------------------------------------
# Event log (append-only, hash-chained)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EventRecord:
    seq: int
    event_type: str
    at: float
    actor: str
    payload: Mapping[str, Any]
    prev_hash: str
    record_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "event_type": self.event_type,
            "at": self.at,
            "actor": self.actor,
            "payload": _jsonable(self.payload),
            "prev_hash": self.prev_hash,
            "record_hash": self.record_hash,
        }


_GENESIS = "0" * 64


class EventLog:
    """Append-only event chain; each record commits to the one before it."""

    def __init__(self) -> None:
        self._records: list[EventRecord] = []

    def append(
        self,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str,
        at: float,
    ) -> EventRecord:
        kind = _text(event_type, "event_type", max_len=120)
        moment = float(at)
        prev = self._records[-1].record_hash if self._records else _GENESIS
        seq = len(self._records) + 1
        body = {
            "seq": seq,
            "event_type": kind,
            "at": round(moment, 6),
            "actor": _text(actor, "actor", max_len=200),
            "payload": _jsonable(payload or {}),
            "prev_hash": prev,
        }
        record = EventRecord(
            seq=seq,
            event_type=kind,
            at=moment,
            actor=body["actor"],
            payload=dict(payload or {}),
            prev_hash=prev,
            record_hash=_digest(body),
        )
        self._records.append(record)
        return record

    def records(self) -> tuple[EventRecord, ...]:
        return tuple(self._records)

    def of_type(self, event_type: str) -> tuple[EventRecord, ...]:
        return tuple(r for r in self._records if r.event_type == event_type)

    def verify(self) -> bool:
        prev = _GENESIS
        for index, record in enumerate(self._records, start=1):
            if record.seq != index or record.prev_hash != prev:
                return False
            body = {
                "seq": record.seq,
                "event_type": record.event_type,
                "at": round(record.at, 6),
                "actor": record.actor,
                "payload": _jsonable(record.payload),
                "prev_hash": record.prev_hash,
            }
            if _digest(body) != record.record_hash:
                return False
            prev = record.record_hash
        return True

    def __len__(self) -> int:
        return len(self._records)


# ---------------------------------------------------------------------------
# Double-entry ledger
# ---------------------------------------------------------------------------

_DIRECTIONS = ("debit", "credit")


@dataclass(frozen=True, slots=True)
class Posting:
    account: str
    direction: str
    amount_minor: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "account", _text(self.account, "account", max_len=120))
        side = _text(self.direction, "direction", max_len=10).lower()
        if side not in _DIRECTIONS:
            raise TradingError(BAD_INPUT, "direction must be debit or credit")
        object.__setattr__(self, "direction", side)
        object.__setattr__(
            self, "amount_minor", _non_negative_int(self.amount_minor, "amount")
        )


@dataclass(frozen=True, slots=True)
class JournalEntry:
    journal_id: str
    at: float
    kind: str
    memo: str
    postings: tuple[Posting, ...]
    event_seq: int | None
    refs: Mapping[str, Any] = field(default_factory=dict)

    @property
    def debit_minor(self) -> int:
        return sum(p.amount_minor for p in self.postings if p.direction == "debit")

    @property
    def credit_minor(self) -> int:
        return sum(p.amount_minor for p in self.postings if p.direction == "credit")

    def to_dict(self) -> dict[str, Any]:
        return {
            "journal_id": self.journal_id,
            "at": self.at,
            "kind": self.kind,
            "memo": self.memo,
            "postings": [
                {
                    "account": p.account,
                    "direction": p.direction,
                    "amount_minor": p.amount_minor,
                }
                for p in self.postings
            ],
            "event_seq": self.event_seq,
            "refs": _jsonable(self.refs),
            "debit_minor": self.debit_minor,
            "credit_minor": self.credit_minor,
        }


class DoubleEntryLedger:
    """Balanced journals with signed account balances (debit - credit)."""

    def __init__(self) -> None:
        self._journals: list[JournalEntry] = []
        self._debits: dict[str, int] = {}
        self._credits: dict[str, int] = {}

    def post(
        self,
        *,
        kind: str,
        memo: str,
        postings: Iterable[Posting],
        at: float,
        event_seq: int | None = None,
        refs: Mapping[str, Any] | None = None,
    ) -> JournalEntry:
        items = tuple(postings)
        if not items:
            raise TradingError(BAD_INPUT, "a journal needs at least one posting")
        debit = sum(p.amount_minor for p in items if p.direction == "debit")
        credit = sum(p.amount_minor for p in items if p.direction == "credit")
        if debit != credit:
            raise TradingError(
                BAD_INPUT,
                f"journal is not balanced: debit={debit} credit={credit}",
            )
        if debit == 0:
            raise TradingError(BAD_INPUT, "a zero-value journal is not a posting")
        journal = JournalEntry(
            journal_id=_new_id(
                "jrnl",
                at=at,
                seed=f"{kind}:{len(self._journals)}:{memo}",
            ),
            at=float(at),
            kind=_text(kind, "kind", max_len=80),
            memo=str(memo or "")[:500],
            postings=items,
            event_seq=event_seq,
            refs=dict(refs or {}),
        )
        for posting in items:
            if posting.direction == "debit":
                self._debits[posting.account] = (
                    self._debits.get(posting.account, 0) + posting.amount_minor
                )
            else:
                self._credits[posting.account] = (
                    self._credits.get(posting.account, 0) + posting.amount_minor
                )
        self._journals.append(journal)
        return journal

    def journals(self) -> tuple[JournalEntry, ...]:
        return tuple(self._journals)

    def debit_total(self, account: str) -> int:
        return self._debits.get(account, 0)

    def credit_total(self, account: str) -> int:
        return self._credits.get(account, 0)

    def balance(self, account: str) -> int:
        """Signed balance: debits minus credits (asset-positive)."""
        return self._debits.get(account, 0) - self._credits.get(account, 0)

    def accounts(self) -> dict[str, int]:
        names = set(self._debits) | set(self._credits)
        return {name: self.balance(name) for name in sorted(names)}

    def total_minor(self) -> int:
        return sum(self.accounts().values())

    def is_balanced(self) -> bool:
        return self.total_minor() == 0

    def totals(self) -> dict[str, int]:
        return {
            "debit_minor": sum(self._debits.values()),
            "credit_minor": sum(self._credits.values()),
            "net_minor": self.total_minor(),
            "journals": len(self._journals),
        }


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MarketSnapshot:
    taken_at: float
    source: str
    marks: dict[str, Decimal]
    bids: dict[str, Decimal]
    asks: dict[str, Decimal]
    liquidity_notional_minor: dict[str, int]

    def __post_init__(self) -> None:
        self.taken_at = float(self.taken_at)
        self.source = _text(self.source, "source", max_len=120)
        self.marks = {
            _text(k, "symbol", max_len=32).upper(): _positive_decimal(v, "mark")
            for k, v in self.marks.items()
        }
        self.bids = {
            _text(k, "symbol", max_len=32).upper(): _positive_decimal(v, "bid")
            for k, v in self.bids.items()
        }
        self.asks = {
            _text(k, "symbol", max_len=32).upper(): _positive_decimal(v, "ask")
            for k, v in self.asks.items()
        }
        self.liquidity_notional_minor = {
            _text(k, "symbol", max_len=32).upper(): _non_negative_int(
                v, "liquidity"
            )
            for k, v in self.liquidity_notional_minor.items()
        }

    def price(self, symbol: str, side: str) -> Decimal:
        symbol = symbol.upper()
        book = self.asks if side == "buy" else self.bids
        if symbol not in book:
            raise TradingError(
                STALE_DATA, f"snapshot has no {side} price for {symbol}"
            )
        return book[symbol]

    def liquidity(self, symbol: str) -> int:
        return int(self.liquidity_notional_minor.get(symbol.upper(), 0))


class MarketDataService:
    """Trusted-clock timestamped snapshots with a stale-data refusal."""

    def __init__(
        self,
        feed: Callable[[], MarketSnapshot],
        clock: Callable[[], float],
        *,
        max_stale_seconds: float = 5.0,
        future_tolerance_seconds: float = 1.0,
    ) -> None:
        if not callable(feed) or not callable(clock):
            raise TradingError(BAD_INPUT, "feed and clock must be callable")
        self._feed = feed
        self._clock = clock
        self._max_stale = float(max_stale_seconds)
        self._future_tolerance = float(future_tolerance_seconds)
        self._last: MarketSnapshot | None = None

    def snapshot(self, symbols: Sequence[str] | None = None) -> MarketSnapshot:
        now = float(self._clock())
        try:
            snapshot = self._feed()
        except TradingError:
            raise
        except Exception as exc:  # noqa: BLE001 - injected feed boundary
            raise TradingError(
                FEED_UNAVAILABLE, f"market feed failed: {exc}"
            ) from exc
        if not isinstance(snapshot, MarketSnapshot):
            raise TradingError(
                FEED_UNAVAILABLE, "market feed returned an invalid snapshot"
            )
        age = now - snapshot.taken_at
        if snapshot.taken_at > now + self._future_tolerance:
            raise TradingError(
                STALE_DATA,
                f"snapshot {snapshot.source!r} is timestamped in the future",
            )
        if age > self._max_stale:
            raise TradingError(
                STALE_DATA,
                f"snapshot {snapshot.source!r} is {age:.3f}s old;"
                f" limit is {self._max_stale:.3f}s",
            )
        for symbol in symbols or ():
            if symbol.upper() not in snapshot.marks:
                raise TradingError(
                    STALE_DATA, f"snapshot has no mark for {symbol!r}"
                )
        self._last = snapshot
        return snapshot

    @property
    def last(self) -> MarketSnapshot | None:
        return self._last


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

STRATEGY_STATUSES = ("draft", "validated", "paper", "retired")


@dataclass(frozen=True, slots=True)
class StrategySpec:
    strategy_id: str
    hypothesis: str
    universe: tuple[str, ...]
    timeframe: str
    entry: str
    exit: str
    fees_bps: int
    slippage_bps: int
    risk: Mapping[str, Any]
    version: str
    status: str = "draft"
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "strategy_id", _text(self.strategy_id, "strategy_id", max_len=80)
        )
        object.__setattr__(
            self, "hypothesis", _text(self.hypothesis, "hypothesis", max_len=2000)
        )
        universe = tuple(_text(item, "universe symbol", max_len=32).upper()
                         for item in self.universe)
        if not universe:
            raise TradingError(BAD_INPUT, "a strategy needs a universe")
        object.__setattr__(self, "universe", universe)
        object.__setattr__(
            self, "timeframe", _text(self.timeframe, "timeframe", max_len=20)
        )
        object.__setattr__(self, "entry", _text(self.entry, "entry rule", max_len=80))
        object.__setattr__(self, "exit", _text(self.exit, "exit rule", max_len=80))
        if not 0 <= int(self.fees_bps) <= 10_000:
            raise TradingError(BAD_INPUT, "fees_bps is out of range")
        if not 0 <= int(self.slippage_bps) <= 10_000:
            raise TradingError(BAD_INPUT, "slippage_bps is out of range")
        object.__setattr__(self, "fees_bps", int(self.fees_bps))
        object.__setattr__(self, "slippage_bps", int(self.slippage_bps))
        object.__setattr__(self, "risk", dict(self.risk))
        object.__setattr__(
            self, "version", _text(self.version, "version", max_len=40)
        )
        status = _text(self.status, "status", max_len=20).lower()
        if status not in STRATEGY_STATUSES:
            raise TradingError(
                BAD_INPUT,
                f"strategy status must be one of {', '.join(STRATEGY_STATUSES)}",
            )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "params", dict(self.params))

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "hypothesis": self.hypothesis,
            "universe": list(self.universe),
            "timeframe": self.timeframe,
            "entry": self.entry,
            "exit": self.exit,
            "fees_bps": self.fees_bps,
            "slippage_bps": self.slippage_bps,
            "risk": _jsonable(self.risk),
            "version": self.version,
            "status": self.status,
            "params": _jsonable(self.params),
        }


class StrategyRegistry:
    def __init__(self, specs: Iterable[StrategySpec] = ()) -> None:
        self._items: dict[str, StrategySpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: StrategySpec) -> StrategySpec:
        if not isinstance(spec, StrategySpec):
            raise TradingError(BAD_INPUT, "register expects a StrategySpec")
        for symbol in spec.universe:
            _text(symbol, "universe symbol", max_len=32)
        existing = self._items.get(spec.strategy_id)
        if existing is not None and existing.version == spec.version:
            return existing
        self._items[spec.strategy_id] = spec
        return spec

    def get(self, strategy_id: str) -> StrategySpec:
        clean = _text(strategy_id, "strategy_id", max_len=80)
        spec = self._items.get(clean)
        if spec is None:
            raise TradingError(
                STRATEGY_UNKNOWN, f"no strategy {clean!r} is registered"
            )
        return spec

    def set_status(self, strategy_id: str, status: str) -> StrategySpec:
        spec = self.get(strategy_id)
        clean = _text(status, "status", max_len=20).lower()
        if clean not in STRATEGY_STATUSES:
            raise TradingError(
                BAD_INPUT,
                f"strategy status must be one of {', '.join(STRATEGY_STATUSES)}",
            )
        updated = StrategySpec(
            strategy_id=spec.strategy_id,
            hypothesis=spec.hypothesis,
            universe=spec.universe,
            timeframe=spec.timeframe,
            entry=spec.entry,
            exit=spec.exit,
            fees_bps=spec.fees_bps,
            slippage_bps=spec.slippage_bps,
            risk=spec.risk,
            version=spec.version,
            status=clean,
            params=spec.params,
        )
        self._items[spec.strategy_id] = updated
        return updated

    def list(self) -> tuple[StrategySpec, ...]:
        return tuple(self._items[key] for key in sorted(self._items))

    def __len__(self) -> int:
        return len(self._items)


# ---------------------------------------------------------------------------
# Risk policy and engine
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    policy_version: str
    max_order_notional_minor: int
    max_leverage: Decimal
    max_concentration_pct: int
    min_liquidity_multiple: int
    max_stale_seconds: int
    max_drawdown_pct: Decimal
    max_daily_loss_minor: int
    allowed_symbols: tuple[str, ...] = ()
    allow_short: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "policy_version", _text(self.policy_version, "policy_version")
        )
        object.__setattr__(
            self,
            "max_order_notional_minor",
            _non_negative_int(self.max_order_notional_minor, "max_order_notional"),
        )
        object.__setattr__(
            self, "max_leverage", _positive_decimal(self.max_leverage, "max_leverage")
        )
        if not 0 < int(self.max_concentration_pct) <= 100:
            raise TradingError(
                BAD_INPUT, "max_concentration_pct must be between 1 and 100"
            )
        object.__setattr__(
            self, "max_concentration_pct", int(self.max_concentration_pct)
        )
        if int(self.min_liquidity_multiple) < 1:
            raise TradingError(
                BAD_INPUT, "min_liquidity_multiple must be at least 1"
            )
        object.__setattr__(
            self, "min_liquidity_multiple", int(self.min_liquidity_multiple)
        )
        object.__setattr__(
            self, "max_stale_seconds", _non_negative_int(
                self.max_stale_seconds, "max_stale_seconds"
            )
        )
        drawdown = _decimal(self.max_drawdown_pct, "max_drawdown")
        if not _ZERO <= drawdown <= _HUNDRED:
            raise TradingError(
                BAD_INPUT, "max_drawdown_pct must be between 0 and 100"
            )
        object.__setattr__(self, "max_drawdown_pct", drawdown)
        object.__setattr__(
            self,
            "max_daily_loss_minor",
            _non_negative_int(self.max_daily_loss_minor, "max_daily_loss"),
        )
        object.__setattr__(
            self,
            "allowed_symbols",
            tuple(
                _text(item, "allowed symbol", max_len=32).upper()
                for item in self.allowed_symbols
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "max_order_notional_minor": self.max_order_notional_minor,
            "max_leverage": str(self.max_leverage),
            "max_concentration_pct": self.max_concentration_pct,
            "min_liquidity_multiple": self.min_liquidity_multiple,
            "max_stale_seconds": self.max_stale_seconds,
            "max_drawdown_pct": str(self.max_drawdown_pct),
            "max_daily_loss_minor": self.max_daily_loss_minor,
            "allowed_symbols": list(self.allowed_symbols),
            "allow_short": self.allow_short,
        }


@dataclass(frozen=True, slots=True)
class OwnerRiskPolicySignoff:
    owner_id: str
    policy_version: str
    limits: Mapping[str, Any]
    signed_at: float
    mode: str = "live"
    expires_at: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_id", _text(self.owner_id, "owner_id"))
        object.__setattr__(
            self, "policy_version", _text(self.policy_version, "policy_version")
        )
        object.__setattr__(self, "limits", dict(self.limits))
        object.__setattr__(self, "signed_at", float(self.signed_at))
        object.__setattr__(self, "mode", _text(self.mode, "mode", max_len=20).lower())

    def matches(self, policy: RiskPolicy) -> bool:
        return (
            self.mode == "live"
            and self.policy_version == policy.policy_version
            and _canonical(self.limits) == _canonical(policy.to_dict())
        )


@dataclass(frozen=True, slots=True)
class RiskCheck:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    checks: tuple[RiskCheck, ...]
    reasons: tuple[str, ...]
    policy_version: str
    notional_minor: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "checks": [
                {"name": c.name, "ok": c.ok, "detail": c.detail}
                for c in self.checks
            ],
            "reasons": list(self.reasons),
            "policy_version": self.policy_version,
            "notional_minor": self.notional_minor,
        }


class RiskEngine:
    """Pre-trade and portfolio limits. Only a risk manager edits the policy."""

    def __init__(self, policy: RiskPolicy) -> None:
        if not isinstance(policy, RiskPolicy):
            raise TradingError(BAD_INPUT, "risk policy must be a RiskPolicy")
        self._policy = policy

    @property
    def policy(self) -> RiskPolicy:
        return self._policy

    def update_policy(self, policy: RiskPolicy, *, actor: Actor) -> RiskPolicy:
        if not isinstance(policy, RiskPolicy):
            raise TradingError(BAD_INPUT, "risk policy must be a RiskPolicy")
        if actor.role is Role.EXECUTION:
            raise TradingError(
                RISK_LIMIT_IMMUTABLE,
                "the execution role cannot change risk limits",
            )
        if actor.role not in (Role.RISK_MANAGER, Role.OWNER):
            raise TradingError(
                ROLE_REFUSED,
                "only the risk manager or owner may change risk limits",
            )
        self._policy = policy
        return policy

    def evaluate_order(
        self,
        *,
        request: "OrderRequest",
        snapshot: MarketSnapshot,
        portfolio: "Portfolio",
        now: float,
    ) -> RiskDecision:
        policy = self._policy
        price = snapshot.price(request.symbol, request.side)
        notional_minor = _minor(request.quantity * price)
        marks = snapshot.marks
        position = portfolio.position(request.symbol)
        existing_qty = position.quantity
        delta = request.quantity if request.side == "buy" else -request.quantity
        projected_qty = existing_qty + delta
        existing_value = _minor(abs(existing_qty) * price)
        projected_value = _minor(abs(projected_qty) * price)
        equity = portfolio.equity_minor(marks)
        gross_before = portfolio.gross_exposure_minor(marks)
        projected_gross = gross_before - existing_value + projected_value
        age = now - snapshot.taken_at
        checks: list[RiskCheck] = []

        def add(name: str, ok: bool, detail: str) -> None:
            checks.append(RiskCheck(name=name, ok=bool(ok), detail=detail))

        allowed = not policy.allowed_symbols or request.symbol in set(
            policy.allowed_symbols
        )
        add(
            "symbol_allowlist",
            allowed,
            f"symbol={request.symbol} allowed={list(policy.allowed_symbols)}",
        )
        add(
            "stale_data",
            age <= policy.max_stale_seconds,
            f"age={age:.3f}s limit={policy.max_stale_seconds}s",
        )
        add(
            "max_order_notional",
            notional_minor <= policy.max_order_notional_minor,
            f"notional={notional_minor} limit={policy.max_order_notional_minor}",
        )
        if equity <= 0:
            add("positive_equity", False, f"equity={equity} is not positive")
            leverage_ok = False
            concentration_ok = False
            leverage_detail = "no equity"
            concentration_detail = "no equity"
        else:
            add("positive_equity", True, f"equity={equity}")
            leverage_ok = (
                Decimal(projected_gross) <= Decimal(equity) * policy.max_leverage
            )
            leverage_detail = (
                f"projected_gross={projected_gross}"
                f" equity={equity} max={policy.max_leverage}"
            )
            concentration_ok = (
                Decimal(projected_value * 100)
                <= Decimal(equity) * policy.max_concentration_pct
            )
            concentration_detail = (
                f"position={projected_value} equity={equity}"
                f" max_pct={policy.max_concentration_pct}"
            )
        add("max_leverage", leverage_ok, leverage_detail)
        add("concentration", concentration_ok, concentration_detail)
        liquidity_ok = snapshot.liquidity(
            request.symbol
        ) >= notional_minor * policy.min_liquidity_multiple
        add(
            "liquidity",
            liquidity_ok,
            f"book={snapshot.liquidity(request.symbol)}"
            f" required={notional_minor * policy.min_liquidity_multiple}",
        )
        drawdown = portfolio.drawdown_pct(marks)
        add(
            "drawdown",
            drawdown <= policy.max_drawdown_pct,
            f"drawdown_pct={drawdown} limit={policy.max_drawdown_pct}",
        )
        daily_loss = portfolio.daily_loss_minor(now, marks)
        add(
            "daily_loss",
            daily_loss <= policy.max_daily_loss_minor,
            f"daily_loss={daily_loss} limit={policy.max_daily_loss_minor}",
        )
        if request.side == "sell" and not policy.allow_short:
            short_ok = request.quantity <= existing_qty
            add(
                "no_short",
                short_ok,
                f"sell={request.quantity} held={existing_qty}",
            )
        reasons = tuple(c.name for c in checks if not c.ok)
        return RiskDecision(
            allowed=not reasons,
            checks=tuple(checks),
            reasons=reasons,
            policy_version=policy.policy_version,
            notional_minor=notional_minor,
        )


# ---------------------------------------------------------------------------
# Orders and OMS
# ---------------------------------------------------------------------------

ORDER_STATUSES = (
    "new",
    "acknowledged",
    "partially_filled",
    "filled",
    "cancelled",
    "rejected",
    "unknown",
)
_OPEN_STATUSES = frozenset({"new", "acknowledged", "partially_filled"})
_TRANSITIONS: dict[str, frozenset[str]] = {
    "new": frozenset({"acknowledged", "rejected", "cancelled"}),
    "acknowledged": frozenset(
        {"partially_filled", "filled", "cancelled", "rejected", "unknown"}
    ),
    "partially_filled": frozenset(
        {"partially_filled", "filled", "cancelled", "unknown"}
    ),
    "filled": frozenset(),
    "cancelled": frozenset(),
    "rejected": frozenset(),
    "unknown": frozenset(
        {"filled", "partially_filled", "cancelled", "rejected", "unknown"}
    ),
}


@dataclass(frozen=True, slots=True)
class OrderRequest:
    client_order_id: str
    symbol: str
    side: str
    quantity: Decimal
    strategy_id: str
    order_type: str = "market"
    limit_price: Decimal | None = None
    max_slippage_bps: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "client_order_id",
            _text(self.client_order_id, "client_order_id", max_len=120),
        )
        object.__setattr__(
            self, "symbol", _text(self.symbol, "symbol", max_len=32).upper()
        )
        side = _text(self.side, "side", max_len=8).lower()
        if side not in ("buy", "sell"):
            raise TradingError(BAD_INPUT, "side must be buy or sell")
        object.__setattr__(self, "side", side)
        object.__setattr__(
            self, "quantity", _positive_decimal(self.quantity, "quantity")
        )
        object.__setattr__(
            self, "strategy_id", _text(self.strategy_id, "strategy_id", max_len=80)
        )
        order_type = _text(self.order_type, "order_type", max_len=20).lower()
        if order_type not in ("market", "limit"):
            raise TradingError(BAD_INPUT, "order_type must be market or limit")
        object.__setattr__(self, "order_type", order_type)
        if self.limit_price is not None:
            object.__setattr__(
                self, "limit_price", _positive_decimal(self.limit_price, "limit_price")
            )
        if order_type == "limit" and self.limit_price is None:
            raise TradingError(BAD_INPUT, "a limit order needs a limit_price")
        if self.max_slippage_bps is not None:
            object.__setattr__(
                self,
                "max_slippage_bps",
                _non_negative_int(self.max_slippage_bps, "max_slippage_bps"),
            )

    def fingerprint(self) -> str:
        return _digest(
            {
                "client_order_id": self.client_order_id,
                "symbol": self.symbol,
                "side": self.side,
                "quantity": str(self.quantity),
                "strategy_id": self.strategy_id,
                "order_type": self.order_type,
                "limit_price": (
                    None if self.limit_price is None else str(self.limit_price)
                ),
                "max_slippage_bps": self.max_slippage_bps,
            }
        )


@dataclass(slots=True)
class Order:
    order_id: str
    client_order_id: str
    symbol: str
    side: str
    quantity: Decimal
    strategy_id: str
    order_type: str
    limit_price: Decimal | None
    max_slippage_bps: int | None
    status: str
    created_at: float
    updated_at: float
    filled_quantity: Decimal = _ZERO
    avg_fill_price: Decimal = _ZERO
    venue: str = ""
    reject_reason: str = ""
    last_event_seq: int | None = None

    @property
    def remaining(self) -> Decimal:
        return self.quantity - self.filled_quantity

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": str(self.quantity),
            "filled_quantity": str(self.filled_quantity),
            "remaining": str(self.remaining),
            "avg_fill_price": str(self.avg_fill_price),
            "strategy_id": self.strategy_id,
            "order_type": self.order_type,
            "limit_price": (
                None if self.limit_price is None else str(self.limit_price)
            ),
            "max_slippage_bps": self.max_slippage_bps,
            "status": self.status,
            "venue": self.venue,
            "reject_reason": self.reject_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_event_seq": self.last_event_seq,
        }


@dataclass(frozen=True, slots=True)
class Fill:
    fill_id: str
    order_id: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    notional_minor: int
    fee_minor: int
    at: float
    event_seq: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "fill_id": self.fill_id,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": str(self.quantity),
            "price": str(self.price),
            "notional_minor": self.notional_minor,
            "fee_minor": self.fee_minor,
            "at": self.at,
            "event_seq": self.event_seq,
        }


class OrderManagementSystem:
    """Order state machine with client-order idempotency."""

    def __init__(self, events: EventLog) -> None:
        self._events = events
        self._orders: dict[str, Order] = {}
        self._client: dict[str, str] = {}
        self._fingerprints: dict[str, str] = {}
        self._fills: dict[str, list[Fill]] = {}

    def get(self, order_id: str) -> Order:
        order = self._orders.get(_text(order_id, "order_id", max_len=120))
        if order is None:
            raise TradingError(ORDER_NOT_FOUND, f"no order {order_id!r}")
        return order

    def find_by_client(self, client_order_id: str) -> Order | None:
        order_id = self._client.get(client_order_id)
        return self._orders.get(order_id) if order_id else None

    def fingerprint_for(self, order_id: str) -> str | None:
        return self._fingerprints.get(order_id)

    def list(self) -> tuple[Order, ...]:
        return tuple(self._orders[key] for key in sorted(self._orders))

    def open_orders(self) -> tuple[Order, ...]:
        return tuple(o for o in self.list() if o.status in _OPEN_STATUSES)

    def fills_for(self, order_id: str) -> tuple[Fill, ...]:
        return tuple(self._fills.get(order_id, ()))

    def all_fills(self) -> tuple[Fill, ...]:
        out: list[Fill] = []
        for order_id in sorted(self._fills):
            out.extend(self._fills[order_id])
        return tuple(out)

    def submit(
        self, request: OrderRequest, *, actor: Actor, at: float
    ) -> tuple[Order, bool]:
        if not isinstance(request, OrderRequest):
            raise TradingError(BAD_INPUT, "submit expects an OrderRequest")
        fingerprint = request.fingerprint()
        existing_id = self._client.get(request.client_order_id)
        if existing_id is not None:
            if self._fingerprints[existing_id] != fingerprint:
                raise TradingError(
                    IDEMPOTENCY_CONFLICT,
                    f"client order id {request.client_order_id!r} was already"
                    " used for a different order",
                )
            return self._orders[existing_id], False
        order = Order(
            order_id=_new_id(
                "ord",
                at=at,
                seed=request.client_order_id,
            ),
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            strategy_id=request.strategy_id,
            order_type=request.order_type,
            limit_price=request.limit_price,
            max_slippage_bps=request.max_slippage_bps,
            status="new",
            created_at=float(at),
            updated_at=float(at),
        )
        event = self._events.append(
            "order.created",
            {"order": order.to_dict(), "request": _jsonable(request)},
            actor=actor.actor_id,
            at=at,
        )
        order.last_event_seq = event.seq
        self._orders[order.order_id] = order
        self._client[order.client_order_id] = order.order_id
        self._fingerprints[order.order_id] = fingerprint
        self._fills[order.order_id] = []
        return order, True

    def acknowledge(self, order_id: str, *, venue: str, actor: Actor, at: float) -> Order:
        order = self.get(order_id)
        order.venue = _text(venue, "venue", max_len=80)
        return self._transition(
            order, "acknowledged", "order.acknowledged", actor, at,
            {"venue": order.venue},
        )

    def record_fill(
        self,
        order_id: str,
        *,
        quantity: Decimal,
        price: Decimal,
        fee_minor: int,
        actor: Actor,
        at: float,
        event_type: str = "fill.recorded",
    ) -> Fill:
        order = self.get(order_id)
        if order.status not in ("acknowledged", "partially_filled"):
            raise TradingError(
                ORDER_STATE,
                f"cannot fill order in state {order.status!r}",
            )
        return self._apply_fill(
            order,
            quantity=_positive_decimal(quantity, "fill quantity"),
            price=_positive_decimal(price, "fill price"),
            fee_minor=_non_negative_int(fee_minor, "fee_minor"),
            actor=actor,
            at=at,
            event_type=event_type,
            from_states=("acknowledged", "partially_filled"),
        )

    def _apply_fill(
        self,
        order: Order,
        *,
        quantity: Decimal,
        price: Decimal,
        fee_minor: int,
        actor: Actor,
        at: float,
        event_type: str,
        from_states: tuple[str, ...],
    ) -> Fill:
        if order.status not in from_states:
            raise TradingError(
                ORDER_STATE, f"cannot fill order in state {order.status!r}"
            )
        if quantity > order.remaining:
            raise TradingError(
                BAD_INPUT,
                f"fill {quantity} exceeds remaining {order.remaining}",
            )
        notional_minor = _minor(quantity * price)
        previous_value = order.avg_fill_price * order.filled_quantity
        new_filled = order.filled_quantity + quantity
        order.avg_fill_price = (previous_value + price * quantity) / new_filled
        order.filled_quantity = new_filled
        status = "filled" if order.remaining <= _ZERO else "partially_filled"
        fill_id = _new_id(
            "fill", at=at, seed=f"{order.order_id}:{order.filled_quantity}"
        )
        event = self._events.append(
            event_type,
            {
                "order_id": order.order_id,
                "fill_id": fill_id,
                "quantity": str(quantity),
                "price": str(price),
                "notional_minor": notional_minor,
                "fee_minor": fee_minor,
                "status": status,
            },
            actor=actor.actor_id,
            at=at,
        )
        order.status = status
        order.updated_at = float(at)
        order.last_event_seq = event.seq
        fill = Fill(
            fill_id=fill_id,
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=quantity,
            price=price,
            notional_minor=notional_minor,
            fee_minor=fee_minor,
            at=float(at),
            event_seq=event.seq,
        )
        self._fills[order.order_id].append(fill)
        return fill

    def reject(
        self, order_id: str, *, reason: str, actor: Actor, at: float
    ) -> Order:
        order = self.get(order_id)
        order.reject_reason = _text(reason, "reason", max_len=500)
        return self._transition(
            order, "rejected", "order.rejected", actor, at,
            {"reason": order.reject_reason},
        )

    def cancel(
        self, order_id: str, *, reason: str, actor: Actor, at: float
    ) -> Order:
        order = self.get(order_id)
        return self._transition(
            order, "cancelled", "order.cancelled", actor, at,
            {"reason": str(reason or "")[:500]},
        )

    def mark_unknown(
        self, order_id: str, *, reason: str, actor: Actor, at: float
    ) -> Order:
        order = self.get(order_id)
        return self._transition(
            order, "unknown", "order.unknown", actor, at,
            {"reason": str(reason or "")[:500]},
        )

    def resolve_unknown(
        self,
        order_id: str,
        *,
        status: str,
        fills: Iterable[tuple[Decimal, Decimal, int]],
        reason: str,
        actor: Actor,
        at: float,
    ) -> tuple[Order, tuple[Fill, ...]]:
        order = self.get(order_id)
        if order.status != "unknown":
            raise TradingError(
                ORDER_STATE,
                f"reconcile expects an unknown order, got {order.status!r}",
            )
        clean = _text(status, "status", max_len=20).lower()
        if clean not in ("filled", "partially_filled", "rejected", "cancelled"):
            raise TradingError(
                BAD_INPUT,
                "reconciled status must be filled, partially_filled,"
                " rejected or cancelled",
            )
        applied: list[Fill] = []
        if clean in ("filled", "partially_filled"):
            for quantity, price, fee_minor in fills:
                applied.append(
                    self._apply_fill(
                        order,
                        quantity=_positive_decimal(quantity, "fill quantity"),
                        price=_positive_decimal(price, "fill price"),
                        fee_minor=_non_negative_int(fee_minor, "fee_minor"),
                        actor=actor,
                        at=at,
                        event_type="fill.reconciled",
                        from_states=("unknown", "partially_filled"),
                    )
                )
            if not applied:
                raise TradingError(
                    BAD_INPUT, "a reconciled fill outcome needs at least one fill"
                )
        else:
            self._transition(
                order, clean, "order.reconciled", actor, at,
                {"reason": str(reason or "")[:500]},
            )
        event = self._events.append(
            "order.reconciled",
            {
                "order_id": order.order_id,
                "status": order.status,
                "reason": str(reason or "")[:500],
                "fills": len(applied),
            },
            actor=actor.actor_id,
            at=at,
        )
        order.last_event_seq = event.seq
        return order, tuple(applied)

    def _transition(
        self,
        order: Order,
        new_status: str,
        event_type: str,
        actor: Actor,
        at: float,
        payload: Mapping[str, Any],
    ) -> Order:
        if new_status not in _TRANSITIONS.get(order.status, frozenset()):
            raise TradingError(
                ORDER_STATE,
                f"cannot move order from {order.status!r} to {new_status!r}",
            )
        event = self._events.append(
            event_type,
            {"order_id": order.order_id, "status": new_status, **_jsonable(payload)},
            actor=actor.actor_id,
            at=at,
        )
        order.status = new_status
        order.updated_at = float(at)
        order.last_event_seq = event.seq
        return order


# ---------------------------------------------------------------------------
# Portfolio (double-entry)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Position:
    symbol: str
    quantity: Decimal = _ZERO
    cost_basis_minor: int = 0
    realized_pnl_minor: int = 0
    fees_minor: int = 0

    def market_value_minor(self, price: Decimal) -> int:
        return _minor(self.quantity * price)


class Portfolio:
    """Double-entry portfolio. Cash and positions derive from the ledger."""

    def __init__(
        self,
        ledger: DoubleEntryLedger,
        *,
        currency: str = "USD",
    ) -> None:
        code = _text(currency, "currency", max_len=8).upper()
        if len(code) != 3 or not code.isalpha():
            raise TradingError(BAD_INPUT, "currency must be a 3-letter code")
        self._ledger = ledger
        self._currency = code
        self._positions: dict[str, Position] = {}
        self._realized_pnl = 0
        self._funding_minor = 0
        self._fees_minor = 0
        self._peak_equity: int | None = None
        self._day_start: dict[str, int] = {}
        self._journals: list[str] = []

    # -- basics --------------------------------------------------------
    @property
    def currency(self) -> str:
        return self._currency

    @property
    def cash_minor(self) -> int:
        return self._ledger.balance("cash")

    @property
    def realized_pnl_minor(self) -> int:
        return self._realized_pnl

    @property
    def fees_minor(self) -> int:
        return self._fees_minor

    @property
    def funding_minor(self) -> int:
        return self._funding_minor

    def position(self, symbol: str) -> Position:
        clean = _text(symbol, "symbol", max_len=32).upper()
        return self._positions.get(clean, Position(symbol=clean))

    def positions(self) -> dict[str, Position]:
        return {k: v for k, v in self._positions.items() if v.quantity != 0}

    def fund(
        self, amount_minor: int, *, at: float, event_seq: int | None = None
    ) -> JournalEntry:
        amount = _non_negative_int(amount_minor, "funding amount")
        if amount == 0:
            raise TradingError(BAD_INPUT, "opening capital must be positive")
        journal = self._ledger.post(
            kind="capital",
            memo="opening capital",
            postings=(
                Posting("cash", "debit", amount),
                Posting("capital", "credit", amount),
            ),
            at=at,
            event_seq=event_seq,
            refs={"kind": "capital"},
        )
        self._journals.append(journal.journal_id)
        return journal

    def apply_fill(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
        fee_minor: int,
        order_id: str,
        fill_id: str,
        at: float,
        event_seq: int | None = None,
    ) -> JournalEntry:
        clean_symbol = _text(symbol, "symbol", max_len=32).upper()
        clean_side = _text(side, "side", max_len=8).lower()
        qty = _positive_decimal(quantity, "quantity")
        px = _positive_decimal(price, "price")
        fee = _non_negative_int(fee_minor, "fee_minor")
        notional = _minor(qty * px)
        position = self._positions.setdefault(
            clean_symbol, Position(symbol=clean_symbol)
        )
        refs = {"order_id": order_id, "fill_id": fill_id, "symbol": clean_symbol}
        if clean_side == "buy":
            journal = self._ledger.post(
                kind="fill",
                memo=f"buy {qty} {clean_symbol} @ {px}",
                postings=(
                    Posting(f"position:{clean_symbol}", "debit", notional),
                    Posting("fees", "debit", fee),
                    Posting("cash", "credit", notional + fee),
                ),
                at=at,
                event_seq=event_seq,
                refs=refs,
            )
            position.quantity += qty
            position.cost_basis_minor += notional
            position.fees_minor += fee
            self._fees_minor += fee
        elif clean_side == "sell":
            if qty > position.quantity:
                raise TradingError(
                    BAD_INPUT,
                    f"cannot sell {qty} {clean_symbol}; holding"
                    f" {position.quantity}",
                )
            if qty == position.quantity:
                cost_released = position.cost_basis_minor
            else:
                cost_released = int(
                    (
                        Decimal(position.cost_basis_minor)
                        * qty
                        / position.quantity
                    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                )
            realized = notional - cost_released
            postings = [
                Posting("cash", "debit", notional - fee),
                Posting("fees", "debit", fee),
                Posting(f"position:{clean_symbol}", "credit", cost_released),
            ]
            if realized >= 0:
                postings.append(Posting("realized_pnl", "credit", realized))
            else:
                postings.append(Posting("realized_pnl", "debit", -realized))
            journal = self._ledger.post(
                kind="fill",
                memo=f"sell {qty} {clean_symbol} @ {px}",
                postings=tuple(postings),
                at=at,
                event_seq=event_seq,
                refs=refs,
            )
            position.quantity -= qty
            position.cost_basis_minor -= cost_released
            position.realized_pnl_minor += realized
            position.fees_minor += fee
            self._realized_pnl += realized
            self._fees_minor += fee
            if position.quantity == 0:
                position.cost_basis_minor = 0
        else:
            raise TradingError(BAD_INPUT, "side must be buy or sell")
        if position.quantity == 0:
            position.quantity = _ZERO
        self._journals.append(journal.journal_id)
        return journal

    def apply_funding(
        self,
        *,
        symbol: str,
        amount_minor: int,
        at: float,
        event_seq: int | None = None,
        memo: str = "",
    ) -> JournalEntry:
        """Positive ``amount_minor`` is paid by us; negative is received."""
        if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
            raise TradingError(BAD_INPUT, "funding amount must be an integer")
        if amount_minor == 0:
            raise TradingError(BAD_INPUT, "funding amount must be non-zero")
        clean_symbol = _text(symbol, "symbol", max_len=32).upper()
        if amount_minor > 0:
            postings = (
                Posting("funding", "debit", amount_minor),
                Posting("cash", "credit", amount_minor),
            )
        else:
            postings = (
                Posting("cash", "debit", -amount_minor),
                Posting("funding", "credit", -amount_minor),
            )
        journal = self._ledger.post(
            kind="funding",
            memo=memo or f"funding {clean_symbol}",
            postings=postings,
            at=at,
            event_seq=event_seq,
            refs={"symbol": clean_symbol},
        )
        self._funding_minor += amount_minor
        self._journals.append(journal.journal_id)
        return journal

    # -- valuation -----------------------------------------------------
    def gross_exposure_minor(self, marks: Mapping[str, Decimal]) -> int:
        total = 0
        for symbol, position in self._positions.items():
            if position.quantity == 0:
                continue
            price = marks.get(symbol)
            if price is None:
                raise TradingError(
                    STALE_DATA, f"no mark for open position {symbol!r}"
                )
            total += _minor(abs(position.quantity) * _positive_decimal(price, "mark"))
        return total

    def market_value_minor(self, marks: Mapping[str, Decimal]) -> int:
        total = 0
        for symbol, position in self._positions.items():
            if position.quantity == 0:
                continue
            price = marks.get(symbol)
            if price is None:
                raise TradingError(
                    STALE_DATA, f"no mark for open position {symbol!r}"
                )
            total += _minor(position.quantity * _positive_decimal(price, "mark"))
        return total

    def equity_minor(self, marks: Mapping[str, Decimal]) -> int:
        return self.cash_minor + self.market_value_minor(marks)

    def unrealized_pnl_minor(self, marks: Mapping[str, Decimal]) -> int:
        return self.market_value_minor(marks) - sum(
            p.cost_basis_minor for p in self._positions.values()
        )

    def observe_equity(self, at: float, marks: Mapping[str, Decimal]) -> int:
        equity = self.equity_minor(marks)
        if self._peak_equity is None or equity > self._peak_equity:
            self._peak_equity = equity
        day = _day(at)
        self._day_start.setdefault(day, equity)
        return equity

    def drawdown_pct(self, marks: Mapping[str, Decimal]) -> Decimal:
        equity = self.equity_minor(marks)
        peak = self._peak_equity
        if peak is None or peak <= 0:
            self._peak_equity = equity
            return _ZERO
        if equity > peak:
            self._peak_equity = equity
            peak = equity
        if peak <= 0:
            return _ZERO
        return (Decimal(peak - equity) * _HUNDRED / Decimal(peak)).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )

    def daily_loss_minor(self, at: float, marks: Mapping[str, Decimal]) -> int:
        equity = self.equity_minor(marks)
        start = self._day_start.setdefault(_day(at), equity)
        return max(0, start - equity)

    def snapshot(self, marks: Mapping[str, Decimal]) -> dict[str, Any]:
        return {
            "currency": self._currency,
            "cash_minor": self.cash_minor,
            "equity_minor": self.equity_minor(marks),
            "gross_exposure_minor": self.gross_exposure_minor(marks),
            "realized_pnl_minor": self._realized_pnl,
            "unrealized_pnl_minor": self.unrealized_pnl_minor(marks),
            "fees_minor": self._fees_minor,
            "funding_minor": self._funding_minor,
            "positions": {
                symbol: {
                    "quantity": str(position.quantity),
                    "cost_basis_minor": position.cost_basis_minor,
                    "realized_pnl_minor": position.realized_pnl_minor,
                    "fees_minor": position.fees_minor,
                }
                for symbol, position in sorted(self._positions.items())
            },
        }


# ---------------------------------------------------------------------------
# Paper broker
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PaperBrokerConfig:
    venue: str = "paper"
    latency_ms: int = 0
    fee_bps: int = 0
    slippage_bps: int = 0
    fill_ratio: Decimal = Decimal("1")
    reject_all: bool = False
    reject_reason: str = ""
    unknown_once: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "venue", _text(self.venue, "venue", max_len=80))
        for name in ("latency_ms", "fee_bps", "slippage_bps"):
            object.__setattr__(
                self, name, _non_negative_int(getattr(self, name), name)
            )
        ratio = _decimal(self.fill_ratio, "fill_ratio")
        if not _ZERO < ratio <= Decimal("1"):
            raise TradingError(BAD_INPUT, "fill_ratio must be in (0, 1]")
        object.__setattr__(self, "fill_ratio", ratio)


@dataclass(frozen=True, slots=True)
class BrokerFill:
    quantity: Decimal
    price: Decimal


@dataclass(frozen=True, slots=True)
class BrokerResult:
    status: str
    fills: tuple[BrokerFill, ...]
    reason: str
    received_at: float
    venue: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "fills": [
                {"quantity": str(f.quantity), "price": str(f.price)}
                for f in self.fills
            ],
            "reason": self.reason,
            "received_at": self.received_at,
            "venue": self.venue,
        }


class PaperBroker:
    """Simulated execution: latency, fees, slippage, partial fills, unknown."""

    def __init__(self, config: PaperBrokerConfig | None = None) -> None:
        self._config = config or PaperBrokerConfig()
        self._unknown_pending = self._config.unknown_once

    @property
    def config(self) -> PaperBrokerConfig:
        return self._config

    @property
    def venue(self) -> str:
        return self._config.venue

    def execute(
        self, order: Order, snapshot: MarketSnapshot, *, now: float
    ) -> BrokerResult:
        config = self._config
        received_at = float(now) + (config.latency_ms / 1000.0)
        if config.reject_all:
            return BrokerResult(
                "rejected",
                (),
                config.reject_reason or "broker rejected the order",
                received_at,
                config.venue,
            )
        if self._unknown_pending:
            self._unknown_pending = False
            return BrokerResult(
                "unknown",
                (),
                "venue timeout; outcome must be reconciled",
                received_at,
                config.venue,
            )
        if (
            order.max_slippage_bps is not None
            and config.slippage_bps > order.max_slippage_bps
        ):
            return BrokerResult(
                "rejected",
                (),
                f"simulated slippage {config.slippage_bps}bps exceeds the"
                f" order cap {order.max_slippage_bps}bps",
                received_at,
                config.venue,
            )
        try:
            reference = snapshot.price(order.symbol, order.side)
        except TradingError as exc:
            return BrokerResult("rejected", (), exc.detail, received_at, config.venue)
        slip = Decimal(config.slippage_bps) / Decimal("10000")
        price = reference * (Decimal("1") + slip if order.side == "buy"
                             else Decimal("1") - slip)
        if order.limit_price is not None:
            if order.side == "buy" and price > order.limit_price:
                return BrokerResult(
                    "rejected",
                    (),
                    "limit price not marketable at the simulated book",
                    received_at,
                    config.venue,
                )
            if order.side == "sell" and price < order.limit_price:
                return BrokerResult(
                    "rejected",
                    (),
                    "limit price not marketable at the simulated book",
                    received_at,
                    config.venue,
                )
        if price <= _ZERO:
            return BrokerResult(
                "rejected", (), "slippage produced an invalid price",
                received_at, config.venue,
            )
        desired = order.remaining * config.fill_ratio
        book_minor = snapshot.liquidity(order.symbol)
        unit_minor = _minor(price)
        if unit_minor <= 0 or book_minor <= 0:
            return BrokerResult(
                "rejected", (), "no simulated liquidity",
                received_at, config.venue,
            )
        max_by_book = Decimal(book_minor) / Decimal(unit_minor)
        quantity = min(desired, max_by_book)
        if quantity <= _ZERO:
            return BrokerResult(
                "rejected", (), "simulated book cannot fill any quantity",
                received_at, config.venue,
            )
        quantity = min(quantity, order.remaining).quantize(
            Decimal("0.00000001"), rounding=ROUND_HALF_UP
        )
        quantity = min(quantity, order.remaining)
        if quantity <= _ZERO:
            return BrokerResult(
                "rejected", (), "simulated book cannot fill any quantity",
                received_at, config.venue,
            )
        status = "filled" if quantity == order.remaining else "partially_filled"
        return BrokerResult(
            status,
            (BrokerFill(quantity=quantity, price=price),),
            "",
            received_at,
            config.venue,
        )

    def fee_minor(self, notional_minor: int) -> int:
        return int(
            (
                Decimal(notional_minor) * Decimal(self._config.fee_bps)
                / Decimal("10000")
            ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MarketBar:
    symbol: str
    ts: float
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = _ZERO
    regime: str = "unspecified"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "symbol", _text(self.symbol, "symbol", max_len=32).upper()
        )
        object.__setattr__(self, "ts", float(self.ts))
        for name in ("open", "high", "low", "close"):
            object.__setattr__(
                self, name, _positive_decimal(getattr(self, name), name)
            )
        object.__setattr__(
            self, "volume", _decimal(self.volume, "volume")
        )
        object.__setattr__(
            self, "regime", _text(self.regime, "regime", max_len=40)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "ts": self.ts,
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": str(self.volume),
            "regime": self.regime,
        }


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    notional_minor: int
    fee_minor: int
    signal_index: int
    signal_ts: float
    execute_index: int
    execute_ts: float
    regime: str
    pnl_minor: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": str(self.quantity),
            "price": str(self.price),
            "notional_minor": self.notional_minor,
            "fee_minor": self.fee_minor,
            "signal_index": self.signal_index,
            "signal_ts": self.signal_ts,
            "execute_index": self.execute_index,
            "execute_ts": self.execute_ts,
            "regime": self.regime,
            "pnl_minor": self.pnl_minor,
        }


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    trades: int
    wins: int
    win_rate_pct: Decimal
    gross_pnl_minor: int
    fees_minor: int
    net_pnl_minor: int
    return_pct: Decimal
    max_drawdown_pct: Decimal
    avg_trade_pnl_minor: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "trades": self.trades,
            "wins": self.wins,
            "win_rate_pct": str(self.win_rate_pct),
            "gross_pnl_minor": self.gross_pnl_minor,
            "fees_minor": self.fees_minor,
            "net_pnl_minor": self.net_pnl_minor,
            "return_pct": str(self.return_pct),
            "max_drawdown_pct": str(self.max_drawdown_pct),
            "avg_trade_pnl_minor": self.avg_trade_pnl_minor,
        }


@dataclass(frozen=True, slots=True)
class BacktestSegment:
    name: str
    start_index: int
    end_index: int
    metrics: BacktestMetrics

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "start_index": self.start_index,
            "end_index": self.end_index,
            "metrics": self.metrics.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class BacktestResult:
    strategy_id: str
    version: str
    starting_cash_minor: int
    ending_equity_minor: int
    trades: tuple[BacktestTrade, ...]
    equity_curve: tuple[dict[str, Any], ...]
    metrics: BacktestMetrics
    segments: tuple[BacktestSegment, ...]
    regimes: Mapping[str, BacktestMetrics]
    split_index: int
    digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "version": self.version,
            "starting_cash_minor": self.starting_cash_minor,
            "ending_equity_minor": self.ending_equity_minor,
            "trades": [t.to_dict() for t in self.trades],
            "equity_curve": list(self.equity_curve),
            "metrics": self.metrics.to_dict(),
            "segments": [s.to_dict() for s in self.segments],
            "regimes": {
                key: value.to_dict() for key, value in sorted(self.regimes.items())
            },
            "split_index": self.split_index,
            "digest": self.digest,
        }


def _sma_at(closes: Sequence[Decimal], index: int, window: int) -> Decimal | None:
    if window <= 0 or index - window + 1 < 0:
        return None
    chunk = closes[index - window + 1 : index + 1]
    return sum(chunk) / Decimal(window)


def _rule_signal(
    rule: str, closes: Sequence[Decimal], index: int, params: Mapping[str, Any]
) -> bool:
    name = rule.lower()
    short_window = int(params.get("short_window", 3))
    long_window = int(params.get("long_window", 5))
    if name in ("never", "none"):
        return False
    if name in ("always", "always_true"):
        return True
    if name in ("sma_cross_up", "sma_cross_buy"):
        if index < long_window:
            return False
        short = _sma_at(closes, index, short_window)
        long = _sma_at(closes, index, long_window)
        prev_short = _sma_at(closes, index - 1, short_window)
        prev_long = _sma_at(closes, index - 1, long_window)
        if None in (short, long, prev_short, prev_long):
            return False
        return short > long and prev_short <= prev_long
    if name in ("sma_cross_down", "sma_cross_sell"):
        if index < long_window:
            return False
        short = _sma_at(closes, index, short_window)
        long = _sma_at(closes, index, long_window)
        prev_short = _sma_at(closes, index - 1, short_window)
        prev_long = _sma_at(closes, index - 1, long_window)
        if None in (short, long, prev_short, prev_long):
            return False
        return short < long and prev_short >= prev_long
    if name in ("close_above_sma", "close_above"):
        value = _sma_at(closes, index, long_window)
        return value is not None and closes[index] > value
    if name in ("close_below_sma", "close_below"):
        value = _sma_at(closes, index, long_window)
        return value is not None and closes[index] < value
    raise TradingError(BAD_INPUT, f"unknown backtest rule {rule!r}")


def _max_drawdown_pct(equity: Sequence[int]) -> Decimal:
    peak: int | None = None
    worst = _ZERO
    for value in equity:
        if peak is None or value > peak:
            peak = value
        if peak and peak > 0:
            drawdown = (Decimal(peak - value) * _HUNDRED / Decimal(peak)).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
            if drawdown > worst:
                worst = drawdown
    return worst


def _metrics(
    trades: Sequence[BacktestTrade],
    equity: Sequence[int],
    starting_cash_minor: int,
) -> BacktestMetrics:
    closed = [t for t in trades if t.side == "sell"]
    wins = sum(1 for t in closed if t.pnl_minor > 0)
    net = sum(t.pnl_minor for t in closed)
    fees = sum(t.fee_minor for t in trades)
    gross = net + fees
    ending = equity[-1] if equity else starting_cash_minor
    return_pct = (
        (Decimal(ending - starting_cash_minor) * _HUNDRED
         / Decimal(starting_cash_minor)).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        if starting_cash_minor
        else _ZERO
    )
    win_rate = (
        (Decimal(wins) * _HUNDRED / Decimal(len(closed))).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        if closed
        else _ZERO
    )
    avg = int(Decimal(net) / Decimal(len(closed))) if closed else 0
    return BacktestMetrics(
        trades=len(closed),
        wins=wins,
        win_rate_pct=win_rate,
        gross_pnl_minor=gross,
        fees_minor=fees,
        net_pnl_minor=net,
        return_pct=return_pct,
        max_drawdown_pct=_max_drawdown_pct(equity),
        avg_trade_pnl_minor=avg,
    )


class BacktestEngine:
    """Deterministic, no-look-ahead backtester with fees and slippage."""

    def run(
        self,
        bars: Sequence[MarketBar],
        spec: StrategySpec,
        *,
        starting_cash_minor: int = 10_000_000,
        quantity: Decimal = Decimal("1"),
        split_fraction: Decimal = Decimal("0.7"),
    ) -> BacktestResult:
        if not isinstance(spec, StrategySpec):
            raise TradingError(BAD_INPUT, "backtest expects a StrategySpec")
        cash = _non_negative_int(starting_cash_minor, "starting_cash_minor")
        if cash == 0:
            raise TradingError(BAD_INPUT, "starting cash must be positive")
        qty = _positive_decimal(quantity, "quantity")
        split = _decimal(split_fraction, "split_fraction")
        if not _ZERO < split < Decimal("1"):
            raise TradingError(BAD_INPUT, "split_fraction must be in (0, 1)")
        data = list(bars)
        if len(data) < 5:
            raise TradingError(BAD_INPUT, "a backtest needs at least 5 bars")
        symbols = {bar.symbol for bar in data}
        if len(symbols) != 1:
            raise TradingError(
                BAD_INPUT, "one backtest series must have exactly one symbol"
            )
        if data[0].symbol not in spec.universe:
            raise TradingError(
                BAD_INPUT,
                f"strategy universe {spec.universe} does not include"
                f" {data[0].symbol}",
            )
        for previous, current in zip(data, data[1:]):
            if current.ts <= previous.ts:
                raise TradingError(
                    BAD_INPUT, "bars must be strictly ascending in time"
                )
        closes = [bar.close for bar in data]
        fee_bps = Decimal(spec.fees_bps)
        slip = Decimal(spec.slippage_bps) / Decimal("10000")
        cash_running = cash
        held = _ZERO
        entry_notional = 0
        entry_fee = 0
        trades: list[BacktestTrade] = []
        curve: list[dict[str, Any]] = []
        equity_values: list[int] = []
        last_index = len(data) - 2

        def fee_for(notional: int) -> int:
            return int(
                (Decimal(notional) * fee_bps / Decimal("10000")).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )

        pending: str | None = None
        pending_signal: int | None = None
        for index, bar in enumerate(data):
            # A signal decided on the previous bar executes at THIS bar's open,
            # so nothing after the decision bar is visible to the decision.
            if pending == "buy" and held == 0:
                price = bar.open * (Decimal("1") + slip)
                notional = _minor(qty * price)
                fee = fee_for(notional)
                if notional + fee <= cash_running:
                    assert pending_signal is not None
                    cash_running -= notional + fee
                    held = qty
                    entry_notional = notional
                    entry_fee = fee
                    trades.append(
                        BacktestTrade(
                            symbol=bar.symbol,
                            side="buy",
                            quantity=qty,
                            price=price,
                            notional_minor=notional,
                            fee_minor=fee,
                            signal_index=pending_signal,
                            signal_ts=data[pending_signal].ts,
                            execute_index=index,
                            execute_ts=bar.ts,
                            regime=data[pending_signal].regime,
                        )
                    )
            elif pending == "sell" and held > 0:
                price = bar.open * (Decimal("1") - slip)
                notional = _minor(held * price)
                fee = fee_for(notional)
                pnl = notional - entry_notional - entry_fee - fee
                assert pending_signal is not None
                cash_running += notional - fee
                trades.append(
                    BacktestTrade(
                        symbol=bar.symbol,
                        side="sell",
                        quantity=held,
                        price=price,
                        notional_minor=notional,
                        fee_minor=fee,
                        signal_index=pending_signal,
                        signal_ts=data[pending_signal].ts,
                        execute_index=index,
                        execute_ts=bar.ts,
                        regime=data[pending_signal].regime,
                        pnl_minor=pnl,
                    )
                )
                held = _ZERO
                entry_notional = 0
                entry_fee = 0
            pending = None
            pending_signal = None
            mark = bar.close
            equity = cash_running + _minor(held * mark)
            equity_values.append(equity)
            curve.append(
                {"index": index, "ts": bar.ts, "equity_minor": equity}
            )
            if index < last_index:
                if held == 0 and _rule_signal(
                    spec.entry, closes, index, spec.params
                ):
                    pending = "buy"
                    pending_signal = index
                elif held > 0 and _rule_signal(
                    spec.exit, closes, index, spec.params
                ):
                    pending = "sell"
                    pending_signal = index
        ending = equity_values[-1]
        metrics = _metrics(trades, equity_values, cash)
        split_index = max(1, min(len(data) - 1, int(len(data) * split)))
        in_equity = equity_values[: split_index + 1]
        out_equity = equity_values[split_index:]
        in_trades = [t for t in trades if t.execute_index < split_index]
        out_trades = [t for t in trades if t.execute_index >= split_index]
        segments = (
            BacktestSegment(
                name="in_sample",
                start_index=0,
                end_index=split_index,
                metrics=_metrics(in_trades, in_equity, cash),
            ),
            BacktestSegment(
                name="out_of_sample",
                start_index=split_index,
                end_index=len(data) - 1,
                metrics=_metrics(
                    out_trades,
                    out_equity,
                    in_equity[-1] if in_equity else cash,
                ),
            ),
        )
        regimes: dict[str, BacktestMetrics] = {}
        for regime in sorted({bar.regime for bar in data}):
            regime_trades = [t for t in trades if t.regime == regime]
            running = cash
            regime_equity = [running]
            for trade in regime_trades:
                running += trade.pnl_minor
                regime_equity.append(running)
            regimes[regime] = _metrics(regime_trades, regime_equity, cash)
        result = BacktestResult(
            strategy_id=spec.strategy_id,
            version=spec.version,
            starting_cash_minor=cash,
            ending_equity_minor=ending,
            trades=tuple(trades),
            equity_curve=tuple(curve),
            metrics=metrics,
            segments=segments,
            regimes=regimes,
            split_index=split_index,
            digest="",
        )
        body = {
            "strategy_id": result.strategy_id,
            "version": result.version,
            "starting_cash_minor": result.starting_cash_minor,
            "ending_equity_minor": result.ending_equity_minor,
            "trades": [t.to_dict() for t in result.trades],
            "equity_curve": list(result.equity_curve),
            "metrics": result.metrics.to_dict(),
            "segments": [s.to_dict() for s in result.segments],
            "regimes": {
                k: v.to_dict() for k, v in sorted(result.regimes.items())
            },
            "split_index": result.split_index,
        }
        return BacktestResult(
            strategy_id=result.strategy_id,
            version=result.version,
            starting_cash_minor=result.starting_cash_minor,
            ending_equity_minor=result.ending_equity_minor,
            trades=result.trades,
            equity_curve=result.equity_curve,
            metrics=result.metrics,
            segments=result.segments,
            regimes=result.regimes,
            split_index=result.split_index,
            digest=_digest(body),
        )


# ---------------------------------------------------------------------------
# Kill switch and audit
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KillSwitchReport:
    engaged: bool
    already_engaged: bool
    reason: str
    actor: str
    at: float
    cancelled_orders: int
    revoked_leases: int
    revoked_keys: int
    frozen_strategies: int
    callback_errors: tuple[str, ...]
    event_seq: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "engaged": self.engaged,
            "already_engaged": self.already_engaged,
            "reason": self.reason,
            "actor": self.actor,
            "at": self.at,
            "cancelled_orders": self.cancelled_orders,
            "revoked_leases": self.revoked_leases,
            "revoked_keys": self.revoked_keys,
            "frozen_strategies": self.frozen_strategies,
            "callback_errors": list(self.callback_errors),
            "event_seq": self.event_seq,
        }


class KillSwitch:
    """Cancel-all and injected lease/key revocation; not a UI-only button."""

    def __init__(
        self,
        *,
        revoke_leases: Callable[[], int] | None = None,
        revoke_keys: Callable[[], int] | None = None,
        freeze_strategies: Callable[[], int] | None = None,
    ) -> None:
        self._revoke_leases = revoke_leases
        self._revoke_keys = revoke_keys
        self._freeze_strategies = freeze_strategies
        self._report: KillSwitchReport | None = None

    @property
    def engaged(self) -> bool:
        return self._report is not None

    def engage(
        self, system: "TradingSystem", *, actor: Actor, reason: str
    ) -> KillSwitchReport:
        if actor.role not in (Role.RISK_MANAGER, Role.AUDITOR, Role.OWNER):
            raise TradingError(
                ROLE_REFUSED,
                "only risk, audit or owner may engage the kill switch",
            )
        if self._report is not None:
            previous = self._report
            return KillSwitchReport(
                engaged=True,
                already_engaged=True,
                reason=previous.reason,
                actor=previous.actor,
                at=previous.at,
                cancelled_orders=previous.cancelled_orders,
                revoked_leases=previous.revoked_leases,
                revoked_keys=previous.revoked_keys,
                frozen_strategies=previous.frozen_strategies,
                callback_errors=previous.callback_errors,
                event_seq=previous.event_seq,
            )
        clean_reason = _text(reason, "reason", max_len=500)
        at = system.now()
        cancelled = system.cancel_all(actor, reason=f"kill_switch:{clean_reason}")
        errors: list[str] = []

        def call(callback: Callable[[], int] | None, name: str) -> int:
            if callback is None:
                return 0
            try:
                return int(callback())
            except Exception as exc:  # noqa: BLE001 - injected callback boundary
                errors.append(f"{name}: {exc}")
                return 0

        leases = call(self._revoke_leases, "revoke_leases")
        keys = call(self._revoke_keys, "revoke_keys")
        frozen = call(self._freeze_strategies, "freeze_strategies")
        system.freeze()
        event = system.events.append(
            "kill_switch.engaged",
            {
                "reason": clean_reason,
                "cancelled_orders": cancelled,
                "revoked_leases": leases,
                "revoked_keys": keys,
                "frozen_strategies": frozen,
                "callback_errors": errors,
            },
            actor=actor.actor_id,
            at=at,
        )
        self._report = KillSwitchReport(
            engaged=True,
            already_engaged=False,
            reason=clean_reason,
            actor=actor.actor_id,
            at=at,
            cancelled_orders=cancelled,
            revoked_leases=leases,
            revoked_keys=keys,
            frozen_strategies=frozen,
            callback_errors=tuple(errors),
            event_seq=event.seq,
        )
        return self._report


# ---------------------------------------------------------------------------
# Trading system
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    order: Order
    created: bool
    risk: RiskDecision | None
    fills: tuple[Fill, ...]
    receipt: BrokerResult | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.order.to_dict(),
            "created": self.created,
            "risk": None if self.risk is None else self.risk.to_dict(),
            "fills": [f.to_dict() for f in self.fills],
            "receipt": None if self.receipt is None else self.receipt.to_dict(),
        }


def _default_clock() -> float:
    import time

    return time.time()


class TradingSystem:
    """Paper/testnet trading orchestration; live capital is gated, not enabled."""

    def __init__(
        self,
        *,
        mode: TradingMode = TradingMode.PAPER,
        clock: Callable[[], float] | None = None,
        market: MarketDataService | None = None,
        strategies: StrategyRegistry | None = None,
        risk_policy: RiskPolicy | None = None,
        broker: PaperBroker | None = None,
        starting_cash_minor: int = 10_000_000,
        currency: str = "USD",
        signoff: OwnerRiskPolicySignoff | None = None,
        exchange_keys: Iterable[ExchangeKeyPolicy] = (),
        kill_switch: KillSwitch | None = None,
    ) -> None:
        self._mode = mode if isinstance(mode, TradingMode) else TradingMode(mode)
        self._clock = clock or _default_clock
        self._events = EventLog()
        self._ledger = DoubleEntryLedger()
        self._portfolio = Portfolio(self._ledger, currency=currency)
        self._strategies = strategies or StrategyRegistry()
        self._risk = RiskEngine(
            risk_policy or default_risk_policy()
        )
        self._market = market
        self._broker = broker or PaperBroker()
        self._oms = OrderManagementSystem(self._events)
        self._kill = kill_switch or KillSwitch()
        self._frozen = False
        self._keys = tuple(exchange_keys)
        for key in self._keys:
            key.assert_trade_only()
        now = self.now()
        if self._mode is TradingMode.LIVE:
            self._authorize_live(signoff, now)
        self._signoff = signoff
        funding = _non_negative_int(starting_cash_minor, "starting_cash_minor")
        if funding == 0:
            raise TradingError(BAD_INPUT, "starting cash must be positive")
        event = self._events.append(
            "portfolio.funded",
            {"amount_minor": funding, "currency": self._portfolio.currency},
            actor="system",
            at=now,
        )
        self._portfolio.fund(funding, at=now, event_seq=event.seq)
        if self._mode is TradingMode.LIVE:
            self._events.append(
                "mode.live_authorized",
                {
                    "policy_version": self._risk.policy.policy_version,
                    "owner_id": signoff.owner_id if signoff else "",
                },
                actor="owner",
                at=now,
            )

    # -- accessors -----------------------------------------------------
    @property
    def mode(self) -> TradingMode:
        return self._mode

    @property
    def events(self) -> EventLog:
        return self._events

    @property
    def ledger(self) -> DoubleEntryLedger:
        return self._ledger

    @property
    def portfolio(self) -> Portfolio:
        return self._portfolio

    @property
    def oms(self) -> OrderManagementSystem:
        return self._oms

    @property
    def risk(self) -> RiskEngine:
        return self._risk

    @property
    def strategies(self) -> StrategyRegistry:
        return self._strategies

    @property
    def market(self) -> MarketDataService | None:
        return self._market

    @property
    def broker(self) -> PaperBroker:
        return self._broker

    @property
    def kill_switch(self) -> KillSwitch:
        return self._kill

    @property
    def frozen(self) -> bool:
        return self._frozen

    def now(self) -> float:
        return float(self._clock())

    def freeze(self) -> None:
        self._frozen = True

    # -- live gate -----------------------------------------------------
    def _authorize_live(
        self, signoff: OwnerRiskPolicySignoff | None, now: float
    ) -> None:
        if self._mode is not TradingMode.LIVE:
            return
        if signoff is None:
            raise TradingError(
                LIVE_NOT_AUTHORIZED,
                "live mode requires an explicit owner risk-policy sign-off",
            )
        if not signoff.matches(self._risk.policy):
            raise TradingError(
                LIVE_NOT_AUTHORIZED,
                "owner sign-off does not match the active risk policy exactly",
            )
        if signoff.signed_at > now:
            raise TradingError(
                LIVE_NOT_AUTHORIZED, "owner sign-off is timestamped in the future"
            )
        if signoff.expires_at is not None and now > signoff.expires_at:
            raise TradingError(LIVE_NOT_AUTHORIZED, "owner sign-off has expired")
        if not self._keys:
            raise TradingError(
                LIVE_NOT_AUTHORIZED,
                "live mode needs at least one trade-only exchange key policy",
            )
        for key in self._keys:
            key.assert_trade_only()

    def enable_live(
        self,
        *,
        actor: Actor,
        signoff: OwnerRiskPolicySignoff,
    ) -> None:
        """Explicit, owner-gated mode change. Never automatic."""
        if actor.role is not Role.OWNER:
            raise TradingError(
                ROLE_REFUSED, "only the owner may enable live trading"
            )
        if self._mode is TradingMode.LIVE:
            return
        self._mode = TradingMode.LIVE
        try:
            self._authorize_live(signoff, self.now())
        except TradingError:
            self._mode = TradingMode.PAPER
            raise
        self._signoff = signoff
        self._events.append(
            "mode.live_authorized",
            {"policy_version": self._risk.policy.policy_version},
            actor=actor.actor_id,
            at=self.now(),
        )

    # -- policy --------------------------------------------------------
    def set_risk_policy(self, policy: RiskPolicy, *, actor: Actor) -> RiskPolicy:
        updated = self._risk.update_policy(policy, actor=actor)
        self._events.append(
            "risk.policy_updated",
            {"policy": updated.to_dict(), "role": actor.role.value},
            actor=actor.actor_id,
            at=self.now(),
        )
        if self._mode is TradingMode.LIVE and (
            self._signoff is None or not self._signoff.matches(updated)
        ):
            self._mode = TradingMode.PAPER
            self._events.append(
                "mode.live_revoked",
                {"reason": "risk policy changed after owner sign-off"},
                actor=actor.actor_id,
                at=self.now(),
            )
        return updated

    # -- execution -----------------------------------------------------
    def _assert_can_trade(self, actor: Actor) -> None:
        if self._frozen or self._kill.engaged:
            raise TradingError(
                KILL_SWITCH_ENGAGED, "trading is frozen by the kill switch"
            )
        if actor.role not in (Role.EXECUTION, Role.OWNER):
            raise TradingError(
                ROLE_REFUSED, "only execution or owner may submit orders"
            )
        if self._mode not in (TradingMode.PAPER, TradingMode.TESTNET, TradingMode.LIVE):
            raise TradingError(MODE_REFUSED, "unsupported trading mode")
        if self._market is None:
            raise TradingError(FEED_UNAVAILABLE, "no market data service is wired")

    def submit_order(
        self, actor: Actor, request: OrderRequest
    ) -> ExecutionReport:
        self._assert_can_trade(actor)
        existing = self._oms.find_by_client(request.client_order_id)
        if existing is not None:
            if self._oms.fingerprint_for(existing.order_id) != (
                request.fingerprint()
            ):
                raise TradingError(
                    IDEMPOTENCY_CONFLICT,
                    f"client order id {request.client_order_id!r} was already"
                    " used for a different order",
                )
            return ExecutionReport(
                order=existing,
                created=False,
                risk=None,
                fills=self._oms.fills_for(existing.order_id),
                receipt=None,
            )
        spec = self._strategies.get(request.strategy_id)
        if request.symbol not in spec.universe:
            raise TradingError(
                BAD_INPUT,
                f"strategy {spec.strategy_id!r} does not trade"
                f" {request.symbol}",
            )
        if self._mode is TradingMode.LIVE and spec.status != "paper":
            raise TradingError(
                MODE_REFUSED,
                "live orders need a strategy that reached paper status",
            )
        snapshot = self._market.snapshot([request.symbol])
        now = self.now()
        order, created = self._oms.submit(request, actor=actor, at=now)
        if not created:
            return ExecutionReport(
                order=order,
                created=False,
                risk=None,
                fills=self._oms.fills_for(order.order_id),
                receipt=None,
            )
        try:
            decision = self._risk.evaluate_order(
                request=request,
                snapshot=snapshot,
                portfolio=self._portfolio,
                now=now,
            )
        except TradingError as exc:
            self._oms.reject(
                order.order_id,
                reason=f"risk_error:{exc.code}",
                actor=actor,
                at=now,
            )
            raise
        if not decision.allowed:
            reason = "risk_veto:" + ",".join(decision.reasons)
            self._oms.reject(
                order.order_id, reason=reason, actor=actor, at=now
            )
            self._events.append(
                "risk.vetoed",
                {
                    "order_id": order.order_id,
                    "reasons": list(decision.reasons),
                    "checks": decision.to_dict()["checks"],
                },
                actor=actor.actor_id,
                at=now,
            )
            return ExecutionReport(
                order=order,
                created=True,
                risk=decision,
                fills=(),
                receipt=None,
            )
        self._oms.acknowledge(
            order.order_id, venue=self._broker.venue, actor=actor, at=now
        )
        receipt = self._broker.execute(order, snapshot, now=now)
        self._events.append(
            "broker.receipt",
            {"order_id": order.order_id, "receipt": receipt.to_dict()},
            actor=actor.actor_id,
            at=now,
        )
        fills: list[Fill] = []
        if receipt.status == "rejected":
            self._oms.reject(
                order.order_id,
                reason=receipt.reason or "broker rejected",
                actor=actor,
                at=receipt.received_at,
            )
        elif receipt.status == "unknown":
            self._oms.mark_unknown(
                order.order_id,
                reason=receipt.reason,
                actor=actor,
                at=receipt.received_at,
            )
        else:
            for broker_fill in receipt.fills:
                notional = _minor(broker_fill.quantity * broker_fill.price)
                fee = self._broker.fee_minor(notional)
                fill = self._oms.record_fill(
                    order.order_id,
                    quantity=broker_fill.quantity,
                    price=broker_fill.price,
                    fee_minor=fee,
                    actor=actor,
                    at=receipt.received_at,
                )
                self._portfolio.apply_fill(
                    symbol=order.symbol,
                    side=order.side,
                    quantity=fill.quantity,
                    price=fill.price,
                    fee_minor=fill.fee_minor,
                    order_id=order.order_id,
                    fill_id=fill.fill_id,
                    at=receipt.received_at,
                    event_seq=fill.event_seq,
                )
                fills.append(fill)
            self._portfolio.observe_equity(now, snapshot.marks)
        return ExecutionReport(
            order=order,
            created=True,
            risk=decision,
            fills=tuple(fills),
            receipt=receipt,
        )

    def reconcile_unknown(
        self,
        order_id: str,
        resolver: Callable[[Order], Mapping[str, Any]],
        *,
        actor: Actor,
    ) -> ExecutionReport:
        if actor.role not in (Role.EXECUTION, Role.RISK_MANAGER, Role.OWNER):
            raise TradingError(
                ROLE_REFUSED, "only execution, risk or owner may reconcile"
            )
        order = self._oms.get(order_id)
        if order.status != "unknown":
            raise TradingError(
                ORDER_STATE,
                f"order {order_id!r} is not unknown (state={order.status!r})",
            )
        try:
            outcome = resolver(order)
        except TradingError:
            raise
        except Exception as exc:  # noqa: BLE001 - injected resolver boundary
            raise TradingError(
                UNKNOWN_OUTCOME, f"reconciliation probe failed: {exc}"
            ) from exc
        if not isinstance(outcome, Mapping):
            raise TradingError(
                UNKNOWN_OUTCOME, "resolver must return a mapping outcome"
            )
        status = str(outcome.get("status", "")).lower()
        raw_fills = outcome.get("fills") or []
        parsed: list[tuple[Decimal, Decimal, int]] = []
        for item in raw_fills:
            if isinstance(item, Mapping):
                parsed.append(
                    (
                        _positive_decimal(item.get("quantity"), "fill quantity"),
                        _positive_decimal(item.get("price"), "fill price"),
                        _non_negative_int(item.get("fee_minor", 0), "fee_minor"),
                    )
                )
            else:
                quantity, price, fee = item
                parsed.append(
                    (
                        _positive_decimal(quantity, "fill quantity"),
                        _positive_decimal(price, "fill price"),
                        _non_negative_int(fee, "fee_minor"),
                    )
                )
        now = self.now()
        order, fills = self._oms.resolve_unknown(
            order_id,
            status=status,
            fills=parsed,
            reason=str(outcome.get("reason", "")),
            actor=actor,
            at=now,
        )
        for fill in fills:
            self._portfolio.apply_fill(
                symbol=fill.symbol,
                side=fill.side,
                quantity=fill.quantity,
                price=fill.price,
                fee_minor=fill.fee_minor,
                order_id=fill.order_id,
                fill_id=fill.fill_id,
                at=fill.at,
                event_seq=fill.event_seq,
            )
        if self._market is not None:
            try:
                snapshot = self._market.last or self._market.snapshot([order.symbol])
                self._portfolio.observe_equity(now, snapshot.marks)
            except TradingError:
                pass
        return ExecutionReport(
            order=order,
            created=False,
            risk=None,
            fills=fills,
            receipt=None,
        )

    def cancel_order(
        self, actor: Actor, order_id: str, *, reason: str = ""
    ) -> Order:
        if actor.role not in (
            Role.EXECUTION,
            Role.RISK_MANAGER,
            Role.AUDITOR,
            Role.OWNER,
        ):
            raise TradingError(
                ROLE_REFUSED, "only execution, risk, audit or owner may cancel"
            )
        return self._oms.cancel(
            order_id, reason=reason, actor=actor, at=self.now()
        )

    def cancel_all(self, actor: Actor, *, reason: str = "") -> int:
        if actor.role not in (
            Role.EXECUTION,
            Role.RISK_MANAGER,
            Role.AUDITOR,
            Role.OWNER,
        ):
            raise TradingError(
                ROLE_REFUSED, "only execution, risk, audit or owner may cancel"
            )
        cancelled = 0
        for order in self._oms.open_orders():
            self._oms.cancel(
                order.order_id,
                reason=reason or "cancel_all",
                actor=actor,
                at=self.now(),
            )
            cancelled += 1
        return cancelled

    # -- audit ---------------------------------------------------------
    def audit(self) -> dict[str, Any]:
        events_ok = self._events.verify()
        ledger_ok = self._ledger.is_balanced()
        fills = self._oms.all_fills()
        fill_ids = {f.fill_id for f in fills}
        journal_refs: set[str] = set()
        postings_ok = True
        for journal in self._ledger.journals():
            if journal.debit_minor != journal.credit_minor:
                postings_ok = False
            ref = journal.refs.get("fill_id")
            if ref is not None:
                journal_refs.add(str(ref))
        fills_backed = fill_ids <= journal_refs
        orders_backed = all(
            order.last_event_seq is not None for order in self._oms.list()
        )
        event_types = {r.event_type for r in self._events.records()}
        routed = any(
            order.status
            in ("acknowledged", "partially_filled", "filled", "unknown")
            for order in self._oms.list()
        )
        receipt_types = not routed or "broker.receipt" in event_types
        return {
            "ok": bool(
                events_ok
                and ledger_ok
                and fills_backed
                and orders_backed
                and postings_ok
                and receipt_types
            ),
            "events_verified": events_ok,
            "event_count": len(self._events),
            "ledger_balanced": ledger_ok,
            "ledger_totals": self._ledger.totals(),
            "postings_balanced": postings_ok,
            "orders_event_backed": orders_backed,
            "fills_ledger_backed": fills_backed,
            "fill_count": len(fills),
            "broker_receipts_present": receipt_types,
        }


@dataclass(frozen=True, slots=True)
class Auditor:
    """Independent, read-only audit identity; never submits or edits."""

    actor_id: str = "auditor"

    def audit(self, system: TradingSystem) -> dict[str, Any]:
        return system.audit()


def default_risk_policy() -> RiskPolicy:
    """A conservative paper-mode default; not a live trading recommendation."""
    return RiskPolicy(
        policy_version="paper-default-v1",
        max_order_notional_minor=500_000,
        max_leverage=Decimal("1"),
        max_concentration_pct=50,
        min_liquidity_multiple=2,
        max_stale_seconds=5,
        max_drawdown_pct=Decimal("20"),
        max_daily_loss_minor=200_000,
        allowed_symbols=(),
        allow_short=False,
    )
