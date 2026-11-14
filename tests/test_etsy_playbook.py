"""Etsy digital-download pilot playbook completeness."""
from __future__ import annotations

from pathlib import Path

from voicebridge.integrations.etsy_playbook import (
    APPROVAL_POINTS,
    PILOT_ID,
    REQUIRED_SECTION_KEYS,
    SELLER_MODEL_LATER,
    SELLER_MODEL_NOW,
    SECTIONS,
    playbook,
    validate_playbook,
)

DOC = Path(__file__).resolve().parents[1] / "docs" / "QUBIT-ETSY-PILOT-PLAYBOOK.md"


def test_playbook_is_complete():
    assert validate_playbook() == []


def test_playbook_is_digital_downloads_only():
    doc = playbook()
    assert doc["pilot_id"] == PILOT_ID
    assert doc["product_type"] == "digital_download"
    assert doc["seller_model_now"] == SELLER_MODEL_NOW == "personal_seller"
    assert doc["seller_model_later"] == SELLER_MODEL_LATER == "separate_legal_entity"


def test_required_sections_are_present_and_non_empty():
    doc = playbook()
    sections = {section["key"]: section for section in doc["sections"]}
    for key in REQUIRED_SECTION_KEYS:
        assert key in sections, key
        items = sections[key]["items"]
        assert items
        assert all(str(item).strip() for item in items)
    assert len(REQUIRED_SECTION_KEYS) == len(SECTIONS)


def test_human_approval_points_cover_the_master_plan():
    doc = playbook()
    approvals = set(doc["approval_points"])
    assert approvals == set(APPROVAL_POINTS)
    for point in (
        "first_publish",
        "claims_pricing",
        "sensitive_replies",
        "refund",
        "spend",
        "irreversible_actions",
    ):
        assert point in approvals


def test_playbook_names_the_expected_launch_topics():
    text = " ".join(
        [section["title"] for section in playbook()["sections"]]
        + [
            item
            for section in playbook()["sections"]
            for item in section["items"]
        ]
    ).lower()
    for phrase in (
        "digital downloads",
        "niche",
        "licence",
        "title",
        "tags",
        "price",
        "policies",
        "refund",
        "analytics",
        "launch",
        "personal seller",
        "legal entity",
    ):
        assert phrase in text, phrase


def test_validate_playbook_detects_missing_sections():
    doc = playbook()
    doc["sections"] = [
        section
        for section in doc["sections"]
        if section["key"] != "ip_licence_checks"
    ]
    problems = validate_playbook(doc)
    assert any("ip_licence_checks" in problem for problem in problems)


def test_markdown_twin_exists_and_covers_the_headings():
    assert DOC.is_file()
    text = DOC.read_text(encoding="utf-8")
    for heading in (
        "Digital downloads only",
        "Niche selection",
        "IP and licence checks",
        "Listing, SEO and images",
        "Pricing",
        "Shop policies and taxes",
        "Customer support",
        "Refunds",
        "Analytics and review",
        "Launch checklist",
        "Human approval points",
        "Personal seller now, entity later",
    ):
        assert heading in text, heading
    assert "first listing published with human approval" in text.lower()
