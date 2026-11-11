"""Etsy digital-shop pilot bootstrap.

Creates one owner-reviewable War Room session from the shipped Etsy playbook,
with the plan, experiments and risks recorded as *labelled* claims. It is a
research/intake convenience, not a launch:

* it never approves the idea, creates tasks, publishes, spends, or contacts
  anyone;
* it never calls the network (the research provider defaults to the
  deterministic local adapter);
* a repeated call returns the same session instead of opening a second one;
* the session is left in ``owner_decision`` with **no** decision row, so only
  the human owner can move it forward.

Everything is stated as a FACT, INFERENCE, ASSUMPTION or UNKNOWN. A FACT is
always backed by the extracted playbook source; the demand/price/conversion
claims are explicitly unproven.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from voicebridge.integrations import etsy_playbook

from . import models, warroom
from .errors import BAD_STATE_CODE, BusinessError
from .research import (
    ResearchInput,
    ResearchProvider,
    ResearchResult,
    resolve_provider,
)

PILOT_TITLE = "Etsy digital shop pilot"
PLAYBOOK_SOURCE_KIND = "note"
PLAYBOOK_SOURCE_LOCATOR = f"playbook:{etsy_playbook.PILOT_ID}"


@dataclass(frozen=True, slots=True)
class EtsyPilotResult:
    """A bootstrap result: the durable session plus what was recorded."""

    tenant_id: str
    business_id: str | None
    session: models.WarRoomSession
    created: bool
    playbook_id: str
    source_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    experiment_names: tuple[str, ...] = ()
    research_provider: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "business_id": self.business_id,
            "created": self.created,
            "session": self.session,
            "playbook_id": self.playbook_id,
            "source_ids": list(self.source_ids),
            "evidence_ids": list(self.evidence_ids),
            "experiment_names": list(self.experiment_names),
            "research_provider": self.research_provider,
        }


def render_playbook_text(playbook: dict[str, Any] | None = None) -> str:
    """Render the playbook as deterministic source text (no filesystem read)."""
    doc = etsy_playbook.playbook() if playbook is None else playbook
    lines = [
        str(doc.get("name") or etsy_playbook.PILOT_NAME),
        f"pilot_id: {doc.get('pilot_id')}",
        f"product_type: {doc.get('product_type')}",
        f"seller_model_now: {doc.get('seller_model_now')}",
        f"seller_model_later: {doc.get('seller_model_later')}",
        f"approval_points: {', '.join(doc.get('approval_points') or [])}",
    ]
    for section in doc.get("sections") or []:
        lines.append("")
        lines.append(f"## {section.get('title')}")
        for item in section.get("items") or []:
            lines.append(f"- {item}")
    return "\n".join(lines).strip() + "\n"


def _pilot_brief() -> dict[str, Any]:
    """A complete War Room brief with honest, unvalidated values.

    The proposed tasks live in the brief only; ``create_tasks`` is owner-gated
    and is deliberately not called here.
    """
    return {
        "idea": "Open an Etsy shop selling original digital downloads "
                "(templates, printables, planners).",
        "niche": "not selected yet; compare niches with scored evidence before "
                 "committing",
        "icp": "Etsy shoppers looking for printable templates and planners "
               "(assumption; unvalidated)",
        "offer": "digital downloads only; no inventory, shipping or custom "
                 "manufacturing in the pilot",
        "channels": ["etsy"],
        "content_plan": [
            "listing mockups and previews for the first product",
            "Pinterest pins after the first listing is approved",
        ],
        "lead_gen_plan": [
            "organic Etsy search only in the pilot",
            "no Etsy Ads or paid promotion until the owner approves a budget",
        ],
        "website_app_scope": "none in the pilot; the Etsy shop is the storefront",
        "pricing": {
            "status": "unset",
            "note": "set from the niche comparison; owner approval required",
        },
        "budget": {
            "monthly_minor": 0,
            "note": "this bootstrap authorises no spend",
        },
        "risks": [
            "IP/licence provenance",
            "Etsy platform policy",
            "unproven demand",
            "pricing and fees",
            "refunds and customer support",
        ],
        "plan_30_60_90": {
            "30": "select a niche and record the licence/provenance check",
            "60": "draft the first listing, files, previews and copy",
            "90": "owner-reviewed first publish and an analytics baseline",
        },
        "tasks": [
            {"title": "Record the niche comparison and evidence labels"},
            {"title": "Collect the IP/licence record for every asset"},
            {"title": "Draft the first digital product and preview images"},
            {
                "title": "Write the listing title, description and 13 tags",
                "note": "proposed only; created after owner approval",
            },
            {"title": "Prepare support and refund wording"},
        ],
        "experiments": [
            {
                "name": "Niche demand check",
                "hypothesis": "A narrow evergreen niche shows enough Etsy demand",
                "method": "record search results, competitor count and price bands",
                "success_metric": "demand and competition recorded for 3 niches",
            },
            {
                "name": "First listing conversion test",
                "hypothesis": "A clear listing converts views into favourites",
                "method": "publish one owner-approved listing and measure for 30 days",
                "success_metric": "views -> favourites -> sales over 30 days",
            },
            {
                "name": "Licence audit",
                "hypothesis": "Every shipped asset has commercial-use rights",
                "method": "record the licence for each font, icon, mockup and file",
                "success_metric": "every asset has a recorded licence",
            },
        ],
    }


def _pilot_score() -> dict[str, Any]:
    return {"demand": 5, "effort": 4, "cost": 2, "risk": 6, "fit": 7}


def _plan_claims(source_id: str) -> list[dict[str, Any]]:
    """The plan/risk evidence the pilot records, labelled honestly."""
    return [
        {
            "label": "FACT",
            "claim": "The Etsy digital-download pilot playbook requires human "
                     "approval before the first publish, any price or claim "
                     "change, refunds and spend.",
            "source_id": source_id,
        },
        {
            "label": "INFERENCE",
            "claim": "If the pilot follows the playbook, the first listing can "
                     "be prepared without inventory or shipping costs.",
            "source_id": source_id,
        },
        {
            "label": "ASSUMPTION",
            "claim": "There is enough Etsy demand for the selected "
                     "digital-download niche to justify one pilot listing.",
        },
        {
            "label": "ASSUMPTION",
            "claim": "A personal Etsy seller account can be opened and approved "
                     "for this pilot.",
        },
        {
            "label": "UNKNOWN",
            "claim": "The achievable conversion, price point and competitive "
                     "position of the chosen niche are unknown until the niche "
                     "is selected and measured.",
        },
        {
            "label": "UNKNOWN",
            "claim": "The actual Etsy fee and tax treatment for this seller is "
                     "not verified in this bootstrap.",
        },
    ]


def _record_research(
    store: Any,
    *,
    tenant_id: str,
    session_id: str,
    source_id: str,
    result: ResearchResult,
) -> list[str]:
    evidence_ids: list[str] = []
    for claim in result.claims:
        evidence, _ = warroom.add_evidence(
            store,
            tenant_id=tenant_id,
            session_id=session_id,
            label=claim.label,
            claim=claim.claim,
            source_id=source_id,
            metadata={
                "research_provider": result.provider,
                "source_digests": list(claim.source_digests),
                "rationale": claim.rationale,
            },
        )
        evidence_ids.append(evidence.evidence_id)
    return evidence_ids


def _record_debate(
    store: Any,
    *,
    tenant_id: str,
    session_id: str,
    fact_id: str,
    unknown_id: str,
) -> None:
    """Three real roles so the synthesis has dissent to preserve."""
    warroom.add_argument(
        store,
        tenant_id=tenant_id,
        session_id=session_id,
        round_no=1,
        role="researcher",
        stance="support",
        text="The playbook defines a complete digital-download launch path.",
        evidence_ids=[fact_id],
    )
    warroom.add_argument(
        store,
        tenant_id=tenant_id,
        session_id=session_id,
        round_no=1,
        role="skeptic",
        stance="question",
        text="Demand, conversion and price are unproven until the niche is "
             "measured.",
        evidence_ids=[unknown_id],
    )
    warroom.add_argument(
        store,
        tenant_id=tenant_id,
        session_id=session_id,
        round_no=1,
        role="legal_platform_policy",
        stance="neutral",
        text="Every asset needs a recorded commercial-use licence, and no "
             "listing may publish before the owner approves it.",
        evidence_ids=[fact_id],
    )


def _snapshot(
    store: Any,
    *,
    tenant_id: str,
    business_id: str | None,
    session: models.WarRoomSession,
    created: bool,
    provider: str,
) -> EtsyPilotResult:
    sources = warroom.list_sources(store, tenant_id, session.session_id)
    evidence = warroom.list_evidence(store, tenant_id, session.session_id)
    experiments = warroom.list_experiments(store, tenant_id, session.session_id)
    return EtsyPilotResult(
        tenant_id=tenant_id,
        business_id=business_id,
        session=session,
        created=created,
        playbook_id=etsy_playbook.PILOT_ID,
        source_ids=tuple(item.source_id for item in sources),
        evidence_ids=tuple(item.evidence_id for item in evidence),
        experiment_names=tuple(item.name for item in experiments),
        research_provider=provider,
    )


def bootstrap_etsy_pilot(
    store: Any,
    *,
    tenant_id: str,
    business_id: str | None = None,
    provider: ResearchProvider | str | None = None,
    idempotency_key: str | None = None,
) -> EtsyPilotResult:
    """Open (or return) the single owner-pending Etsy pilot War Room session.

    Repeat calls are idempotent: the same tenant/business resolves to the same
    ``idempotency_key`` and the existing session is returned untouched.
    """
    if isinstance(provider, str) or provider is None:
        research = resolve_provider(provider)
    else:
        research = provider

    playbook = etsy_playbook.playbook()
    problems = etsy_playbook.validate_playbook(playbook)
    if problems:
        raise BusinessError(
            BAD_STATE_CODE,
            "the shipped Etsy pilot playbook is incomplete: "
            + "; ".join(problems),
        )

    # Run research before opening anything: an unconfigured/absent provider is
    # a typed refusal with no half-created session left behind.
    playbook_text = render_playbook_text(playbook)
    research_result = research.research([
        ResearchInput(
            kind=PLAYBOOK_SOURCE_KIND,
            locator=PLAYBOOK_SOURCE_LOCATOR,
            title=etsy_playbook.PILOT_NAME,
            metadata={"text": playbook_text},
        )
    ])

    key = (
        idempotency_key.strip()
        if isinstance(idempotency_key, str) and idempotency_key.strip()
        else f"etsy-pilot:{tenant_id}:{business_id or '-'}"
    )
    session, created = warroom.create_session(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        title=PILOT_TITLE,
        idempotency_key=key,
    )
    if not created:
        return _snapshot(
            store,
            tenant_id=tenant_id,
            business_id=business_id,
            session=session,
            created=False,
            provider=research.provider_id,
        )

    session = warroom.advance(
        store,
        tenant_id=tenant_id,
        session_id=session.session_id,
        target="research",
    )

    source, _ = warroom.add_source(
        store,
        tenant_id=tenant_id,
        session_id=session.session_id,
        kind=PLAYBOOK_SOURCE_KIND,
        locator=PLAYBOOK_SOURCE_LOCATOR,
        title=etsy_playbook.PILOT_NAME,
        metadata={
            "playbook_id": etsy_playbook.PILOT_ID,
            "seller_model_now": etsy_playbook.SELLER_MODEL_NOW,
            "seller_model_later": etsy_playbook.SELLER_MODEL_LATER,
            "approval_points": list(etsy_playbook.APPROVAL_POINTS),
            "text": playbook_text,
        },
    )
    if source.status != "extracted":
        warroom.extract_source(
            store,
            tenant_id=tenant_id,
            session_id=session.session_id,
            source_id=source.source_id,
        )

    evidence_ids = _record_research(
        store,
        tenant_id=tenant_id,
        session_id=session.session_id,
        source_id=source.source_id,
        result=research_result,
    )
    for claim in _plan_claims(source.source_id):
        evidence, _ = warroom.add_evidence(
            store,
            tenant_id=tenant_id,
            session_id=session.session_id,
            metadata={"origin": "etsy-pilot-bootstrap"},
            **claim,
        )
        evidence_ids.append(evidence.evidence_id)

    session = warroom.advance(
        store,
        tenant_id=tenant_id,
        session_id=session.session_id,
        target="debate",
    )
    evidence = warroom.list_evidence(store, tenant_id, session.session_id)
    fact_id = next(item.evidence_id for item in evidence
                   if item.label == "FACT" and item.source_id == source.source_id)
    unknown_id = next(item.evidence_id for item in evidence
                      if item.label == "UNKNOWN")
    _record_debate(
        store,
        tenant_id=tenant_id,
        session_id=session.session_id,
        fact_id=fact_id,
        unknown_id=unknown_id,
    )

    brief = _pilot_brief()
    session = warroom.synthesize(
        store,
        tenant_id=tenant_id,
        session_id=session.session_id,
        brief=brief,
        score=_pilot_score(),
        experiments=list(brief.get("experiments") or []),
    )
    # Leave the decision to the human owner: state ``owner_decision`` with no
    # decision row, no tasks, no approval, nothing executed.
    session = warroom.advance(
        store,
        tenant_id=tenant_id,
        session_id=session.session_id,
        target="owner_decision",
    )

    return _snapshot(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        session=session,
        created=True,
        provider=research.provider_id,
    )


__all__ = [
    "EtsyPilotResult",
    "PILOT_TITLE",
    "PLAYBOOK_SOURCE_KIND",
    "PLAYBOOK_SOURCE_LOCATOR",
    "bootstrap_etsy_pilot",
    "render_playbook_text",
]
