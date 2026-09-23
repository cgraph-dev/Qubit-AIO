"""Packet 8P: verified skill memory (optional, disabled by default).

Public package surface. Re-exports the constants, dataclasses, store, and
factory so callers use ``from voicebridge.verified_skills import ...``. All
logic lives in clearly owned sub-components:

- ``_models``   pure constants + dataclasses + deterministic helpers
- ``_persist``  SQLite schema + CRUD
- ``_lifecycle``verified-before-remember state transitions
- ``_recall``   budgeted top-K recall + tiers + retention
- ``_transfer`` snapshot/restore/export
- ``store``     the composed ``VerifiedSkillStore``
"""
from __future__ import annotations

from ._models import (COST_CHEAP, COST_EXPENSIVE, COST_NORMAL, DEFAULT_HOT_CAP,
                      DEFAULT_MAX_NEGATIVES, DEFAULT_NEGATIVE_THRESHOLD,
                      DEFAULT_TOKEN_BUDGET, DEFAULT_TOP_K, DEDUP_THRESHOLD,
                      HUMAN_REQUESTERS, KIND_NEGATIVE, KIND_POSITIVE,
                      MIN_ACCEPTANCE_STRENGTH,
                      SKILL_COSTS, SKILL_KINDS, SKILL_STATE_SCHEMA,
                      SKILL_STATUSES, SKILL_TIERS, ST_ARCHIVED, ST_COLD,
                      ST_HOT, ST_QUARANTINED, ST_REFUTED, ST_VERIFIED,
                      TIER_COLD, TIER_HOT, TIER_WARM, TRUST_OBSERVED,
                      TRUST_TOOL, TRUST_USER, TRUSTS, SecretRejectedError,
                      SkillProposal, SkillRecord, VerifiedSkillError,
                      VerifiedSkillStateError, acceptance_strength, bound,
                      cosine, digest, is_human_requester, looks_secret,
                      normalize_words, recipe_embedding, recency_decay,
                      utility_score)
from .store import VerifiedSkillStore, verified_skills_store
from ._bundled import (BUNDLED_SKILLS, BUNDLED_SKILL_SCHEMA, BundledSkillError,
                       bundled_skill_manifests, bundled_skill_names,
                       bundled_skills, bundled_skills_root, get_bundled_skill,
                       load_bundled_skill, load_bundled_skill_manifest,
                       parse_skill_front_matter, register_bundled_skills,
                       validate_bundled_skill_manifest)
from .workshop import (COLLECTION_CONTRACTS, CURATOR_ROLES, WorkshopError,
                       SkillWorkshop, verified_skill_workshop)
from .mutations import (SKILL_MUTATION_CAPABILITY, MUTATION_ACTIONS,
                        MUTATION_RISK, SkillMutationError, apply_skill_mutation,
                        handle_skill_mutation, mutation_risk,
                        pending_skill_mutations, settle_skill_mutation,
                        settle_skill_mutation_if_owned, stage_skill_mutation)

__all__ = [
    "ST_QUARANTINED", "ST_VERIFIED", "ST_REFUTED", "ST_ARCHIVED",
    "SKILL_STATUSES", "KIND_POSITIVE", "KIND_NEGATIVE", "SKILL_KINDS",
    "TIER_HOT", "TIER_WARM", "TIER_COLD", "SKILL_TIERS", "ST_HOT", "ST_COLD",
    "COST_CHEAP", "COST_NORMAL", "COST_EXPENSIVE", "SKILL_COSTS",
    "TRUST_USER", "TRUST_TOOL", "TRUST_OBSERVED", "TRUSTS",
    "SKILL_STATE_SCHEMA", "DEFAULT_TOP_K", "DEFAULT_TOKEN_BUDGET",
    "DEFAULT_NEGATIVE_THRESHOLD", "DEFAULT_MAX_NEGATIVES", "DEFAULT_HOT_CAP",
    "DEDUP_THRESHOLD", "MIN_ACCEPTANCE_STRENGTH",
    "VerifiedSkillError", "VerifiedSkillStateError", "SecretRejectedError",
    "HUMAN_REQUESTERS", "is_human_requester",
    "SkillProposal", "SkillRecord", "VerifiedSkillStore",
    "verified_skills_store",
    "SkillWorkshop", "verified_skill_workshop", "WorkshopError",
    "COLLECTION_CONTRACTS", "CURATOR_ROLES",
    "BUNDLED_SKILLS", "BUNDLED_SKILL_SCHEMA", "BundledSkillError",
    "bundled_skill_manifests", "bundled_skill_names", "bundled_skills",
    "bundled_skills_root", "get_bundled_skill", "load_bundled_skill",
    "load_bundled_skill_manifest", "parse_skill_front_matter",
    "register_bundled_skills", "validate_bundled_skill_manifest",
    "acceptance_strength", "recipe_embedding", "cosine", "bound",
    "looks_secret", "digest", "normalize_words", "recency_decay",
    "utility_score",
    "SKILL_MUTATION_CAPABILITY", "MUTATION_ACTIONS", "MUTATION_RISK",
    "SkillMutationError", "mutation_risk", "stage_skill_mutation",
    "settle_skill_mutation", "settle_skill_mutation_if_owned",
    "apply_skill_mutation", "handle_skill_mutation",
    "pending_skill_mutations",
]
