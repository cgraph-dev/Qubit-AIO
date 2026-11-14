"""War Room research-provider seam.

A War Room intake records *material*; research is the step that turns that
material into labelled claims. This module owns the provider contract, a
deterministic offline implementation, and an explicit seam for a real provider
that is **disabled by default**.

Rules the module enforces rather than documents:

* **No invented facts.** A ``FACT`` claim must name at least one captured
  source digest. The local provider only ever calls the *capture* of supplied
  material a fact; everything about demand, fit or the future is
  ``INFERENCE``, ``ASSUMPTION`` or ``UNKNOWN``.
* **No hidden network.** :class:`LocalResearchProvider` is pure and
  deterministic. :class:`RemoteResearchProvider` refuses with a typed
  ``BUSINESS_RESEARCH_NOT_CONFIGURED`` until a caller injects both an explicit
  transport and ``enabled=True``; shipping code never does.
* **Typed refusals.** An unknown provider or source kind is
  ``BUSINESS_RESEARCH_UNAVAILABLE``; a disabled/unconfigured real seam is
  ``BUSINESS_RESEARCH_NOT_CONFIGURED``. Neither is an empty success.

The interface is deliberately small so a future YouTube/article/OCR provider
can be plugged in without changing the War Room service.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from .errors import (
    BAD_INPUT_CODE,
    RESEARCH_FAILED_CODE,
    RESEARCH_NOT_CONFIGURED_CODE,
    RESEARCH_UNAVAILABLE_CODE,
    BusinessError,
)
from .util import redact
from .warroom import EVIDENCE_LABELS

#: The material shapes a research provider accepts. These are the War Room's
#: own source kinds (image/doc are extraction-only inputs, not research
#: inputs, until a provider exists that can reason about them).
RESEARCH_KINDS = ("url", "youtube", "note", "post")

LOCAL_PROVIDER_ID = "local-deterministic"
REMOTE_PROVIDER_ID = "remote"
PERPLEXITY_PROVIDER_ID = "perplexity"

#: Perplexity defaults, matching the voice orchestrator's ``intent`` settings.
DEFAULT_PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
DEFAULT_PERPLEXITY_MODEL = "sonar-pro"
DEFAULT_PERPLEXITY_TIMEOUT_S = 45.0


def _clean_text(value: Any, label: str, *, max_len: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} is longer than {max_len} characters"
        )
    return clean


def _clean_kind(value: Any) -> str:
    kind = _clean_text(value, "research input kind", max_len=20).lower()
    if kind not in RESEARCH_KINDS:
        raise BusinessError(
            RESEARCH_UNAVAILABLE_CODE,
            f"no research provider supports {kind!r}; expected one of "
            f"{', '.join(RESEARCH_KINDS)}",
        )
    return kind


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ResearchInput:
    """One piece of material handed to a provider."""

    kind: str
    locator: str
    title: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def normalised(self) -> "ResearchInput":
        return ResearchInput(
            kind=_clean_kind(self.kind),
            locator=_clean_text(self.locator, "research locator", max_len=2048),
            title=str(self.title or "").strip()[:300],
            metadata=dict(self.metadata or {}),
        )


@dataclass(frozen=True, slots=True)
class ResearchSource:
    """The provider's deterministic capture of one input."""

    kind: str
    locator: str
    title: str
    text: str
    digest: str
    adapter: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "locator": self.locator,
            "title": self.title,
            "digest": self.digest,
            "adapter": self.adapter,
            "chars": len(self.text),
        }


@dataclass(frozen=True, slots=True)
class ResearchClaim:
    """One labelled claim. A FACT must cite at least one source digest."""

    label: str
    claim: str
    source_digests: tuple[str, ...] = ()
    rationale: str = ""

    def __post_init__(self) -> None:
        label = str(self.label).upper()
        if label not in EVIDENCE_LABELS:
            raise BusinessError(
                BAD_INPUT_CODE,
                f"unknown research label {label!r}; expected one of "
                f"{', '.join(EVIDENCE_LABELS)}",
            )
        if label == "FACT" and not self.source_digests:
            raise BusinessError(
                "BUSINESS_WARROOM_UNVERIFIED_FACT",
                "a research FACT must cite at least one captured source",
            )
        if not str(self.claim).strip():
            raise BusinessError(BAD_INPUT_CODE, "research claim must be non-empty")

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "claim": self.claim,
            "source_digests": list(self.source_digests),
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True)
class ResearchResult:
    """What a provider returns: captured sources plus labelled claims."""

    provider: str
    inputs: tuple[ResearchInput, ...]
    sources: tuple[ResearchSource, ...]
    claims: tuple[ResearchClaim, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "inputs": [
                {"kind": item.kind, "locator": item.locator, "title": item.title}
                for item in self.inputs
            ],
            "sources": [item.as_dict() for item in self.sources],
            "claims": [item.as_dict() for item in self.claims],
            "labels": sorted({item.label for item in self.claims}),
        }


