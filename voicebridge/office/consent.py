"""Consent: four tiers, resolved in order, hardline first (D17/D18).

```
1. HARDLINE      unconditional deny floor — checked FIRST, so no grant,
                 posture, or promotion can reach past it
2. BYPASS        the owner's explicit full-access posture, as a *live*
                 predicate so a revocation applies on the very next call
3. GRANT         allowed if this danger CLASS was granted for the session
                 or permanently
4. RESOLVE       read-only and non-network auto-allows; a mutation with no
                 grant DEFAULT-DENIES
```

Two rules carry most of the weight:

* **The danger key is the class** — ``capability + ':' + scope`` — never the
  arguments, the path, or the API key. Gating on arguments means every new
  argument is an unvetted hole; gating on the class means a scope is either
  trusted or it is not.
* **Silence is not consent.** An unattended org cannot approve its own way
  forward by waiting, so a tier-4 mutation with no grant denies — and the
  denial carries an **anti-retry suffix** so the agent stops burning turns
  re-attempting it.

This module is pure decision logic: it reads state, returns a verdict, and
changes nothing. Its first real consumer is :mod:`voicebridge.office.runner`,
which asks before spending on a provider call.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Sequence

#: Tier names, in resolution order. Exposed so callers can assert *which* tier
#: allowed or denied — a bare boolean cannot be audited.
TIERS = ("hardline", "bypass", "grant", "resolve")

#: The unconditional floor (D18). Short by design: this is the one place a
#: mistake is unrecoverable, so it stays small enough to reason about in one
#: sitting. Each entry is (id, matcher, what it means). The matcher sees the
#: danger class and any explicit tags the caller passes.
HARDLINE: Sequence[Dict[str, str]] = (
    {"id": "personal_spend",
     "match": "spend:personal",
     "why": "spending on anything that is not a business input"},
    {"id": "owner_legal_commitment",
     "match": "legal:commitment:owner",
     "why": "creating an account, contract, or legal commitment in the "
            "owner's name without a per-instance approval naming the "
            "counterparty"},
    {"id": "platform_terms",
     "match": "terms:violate",
     "why": "anything that would put the owner on the wrong side of a "
            "platform's published terms"},
    {"id": "exfiltrate_owner",
     "match": "secret:owner:exfiltrate",
     "why": "exfiltrating the owner's secrets, private vault contents, or "
            "unrelated personal data"},
    {"id": "ledger_tamper",
     "match": "ledger:rewrite",
     "why": "deleting or rewriting the ledger"},
    {"id": "fetched_instructions",
     "match": "instruction:fetch",
     "why": "acting on instructions found in fetched content (prompt "
            "injection is data, never a directive)"},
)

#: Appended to every denial. Without it an agent retries the same denied action
#: until its budget is gone (StarNet's dryStreak failure).
ANTI_RETRY = (" This is a final answer for this action: do not re-attempt it "
              "in this run. Choose a different action, or raise it to a human "
              "if it is genuinely required.")

#: The tier-4 refusal sentence, verbatim from the plan (D17).
SILENCE = ("autonomous run cannot self-approve this action — silence is not "
           "consent")


def danger_key(capability: str, scope: str = "") -> str:
    """The danger **class**: never the arguments, path, or key."""
    cap = (capability or "").strip().lower()
    sc = (scope or "").strip().lower()
    return f"{cap}:{sc}" if sc else cap


def _hardline_hit(key: str, tags: Iterable[str]) -> Optional[Dict[str, str]]:
    haystack = {key, *[str(t).strip().lower() for t in tags if t]}
    for item in HARDLINE:
        if item["match"] in haystack:
            return item
    return None


def check(cfg: Any = None, *, agent_id: Optional[int] = None,
          capability: str, scope: str = "", posture: str = "unattended_full",
          grants: Optional[Iterable[str]] = None,
          tags: Optional[Iterable[str]] = None,
          read_only: bool = False, network: bool = False) -> Dict[str, Any]:
    """Resolve one action against the four tiers, in order.

    Returns a verdict, never a bare boolean: ``allowed``, the ``tier`` that
    decided, the ``danger_key``, a human ``reason``, and — on a denial — the
    ``anti_retry`` suffix the caller should hand back to the agent.
    """
    key = danger_key(capability, scope)
    tag_list = list(tags or [])

    def verdict(allowed: bool, tier: str, reason: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "allowed": allowed, "tier": tier, "danger_key": key,
            "capability": capability, "scope": scope,
            "posture": posture, "reason": reason,
        }
        if not allowed:
            out["code"] = "OFFICE_CONSENT_DENIED"
            out["anti_retry"] = ANTI_RETRY
            out["message"] = reason + ANTI_RETRY
        return out

    # --- tier 1: the hardline floor, before anything else ---------------
    hit = _hardline_hit(key, tag_list)
    if hit is not None:
        return verdict(False, "hardline",
                       f"refused: '{key}' is on the hardline floor "
                       f"({hit['id']}: {hit['why']}). No grant, posture, or "
                       f"owner approval reaches past this tier.")

    # --- tier 2: the owner's standing bypass, as a live predicate -------
    if (posture or "").strip() == "frozen":
        return verdict(False, "bypass",
                       "refused: the seat is frozen; its standing bypass is "
                       "off and its room grants are revoked (D19)")
    if (posture or "").strip() == "unattended_full":
        return verdict(True, "bypass",
                       "allowed by the owner's standing full-access posture")
    if (posture or "").strip() == "supervised":
        # a supervised seat acts only on what it already holds a grant for
        granted = {str(g).strip().lower() for g in (grants or [])}
        if key in granted:
            return verdict(True, "grant",
                           "allowed: this danger class is granted to the seat")
        return verdict(False, "bypass",
                       f"refused: the seat is supervised and '{key}' is not "
                       f"granted, so it would be staged for the owner. "
                       f"{SILENCE}.")

    # --- tier 3: the grant cache ---------------------------------------
    granted = {str(g).strip().lower() for g in (grants or [])}
    if key in granted:
        return verdict(True, "grant",
                       "allowed: this danger class is granted")

    # --- tier 4: resolve ----------------------------------------------
    if read_only and not network:
        return verdict(True, "resolve",
                       "allowed: read-only and non-network")
    if read_only and network:
        return verdict(False, "resolve",
                       f"refused: '{key}' reads over the network with no "
                       f"grant. {SILENCE}.")
    return verdict(False, "resolve",
                   f"refused: '{key}' is a mutation with no grant. {SILENCE}.")


def hardline_list() -> Dict[str, Any]:
    """The floor as data, so a surface can show exactly what is refused."""
    return {"state": "OK", "tiers": list(TIERS),
            "hardline": [dict(item) for item in HARDLINE],
            "note": ("owner-editable and short by design: this is the one "
                     "place a mistake is unrecoverable (D18)")}
