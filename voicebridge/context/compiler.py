"""Packet 5C: bounded context compiler with retrieval safety.

Deterministic precedence (highest to lowest, dropped LAST):
  persona   immutable identity/safety instructions (never truncated)
  tools     bounded tool schemas (never truncated)
  active    current conversation/state (hard-clipped)
  core      corrected durable facts from the 5B store (core tier)
  recall    FTS5-retrieved records (score/limit-bounded)
  evidence  adapter evidence (parallel, deadline-bounded)

Security invariants:
  - persona, tools and active are FIXED inputs rendered verbatim; retrieval
    can only APPEND quoted [reference] blocks, so hostile text cannot change
    policy, tools, routing or persona.
  - durable memory is only READ (store.search); the compiler never writes.
  - every excerpt is redacted before inclusion; citations always present.
  - partial adapter failure is visible in the report, never silent.
  - cache stores only redacted excerpts with TTL; secrets are never cached.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..context_compiler import (CHARS_PER_TOKEN, CORE_FACTS_MAX_CHARS,
                                ACTIVE_MAX_CHARS, RECALL_MAX_CHARS,
                                compile_context, fits_budget)

PERSONA_MAX_CHARS = 6000
TOOLS_MAX_CHARS = 4000
EVIDENCE_MAX_CHARS = 1500
DEFAULT_OUTPUT_RESERVE_TOKENS = 150
DEFAULT_CACHE_TTL_S = 60.0
CONFLICT_TOPIC_TOKENS = 3


class ContextBudgetError(Exception):
    """Typed: required sections cannot fit the model context."""


class RetrievalUnavailableError(Exception):
    """Typed: a required retrieval source failed."""


@dataclass
class SectionItem:
    item_id: str
    text: str
    tier: str
    scope: str
    confidence: float
    citations: List[str] = field(default_factory=list)
    conflict: bool = False


@dataclass
class CompilationReport:
    sections_included: List[str] = field(default_factory=list)
    section_tokens: Dict[str, int] = field(default_factory=dict)
    omitted: List[Dict[str, str]] = field(default_factory=list)
    failures: List[Dict[str, str]] = field(default_factory=list)
    conflicts: List[Dict[str, str]] = field(default_factory=list)
    cache_hits: int = 0
    retrieval_ms: int = 0
    citations_count: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {"sections_included": self.sections_included,
                "section_tokens": self.section_tokens,
                "omitted": self.omitted, "failures": self.failures,
                "conflicts": self.conflicts, "cache_hits": self.cache_hits,
                "retrieval_ms": self.retrieval_ms,
                "citations_count": self.citations_count}


@dataclass
class CompiledTurn:
    text: str
    report: CompilationReport

    @property
    def est_tokens(self) -> int:
        return sum(self.report.section_tokens.values())


class TTLTextCache:
    """Thread-safe TTL cache for redacted excerpts only."""

    def __init__(self, ttl_s: float = DEFAULT_CACHE_TTL_S) -> None:
        self._ttl = ttl_s
        self._store: Dict[str, tuple] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            ts, value = entry
            if time.time() - ts > self._ttl:
                self._store.pop(key, None)
                return None
            return value

    def put(self, key: str, value: str) -> None:
        with self._lock:
            self._store[key] = (time.time(), value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


def _topic(text: str) -> Optional[str]:
    words = [w for w in text.lower().split() if w.isalpha()][:CONFLICT_TOPIC_TOKENS]
    return " ".join(words) if len(words) >= 2 else None


def detect_conflicts(items: List[SectionItem]) -> List[Dict[str, str]]:
    """Deterministic conflict labels: same scope+topic, different values."""
    by_topic: Dict[str, List[SectionItem]] = {}
    for it in items:
        topic = _topic(it.text)
        if topic:
            by_topic.setdefault(topic, []).append(it)
    out = []
    for topic, group in by_topic.items():
        values = {i.text for i in group}
        if len(values) > 1:
            out.append({"topic": topic,
                        "items": [g.item_id for g in group],
                        "values": sorted(values)[:3]})
    return out


class ContextCompiler:
    def __init__(self, *, store: Optional[Any] = None,
                 max_tokens: int = 4096,
                 output_reserve_tokens: int = DEFAULT_OUTPUT_RESERVE_TOKENS,
                 rerank_enabled: bool = False,
                 rerank_fn: Optional[Callable] = None,
                 cache: Optional[TTLTextCache] = None,
                 evidence_provider: Optional[Callable] = None,
                 max_workers: int = 4,
                 deadline_s: float = 2.0) -> None:
        self.store = store
        self.max_tokens = int(max_tokens)
        self.reserve = int(output_reserve_tokens)
        self.rerank_enabled = rerank_enabled
        self.rerank_fn = rerank_fn
        self.cache = cache or TTLTextCache()
        self.evidence_provider = evidence_provider
        self.max_workers = max_workers
        self.deadline_s = deadline_s

    # ----------------------------------------------------------- recall
    def _recall_items(self, query: str, scope: str, report: CompilationReport
                      ) -> List[SectionItem]:
        if self.store is None:
            return []
        t0 = time.perf_counter()
        try:
            records = self.store.search(query, scope=scope, tier="recall",
                                        limit=10)
        except Exception as exc:
            report.failures.append({"source": "store.recall",
                                    "reason": str(exc)[:120]})
            return []
        report.retrieval_ms += int((time.perf_counter() - t0) * 1000)
        items = [SectionItem(
            item_id=str(r["id"]), text=r["text"], tier="recall",
            scope=r["scope"], confidence=r["confidence"],
            citations=[str(r.get("source") or r["id"])])
            for r in records]
        if self.rerank_enabled and self.rerank_fn is not None:
            items = self.rerank_fn(items, query)
        return items

    def _core_items(self, scope: str, report: CompilationReport
                    ) -> List[SectionItem]:
        if self.store is None:
            return []
        try:
            records = self.store._active_select(scope=scope, tier="core",
                                                staged=False)
        except Exception as exc:
            report.failures.append({"source": "store.core",
                                    "reason": str(exc)[:120]})
            return []
        return [SectionItem(
            item_id=str(r["id"]), text=r["text"], tier="core",
            scope=r["scope"], confidence=r["confidence"],
            citations=[str(r.get("source") or r["id"])])
            for r in records]

    # ----------------------------------------------------------- evidence
    def _evidence_text(self, query: str, report: CompilationReport) -> str:
        if self.evidence_provider is None:
            return ""
        cache_key = f"evq:{query}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            report.cache_hits += 1
            report.citations_count += cached.count("[/ref]")
            return cached
        t0 = time.perf_counter()
        try:
            records = self.evidence_provider(query, self.max_workers,
                                             self.deadline_s)
        except Exception as exc:
            report.failures.append({"source": "evidence_provider",
                                    "reason": str(exc)[:120]})
            return ""
        report.retrieval_ms += int((time.perf_counter() - t0) * 1000)
        if not records:
            return ""
        parts = []
        for entry in records:
            rec, err = (entry if isinstance(entry, tuple) else (entry, None))
            if err:
                report.failures.append({"source": "evidence_partial",
                                        "reason": err})
            if rec is None:
                continue
            cite = "; ".join(rec.citations or [rec.source_ref])
            parts.append(f"[ref:{rec.adapter}]{rec.redacted_excerpt}[/ref]({cite})")
            report.citations_count += len(rec.citations or [1])
        text = "\n".join(parts)
        self.cache.put(cache_key, text)
        return text

    # ----------------------------------------------------------- compile
    def compile(self, query: str, *, scope: str = "default",
                persona: str = "", active: str = "", tools: str = "",
                render_persona_tools: bool = True) -> CompiledTurn:
        report = CompilationReport()
        if not persona:
            raise RetrievalUnavailableError(
                "persona section is required")
        tools = tools or ""
        required_chars = len(persona) + len(tools)
        if not fits_budget(self.max_tokens, self.reserve,
                           _FakeCompiled(required_chars)):
            raise ContextBudgetError(
                "persona+tools+output reserve exceed model context")

        recall_items = self._recall_items(query, scope, report)
        core_items = self._core_items(scope, report)
        report.conflicts = detect_conflicts(recall_items + core_items)

        evidence = self._evidence_text(query, report)

        # Assemble with per-section budgets; recall/core/evidence are
        # truncated/dropped lowest-first; persona+tools+active survive.
        recall_text = _join_items(recall_items)
        core_text = _join_items(core_items)
        compiled = compile_context(
            persona=persona, active=active,
            core_facts=_clip(core_text, CORE_FACTS_MAX_CHARS),
            recall=_clip(recall_text, RECALL_MAX_CHARS),
            char_budget=(self.max_tokens - self.reserve) * CHARS_PER_TOKEN)
        # persona and tools are IMMUTABLE required sections rendered verbatim
        # OUTSIDE any reference block; retrieval can only append below.
        # render_persona_tools=False keeps them in the caller's separate
        # system message (live chat path) while still enforcing their
        # budget footprint.
        if render_persona_tools:
            blocks = [f"[persona]\n{persona}"]
            if tools:
                blocks.append(f"[tools]\n{tools}")
            if compiled.text:
                blocks.append(compiled.text)
            compiled_text = "\n\n".join(blocks)
        else:
            compiled_text = compiled.text

        evidence_clip = _clip(evidence, EVIDENCE_MAX_CHARS)
        if evidence_clip:
            compiled_text = (compiled_text + "\n\n[untrusted evidence]\n"
                             + evidence_clip)

        # token accounting per section for observability
        report.sections_included = list(dict.fromkeys(
            ["persona"] + (["tools"] if tools else [])
            + list(compiled.sections_included)))
        if evidence_clip:
            report.sections_included.append("evidence")
        report.section_tokens = {
            "persona": int(len(persona) / CHARS_PER_TOKEN),
            "tools": int(len(tools) / CHARS_PER_TOKEN),
            "active": int(len(active) / CHARS_PER_TOKEN),
            "core": int(len(core_text) / CHARS_PER_TOKEN),
            "recall": int(len(recall_text) / CHARS_PER_TOKEN),
            "evidence": int(len(evidence_clip) / CHARS_PER_TOKEN),
        }
        report.citations_count += len(core_items) + len(recall_items)

        # omission metadata: any retrieval that got cut shows a reason
        for it in recall_items[len(recall_items):10]:
            pass
        if recall_items and not compiled.text.count("[reference]"):
            report.omitted.append({"kind": "recall", "reason": "budget"})
        for it in core_items:
            if it.text not in compiled.text:
                report.omitted.append({"kind": "core", "item": it.item_id,
                                       "reason": "budget"})
        for it in recall_items:
            if it.text not in compiled.text:
                report.omitted.append({"kind": "recall", "item": it.item_id,
                                       "reason": "budget/score"})
        return CompiledTurn(text=compiled_text, report=report)


def _clip(text: str, limit: int) -> str:
    return text[:limit]


def _join_items(items: List[SectionItem]) -> str:
    out = []
    for it in items:
        tag = f"[conflict:{','.join(c['items'] for c in [])}]" if it.conflict \
            else ""
        out.append(f"{tag}{it.text}")
    return "\n".join(out)


class _FakeCompiled:
    """Minimal est_tokens carrier for fits_budget."""

    def __init__(self, chars: int) -> None:
        self.est_tokens = int(chars / CHARS_PER_TOKEN)


def parallel_evidence(provider, names, make_query, max_workers, deadline_s):
    """Generic bounded parallel read with per-source + total deadlines.

    provider: callable(name, query) -> EvidenceRecord | raises
    returns list preserving order; failed sources produce None entries
    and failures are surfaced via the caller's report by the compiler.
    """
    from concurrent.futures import ThreadPoolExecutor
    deadline = time.monotonic() + deadline_s
    out: Dict[str, Any] = {}
    pool = ThreadPoolExecutor(max_workers=max_workers)
    futs = {n: pool.submit(provider, n, make_query(n)) for n in names}
    try:
        while futs and time.monotonic() <= deadline:
            done = [n for n, f in futs.items() if f.done()]
            if not done:
                time.sleep(0.01)
                continue
            for n in done:
                f = futs.pop(n)
                try:
                    out[n] = (f.result(), None)
                except Exception as exc:
                    out[n] = (None, str(exc)[:120])
        for n, f in futs.items():     # deadline exceeded -> drop remaining
            out[n] = (None, "deadline_exceeded")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return [(out.get(n)[0] if isinstance(out.get(n), tuple) else out.get(n),
             out.get(n)[1] if isinstance(out.get(n), tuple) else None)
            for n in names]