@runtime_checkable
class ResearchProvider(Protocol):
    """The contract a research backend implements."""

    provider_id: str

    def research(self, inputs: list[ResearchInput]) -> ResearchResult:
        ...  # pragma: no cover - protocol definition


class LocalResearchProvider:
    """Deterministic, offline, network-free research.

    It captures the supplied material (a note's text, a transcript supplied in
    metadata, or a deterministic locator digest) and separates what is known
    from what is merely assumed. It never fetches the locator.
    """

    provider_id = LOCAL_PROVIDER_ID

    def research(self, inputs: list[ResearchInput]) -> ResearchResult:
        clean_inputs = tuple(item.normalised() for item in inputs)
        sources: list[ResearchSource] = []
        claims: list[ResearchClaim] = []
        for item in clean_inputs:
            text = _capture_text(item)
            digest = _digest(text)
            title = item.title or _first_line(text) or item.locator[:120]
            sources.append(
                ResearchSource(
                    kind=item.kind,
                    locator=item.locator,
                    title=title,
                    text=text,
                    digest=digest,
                    adapter=LOCAL_PROVIDER_ID,
                )
            )
            claims.extend(_claims_for(item, digest))
        return ResearchResult(
            provider=self.provider_id,
            inputs=clean_inputs,
            sources=tuple(sources),
            claims=tuple(claims),
        )


class RemoteResearchProvider:
    """The optional real-provider seam, disabled by default.

    Constructing this provider performs no I/O. ``research`` refuses with
    ``BUSINESS_RESEARCH_NOT_CONFIGURED`` unless the caller explicitly passes
    ``enabled=True`` *and* a transport callable. The transport is injected so
    no product code path and no test can reach the network implicitly.
    """

    provider_id = REMOTE_PROVIDER_ID

    def __init__(
        self,
        *,
        enabled: bool = False,
        transport: Callable[[list[ResearchInput]], ResearchResult] | None = None,
        endpoint: str = "",
    ) -> None:
        self.enabled = bool(enabled)
        self._transport = transport
        self.endpoint = endpoint

    def research(self, inputs: list[ResearchInput]) -> ResearchResult:
        clean_inputs = [item.normalised() for item in inputs]
        if not self.enabled or self._transport is None:
            raise BusinessError(
                RESEARCH_NOT_CONFIGURED_CODE,
                "the remote research provider is disabled by default; configure "
                "an explicit transport and enable it before use",
            )
        try:
            result = self._transport(clean_inputs)
        except BusinessError:
            raise
        except Exception as exc:  # noqa: BLE001 - external seam
            raise BusinessError(
                RESEARCH_UNAVAILABLE_CODE,
                f"the remote research provider failed: "
                f"{type(exc).__name__}: {str(exc)[:200]}",
            ) from exc
        return result


