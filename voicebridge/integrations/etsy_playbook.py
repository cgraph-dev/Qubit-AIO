"""Etsy digital-download pilot playbook — machine-readable source of truth.

The markdown twin is ``docs/QUBIT-ETSY-PILOT-PLAYBOOK.md``. This module carries
the same content as data so tests, UI and agents can validate that a launch
plan is complete without parsing prose.

The pilot is deliberately narrow: one personal Etsy shop, digital downloads
(templates/printables/planners), no inventory or shipping.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PILOT_ID = "etsy-digital-downloads"
PILOT_NAME = "Etsy digital-download pilot"
SELLER_MODEL_NOW = "personal_seller"
SELLER_MODEL_LATER = "separate_legal_entity"


@dataclass(frozen=True, slots=True)
class PlaybookSection:
    key: str
    title: str
    required: bool
    items: tuple[str, ...]
    human_approval: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "required": self.required,
            "items": list(self.items),
            "human_approval": self.human_approval,
        }


SECTIONS: tuple[PlaybookSection, ...] = (
    PlaybookSection(
        key="offer",
        title="Digital downloads only",
        required=True,
        items=(
            "Sell digital downloads only: templates, printables and planners.",
            "No physical inventory, no shipping, no custom manufacturing in the pilot.",
            "Every product needs a downloadable file, a licence statement and a preview image.",
        ),
    ),
    PlaybookSection(
        key="niche_selection",
        title="Niche selection",
        required=True,
        items=(
            "Use the War Room to compare demand, competition, price and effort.",
            "Score each niche on demand, competition, price, effort, IP risk and speed to first sale.",
            "Prefer narrow, repeatable evergreen needs over trend-chasing one-offs.",
            "Record evidence labels (FACT/INFERENCE/ASSUMPTION/UNKNOWN); no fabricated research.",
        ),
    ),
    PlaybookSection(
        key="ip_licence_checks",
        title="IP and licence checks",
        required=True,
        items=(
            "Use only original work or assets with explicit commercial-use rights.",
            "No copied art, fonts, characters, trademarks or AI-generated images of protected IP.",
            "Keep a licence record for every font, icon and mockup used.",
            "Reject any listing whose provenance or licence is uncertain.",
        ),
        human_approval=True,
    ),
    PlaybookSection(
        key="listing",
        title="Listing, SEO and images",
        required=True,
        items=(
            "Write a 140-character title led by the buyer's search phrase.",
            "Use all 13 tags, each 20 characters or fewer.",
            "Write a description with what is included, file formats, sizes and how to use it.",
            "Provide clear mockups/previews and list every included file.",
            "Set the digital-download listing type and attach the actual deliverable.",
        ),
        human_approval=True,
    ),
    PlaybookSection(
        key="pricing",
        title="Pricing",
        required=True,
        items=(
            "Set a price from the niche comparison, not from hope.",
            "Account for Etsy fees, payment processing, tax and refunds.",
            "Start with a simple price; no fake discounts or invented 'was' prices.",
            "Price changes require human approval.",
        ),
        human_approval=True,
    ),
    PlaybookSection(
        key="shop_policies",
        title="Shop policies and taxes",
        required=True,
        items=(
            "Publish digital-download shop policies: no shipping, instant delivery.",
            "State the refund policy and the licence for personal vs commercial use.",
            "Configure tax settings and personal seller details.",
            "No policy may promise something the product cannot deliver.",
        ),
        human_approval=True,
    ),
    PlaybookSection(
        key="support",
        title="Customer support",
        required=True,
        items=(
            "Draft replies for download issues, licence questions and product questions.",
            "Answer within the shop's stated response window.",
            "Escalate complaints, chargebacks and legal threats to the owner.",
            "No automated message may commit to custom work or a new price.",
        ),
        human_approval=True,
    ),
    PlaybookSection(
        key="refunds",
        title="Refunds",
        required=True,
        items=(
            "Follow the published refund policy and Etsy seller rules.",
            "Draft refund decisions; the owner approves every refund above the cap.",
            "Record the reason and the receipt for every refund.",
            "No automatic refund and no fake review or testimonial.",
        ),
        human_approval=True,
    ),
    PlaybookSection(
        key="analytics",
        title="Analytics and review",
        required=True,
        items=(
            "Track visits, conversion, favourites, messages, refunds and revenue.",
            "Use Etsy Ads only within an owner-approved budget cap.",
            "Review weekly and improve the weakest listing first.",
            "Report the metric and its source, not a guess.",
        ),
    ),
    PlaybookSection(
        key="launch_checklist",
        title="Launch checklist",
        required=True,
        items=(
            "Niche and product brief approved.",
            "IP/licence record complete.",
            "Digital file, preview and mockups ready.",
            "Title, description and 13 tags written.",
            "Price and shop policies set.",
            "First listing published with human approval.",
            "Support/refund wording ready and approval limits configured.",
            "Analytics baseline captured.",
        ),
        human_approval=True,
    ),
    PlaybookSection(
        key="human_approval_points",
        title="Human approval points",
        required=True,
        items=(
            "First publish of every listing.",
            "Claims, pricing, discounts and guarantees.",
            "Sensitive buyer replies and complaints.",
            "Any refund or spend (including Etsy Ads).",
            "Any irreversible action or credential change.",
        ),
        human_approval=True,
    ),
    PlaybookSection(
        key="seller_model",
        title="Personal seller now, entity later",
        required=True,
        items=(
            "Start as a personal seller one personal Etsy shop.",
            "Keep personal and future company money/data separate from day one.",
            "Record every sale, fee and refund for bookkeeping.",
            "Move to a separate legal entity later through an explicit migration.",
        ),
    ),
)

REQUIRED_SECTION_KEYS: tuple[str, ...] = tuple(
    section.key for section in SECTIONS if section.required
)

APPROVAL_POINTS: tuple[str, ...] = (
    "first_publish",
    "claims_pricing",
    "sensitive_replies",
    "refund",
    "spend",
    "irreversible_actions",
)


def playbook() -> dict[str, Any]:
    """Return the full pilot as a plain, testable mapping."""
    return {
        "pilot_id": PILOT_ID,
        "name": PILOT_NAME,
        "product_type": "digital_download",
        "seller_model_now": SELLER_MODEL_NOW,
        "seller_model_later": SELLER_MODEL_LATER,
        "approval_points": list(APPROVAL_POINTS),
        "sections": [section.as_dict() for section in SECTIONS],
        "required_section_keys": list(REQUIRED_SECTION_KEYS),
    }


def validate_playbook(data: dict[str, Any] | None = None) -> list[str]:
    """Return a list of completeness problems; empty means complete."""
    doc = playbook() if data is None else data
    problems: list[str] = []
    if doc.get("product_type") != "digital_download":
        problems.append("product_type must be digital_download")
    if doc.get("seller_model_now") != SELLER_MODEL_NOW:
        problems.append("seller_model_now must be personal_seller")
    if doc.get("seller_model_later") != SELLER_MODEL_LATER:
        problems.append("seller_model_later must be separate_legal_entity")
    sections = {item.get("key"): item for item in doc.get("sections", [])}
    for key in REQUIRED_SECTION_KEYS:
        section = sections.get(key)
        if section is None:
            problems.append(f"missing required section: {key}")
            continue
        items = [str(item).strip() for item in section.get("items", [])]
        if not items or any(not item for item in items):
            problems.append(f"section {key} has no usable items")
    approvals = set(doc.get("approval_points", []))
    for point in (
        "first_publish",
        "claims_pricing",
        "sensitive_replies",
        "refund",
        "spend",
        "irreversible_actions",
    ):
        if point not in approvals:
            problems.append(f"missing approval point: {point}")
    return problems


__all__ = [
    "APPROVAL_POINTS",
    "PILOT_ID",
    "PILOT_NAME",
    "PlaybookSection",
    "REQUIRED_SECTION_KEYS",
    "SELLER_MODEL_LATER",
    "SELLER_MODEL_NOW",
    "SECTIONS",
    "playbook",
    "validate_playbook",
]