@dataclass(frozen=True, slots=True)
class PerplexityRequest:
    """One request to the Perplexity chat-completions endpoint.

    ``payload`` is the exact JSON body a transport posts. The API key is not
    part of this object: it travels in the headers the caller builds, so a
    request can be logged or asserted without ever exposing the credential.
    """

    url: str
    model: str
    prompt: str
    inputs: tuple[ResearchInput, ...]
    timeout_s: float = DEFAULT_PERPLEXITY_TIMEOUT_S
    max_tokens: int = 900
    temperature: float = 0.2

    def payload(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": self.prompt}],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }


#: A transport receives the request and the headers (including the bearer
#: token) and returns the decoded JSON mapping. Tests inject a fake; the
#: shipped default is :func:`_http_post_json`.
PerplexityTransport = Callable[
    [PerplexityRequest, Mapping[str, str]], Mapping[str, Any]
]

_CITATION_MARKER = re.compile(r"\[(\d+)\]")


def _cfg_value(cfg: Any, *path: str, default: Any = None) -> Any:
    """Read a nested config value from a ``Config`` or a plain mapping."""
    try:
        getter = getattr(cfg, "get", None)
        if callable(getter):
            try:
                return getter(*path, default=default)
            except TypeError:
                node: Any = cfg
                for part in path:
                    if isinstance(node, Mapping):
                        node = node.get(part)
                    else:
                        return default
                return default if node is None else node
    except Exception:  # noqa: BLE001 - a bad config must not crash routing
        return default
    return default


def _env_file_value(name: str, candidates: list[str]) -> str:
    for candidate in candidates:
        path = Path(candidate).expanduser()
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if line.startswith(name + "="):
                return (
                    line.split("=", 1)[1].strip().strip('"').strip("'")
                )
    return ""


def perplexity_settings(
    cfg: Any = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str, str]:
    """Resolve ``(api_key, endpoint, model)`` without ever logging the key.

    Order for the key: the explicit ``environ`` mapping / process environment,
    then the configured ``intent.perplexity_env`` file, then the deepseek /
    canonical VoiceBridge ``.env``, then the canonical runtime ``.env``. A
    configured ``intent.perplexity_enabled: false`` disables the provider even
    when a key is present. The endpoint/model come from ``intent`` with the
    documented defaults.
    """
    explicit_env = environ is not None
    env = os.environ if environ is None else environ
    key = str(env.get("PERPLEXITY_API_KEY") or "").strip()

    # An explicit ``environ`` mapping is the whole credential source. Tests use
    # this to stay hermetic; production passes None so the configured files
    # are consulted after the process environment.
    if not explicit_env and not key:
        candidates: list[str] = []
        configured = str(
            _cfg_value(cfg, "intent", "perplexity_env", default="") or ""
        ).strip()
        if configured:
            candidates.append(configured)
        deepseek_env = str(
            _cfg_value(cfg, "intent", "deepseek_env", default="") or ""
        ).strip()
        candidates.append(
            deepseek_env or "~/.config/qubit-voicebridge/.env"
        )
        try:
            from ..config import canonical_runtime_env_path

            runtime_env = str(canonical_runtime_env_path())
        except Exception:  # noqa: BLE001 - unresolvable path means skip it
            runtime_env = ""
        if runtime_env and runtime_env not in candidates:
            candidates.append(runtime_env)
        key = _env_file_value("PERPLEXITY_API_KEY", candidates).strip()

    enabled = _cfg_value(cfg, "intent", "perplexity_enabled", default=True)
    if enabled is False:
        key = ""

    endpoint = str(
        _cfg_value(cfg, "intent", "perplexity_url",
                   default=DEFAULT_PERPLEXITY_URL)
        or DEFAULT_PERPLEXITY_URL
    ).strip()
    model = str(
        _cfg_value(cfg, "intent", "perplexity_model",
                   default=DEFAULT_PERPLEXITY_MODEL)
        or DEFAULT_PERPLEXITY_MODEL
    ).strip()
    return key, endpoint, model


def _build_perplexity_prompt(inputs: tuple[ResearchInput, ...]) -> str:
    question = ""
    material: list[str] = []
    for item in inputs:
        meta = dict(item.metadata or {})
        text = str(
            meta.get("text") or meta.get("transcript") or ""
        ).strip()
        if meta.get("query") or meta.get("role") == "query":
            question = question or text or item.locator
        elif not question and item.kind == "note" and not text:
            question = item.locator
        label = item.title or item.locator
        if text:
            material.append(
                f"- {item.kind}: {label}\n  excerpt: {text[:1200]}"
            )
        else:
            material.append(f"- {item.kind}: {label}")
    if not question:
        question = (
            "Assess the idea or material supplied for this War Room session."
        )
    lines = [
        "You are the War Room research analyst. Answer the research question"
        " using current, verifiable web sources.",
        "Return a concise answer in complete sentences. Cite the web source"
        " for every factual sentence with bracketed numbers like [1].",
        "Every statement of fact must be backed by a cited URL. If you cannot"
        " source a claim, state it as an inference instead of a fact.",
        f"Research question: {question}",
    ]
    if material:
        lines.append("Material supplied by the operator:")
        lines.extend(material)
    return "\n".join(lines)


def _answer_text(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        message = first.get("message") if isinstance(first, Mapping) else None
        if isinstance(message, Mapping):
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts: list[str] = []
                for part in content:
                    if isinstance(part, Mapping):
                        text = part.get("text")
                        if isinstance(text, str):
                            parts.append(text)
                    elif isinstance(part, str):
                        parts.append(part)
                if parts:
                    return "".join(parts)
    for key in ("answer", "output_text", "text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _citation_entries(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    by_url: dict[str, dict[str, str]] = {}

    def add(url: Any, title: Any = "", text: Any = "") -> None:
        clean = str(url or "").strip()
        if not clean:
            return
        if not clean.startswith(("http://", "https://")):
            return
        existing = by_url.get(clean)
        if existing is not None:
            # Later rows (``search_results``) carry the title/snippet, while
            # the ``citations`` list may hold the bare URL. Enrich in place.
            if title and not existing["title"]:
                existing["title"] = str(title)[:300]
            if text and not existing["text"]:
                existing["text"] = str(text)[:2000]
            return
        entry = {
            "url": clean,
            "title": str(title or "")[:300],
            "text": str(text or "")[:2000],
        }
        by_url[clean] = entry
        entries.append(entry)

    citations = payload.get("citations")
    if isinstance(citations, list):
        for item in citations:
            if isinstance(item, str):
                add(item)
            elif isinstance(item, Mapping):
                add(item.get("url"), item.get("title"),
                    item.get("text") or item.get("snippet"))
    results = payload.get("search_results")
    if isinstance(results, list):
        for item in results:
            if isinstance(item, Mapping):
                add(item.get("url"), item.get("title"),
                    item.get("text") or item.get("snippet")
                    or item.get("date"))
    return entries


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def _claims_from_answer(
    answer: str, sources: tuple[ResearchSource, ...]
) -> tuple[ResearchClaim, ...]:
    digests = [source.digest for source in sources]
    claims: list[ResearchClaim] = []
    for sentence in _split_sentences(answer)[:12]:
        markers = sorted({int(item) for item in _CITATION_MARKER.findall(sentence)})
        cited = tuple(
            digests[index - 1]
            for index in markers
            if 1 <= index <= len(digests)
        )
        clean = _CITATION_MARKER.sub("", sentence)
        clean = re.sub(r"\s{2,}", " ", clean).strip()
        if not clean:
            continue
        if cited:
            claims.append(
                ResearchClaim(
                    label="FACT",
                    claim=clean,
                    source_digests=cited,
                    rationale="the answer cites the matched source URL",
                )
            )
        else:
            claims.append(
                ResearchClaim(
                    label="INFERENCE",
                    claim=clean,
                    source_digests=tuple(digests),
                    rationale=(
                        "the answer did not cite a specific source for this"
                        " sentence"
                    ),
                )
            )
    if not claims:
        claims.append(
            ResearchClaim(
                label="UNKNOWN",
                claim=answer[:2000],
                rationale="the provider returned no discrete sentence",
            )
        )
    return tuple(claims)


def _scrub_secret(value: Any, secret: str) -> str:
    text = str(value)
    if secret:
        text = text.replace(secret, "***REDACTED***")
    return str(redact(text))


def _http_post_json(
    request: PerplexityRequest, headers: Mapping[str, str]
) -> Mapping[str, Any]:
    """The shipped transport. Only called when a real provider is configured."""
    body = json.dumps(request.payload()).encode("utf-8")
    http_request = urllib.request.Request(
        request.url, data=body, headers=dict(headers), method="POST"
    )
    try:
        with urllib.request.urlopen(
            http_request, timeout=request.timeout_s
        ) as response:
            raw = response.read(2_000_000)
    except urllib.error.HTTPError as exc:
        # The response body is never echoed: it can contain the key hint or
        # provider account metadata, and the status is enough to act on.
        raise RuntimeError(f"HTTP {exc.code} from the Perplexity endpoint") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"network error: {exc.reason}") from None
    except OSError as exc:
        raise RuntimeError(f"transport error: {exc}") from None
    try:
        decoded = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise RuntimeError("Perplexity returned invalid JSON") from None
    if not isinstance(decoded, Mapping):
        raise RuntimeError("Perplexity returned a non-object response")
    return decoded


class PerplexityResearchProvider:
    """Real, optional Perplexity research behind an injected transport.

    No request is made until :meth:`research` is called *and* a key is
    configured. Tests pass a fake ``transport``; the shipped
    :func:`_http_post_json` is only used when the operator configured
    ``PERPLEXITY_API_KEY``. The API key travels only in the ``Authorization``
    header and is scrubbed from every error string.
    """

    provider_id = PERPLEXITY_PROVIDER_ID

    def __init__(
        self,
        *,
        api_key: str = "",
        endpoint: str = DEFAULT_PERPLEXITY_URL,
        model: str = DEFAULT_PERPLEXITY_MODEL,
        transport: PerplexityTransport | None = None,
        timeout_s: float = DEFAULT_PERPLEXITY_TIMEOUT_S,
    ) -> None:
        self._api_key = str(api_key or "").strip()
        self.endpoint = str(endpoint or DEFAULT_PERPLEXITY_URL).strip()
        self.model = str(model or DEFAULT_PERPLEXITY_MODEL).strip()
        self._transport = transport
        self.timeout_s = float(timeout_s)

    @classmethod
    def from_config(
        cls,
        cfg: Any = None,
        *,
        transport: PerplexityTransport | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> "PerplexityResearchProvider":
        key, endpoint, model = perplexity_settings(cfg, environ=environ)
        return cls(
            api_key=key,
            endpoint=endpoint,
            model=model,
            transport=transport,
        )

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def research(self, inputs: list[ResearchInput]) -> ResearchResult:
        clean_inputs = tuple(item.normalised() for item in inputs)
        if not clean_inputs:
            raise BusinessError(
                BAD_INPUT_CODE, "research needs at least one input"
            )
        if not self._api_key:
            raise BusinessError(
                RESEARCH_NOT_CONFIGURED_CODE,
                "PERPLEXITY_API_KEY is not configured; add it to the"
                " VoiceBridge .env or set intent.perplexity_enabled and a key"
                " before running Perplexity research",
            )
        request = PerplexityRequest(
            url=self.endpoint,
            model=self.model,
            prompt=_build_perplexity_prompt(clean_inputs),
            inputs=clean_inputs,
            timeout_s=self.timeout_s,
        )
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        transport = self._transport or _http_post_json
        try:
            payload = transport(request, headers)
        except BusinessError:
            raise
        except Exception as exc:  # noqa: BLE001 - external seam
            detail = _scrub_secret(
                f"Perplexity research request failed: "
                f"{type(exc).__name__}: {str(exc)[:200]}",
                self._api_key,
            )
            raise BusinessError(RESEARCH_FAILED_CODE, detail) from None
        return _perplexity_result(clean_inputs, payload, self._api_key)


def _perplexity_result(
    inputs: tuple[ResearchInput, ...],
    payload: Any,
    api_key: str,
) -> ResearchResult:
    if not isinstance(payload, Mapping):
        raise BusinessError(
            RESEARCH_FAILED_CODE,
            "Perplexity returned a non-object response",
        )
    error = payload.get("error")
    if error:
        if isinstance(error, Mapping):
            error = error.get("message") or error.get("detail") or error
        raise BusinessError(
            RESEARCH_FAILED_CODE,
            _scrub_secret(f"Perplexity returned an error: {error}", api_key),
        )
    answer = _answer_text(payload).strip()
    if not answer:
        raise BusinessError(
            RESEARCH_FAILED_CODE, "Perplexity returned no answer text"
        )
    sources = tuple(
        ResearchSource(
            kind="url",
            locator=entry["url"],
            title=entry["title"] or entry["url"][:120],
            text=entry["text"] or answer,
            digest=_digest(f"{PERPLEXITY_PROVIDER_ID}\0{entry['url']}"),
            adapter=PERPLEXITY_PROVIDER_ID,
        )
        for entry in _citation_entries(payload)
    )
    return ResearchResult(
        provider=PERPLEXITY_PROVIDER_ID,
        inputs=inputs,
        sources=sources,
        claims=_claims_from_answer(answer, sources),
    )


class UnavailableResearchProvider:
    """A provider that is deliberately absent, for typed-refusal paths."""

    provider_id = "unavailable"

    def research(self, inputs: list[ResearchInput]) -> ResearchResult:
        raise BusinessError(
            RESEARCH_UNAVAILABLE_CODE,
            "no research provider is installed for this War Room",
        )


def resolve_provider(
    name: str | None = None,
    *,
    cfg: Any = None,
    transport: PerplexityTransport | None = None,
    environ: Mapping[str, str] | None = None,
) -> ResearchProvider:
    """Resolve a provider by name. The local deterministic adapter is default.

    ``perplexity`` resolves to the real provider, configured from the passed
    config/environment; without a key it is constructed but refuses with a
    typed ``not_configured`` when called. ``remote`` resolves to the disabled
    generic seam. Anything unknown is a typed ``unavailable`` refusal. Only
    the Perplexity resolver reads a key, and it never logs it.
    """
    requested = (name or LOCAL_PROVIDER_ID).strip().lower()
    if requested in ("", LOCAL_PROVIDER_ID, "local"):
        return LocalResearchProvider()
    if requested in (PERPLEXITY_PROVIDER_ID, "real-perplexity", "pplx"):
        return PerplexityResearchProvider.from_config(
            cfg, transport=transport, environ=environ
        )
    if requested in (REMOTE_PROVIDER_ID, "real"):
        return RemoteResearchProvider()
    raise BusinessError(
        RESEARCH_UNAVAILABLE_CODE,
        f"unknown research provider {name!r}; expected "
        f"{LOCAL_PROVIDER_ID!r}, {PERPLEXITY_PROVIDER_ID!r} or "
        f"{REMOTE_PROVIDER_ID!r}",
    )


def _capture_text(item: ResearchInput) -> str:
    meta = dict(item.metadata or {})
    supplied = meta.get("text") or meta.get("transcript") or meta.get("alt_text")
    if supplied is not None and str(supplied).strip():
        return str(supplied).strip()
    if item.kind == "note":
        return item.locator
    digest = _digest(f"{item.kind}\0{item.locator}")
    return (
        f"[{item.kind} research capture — deterministic local adapter, no fetch]\n"
        f"locator: {item.locator}\n"
        f"digest: {digest}\n"
        "The locator was recorded but not fetched; its content is unknown."
    )


def _first_line(text: str) -> str:
    for line in text.splitlines():
        clean = line.strip()
        if clean:
            return clean[:120]
    return ""


def _claims_for(item: ResearchInput, digest: str) -> list[ResearchClaim]:
    subject = item.title or item.locator
    return [
        ResearchClaim(
            label="FACT",
            claim=(
                f"Captured {item.kind} material for {subject!r} "
                f"(sha256 {digest})."
            ),
            source_digests=(digest,),
            rationale="the capture itself is recorded in this session",
        ),
        ResearchClaim(
            label="INFERENCE",
            claim=(
                f"If the captured {item.kind} material is current, it may inform "
                "the session hypothesis; the local adapter does not verify it."
            ),
            source_digests=(digest,),
            rationale="derived from the capture, not independently checked",
        ),
        ResearchClaim(
            label="ASSUMPTION",
            claim=(
                f"The captured {item.kind} material is relevant, representative "
                "and current for the target customer."
            ),
            rationale="untested assumption; the owner must confirm it",
        ),
        ResearchClaim(
            label="UNKNOWN",
            claim=(
                "Whether this material generalises to the target market, and "
                "what it implies for demand, price or effort, is unverified."
            ),
            rationale="no live research was performed",
        ),
    ]


__all__ = [
    "DEFAULT_PERPLEXITY_MODEL",
    "DEFAULT_PERPLEXITY_TIMEOUT_S",
    "DEFAULT_PERPLEXITY_URL",
    "LOCAL_PROVIDER_ID",
    "PERPLEXITY_PROVIDER_ID",
    "REMOTE_PROVIDER_ID",
    "RESEARCH_KINDS",
    "LocalResearchProvider",
    "PerplexityRequest",
    "PerplexityResearchProvider",
    "PerplexityTransport",
    "RemoteResearchProvider",
    "ResearchClaim",
    "ResearchInput",
    "ResearchProvider",
    "ResearchResult",
    "ResearchSource",
    "UnavailableResearchProvider",
    "perplexity_settings",
    "resolve_provider",
]
