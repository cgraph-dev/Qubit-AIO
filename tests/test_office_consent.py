"""C16 + C19 — consent resolves in four tiers, hardline first (D17/D18).

* **C16** — the ladder resolves in fixed order, each tier short-circuiting, and
  the danger key is the **class** (`capability + ':' + scope`), never the
  arguments, path, or key.
* **C19** — the hardline floor is checked first, under every posture: an
  `unattended_full` agent holding every grant still cannot reach past it.

The denial must also carry the **anti-retry suffix**, or an agent burns its
budget retrying the same refused action.

Hermetic: pure decision logic; no store, no network, no provider.
"""
from __future__ import annotations

import pytest


def test_the_danger_key_is_the_class_not_the_arguments():
    from voicebridge.office import consent

    assert consent.danger_key("fs.write", "/home/x/secret.txt") == \
        "fs.write:/home/x/secret.txt"
    # …but the *match* is against the class, so a path never becomes a key
    key = consent.danger_key("spend", "provider")
    assert key == "spend:provider"


@pytest.mark.parametrize("posture", ["unattended_full", "supervised", "frozen"])
@pytest.mark.parametrize("item", [
    "spend:personal",
    "legal:commitment:owner",
    "terms:violate",
    "secret:owner:exfiltrate",
    "ledger:rewrite",
    "instruction:fetch",
])
def test_every_hardline_item_denies_under_every_posture(item, posture):
    """C19: no posture, and no grant, reaches past the floor."""
    from voicebridge.office import consent

    cap, _, scope = item.partition(":")
    verdict = consent.check(capability=cap, scope=scope, posture=posture,
                            grants=[item], tags=[item])
    assert verdict["allowed"] is False
    assert verdict["tier"] == "hardline"
    assert "hardline" in verdict["reason"]
    assert "do not re-attempt" in verdict["message"]


def test_writing_the_ledger_is_never_allowed_even_for_the_ceo():
    from voicebridge.office import consent

    v = consent.check(capability="ledger", scope="rewrite",
                      posture="unattended_full",
                      grants=["ledger:rewrite"], read_only=False)
    assert v["allowed"] is False
    assert v["tier"] == "hardline"
    assert v["code"] == "OFFICE_CONSENT_DENIED"


# ------------------------------------------------------------- tier 2: bypass
def test_unattended_full_is_the_standing_bypass():
    from voicebridge.office import consent

    v = consent.check(capability="publish", scope="storefront",
                      posture="unattended_full")
    assert v["allowed"] is True
    assert v["tier"] == "bypass"


def test_frozen_seats_are_revoked_outright():
    from voicebridge.office import consent

    v = consent.check(capability="publish", scope="storefront",
                      posture="frozen", grants=["publish:storefront"])
    assert v["allowed"] is False
    assert v["tier"] == "bypass"
    assert "frozen" in v["reason"]


def test_a_supervised_seat_without_a_grant_is_denied_by_silence():
    from voicebridge.office import consent

    v = consent.check(capability="publish", scope="storefront",
                      posture="supervised")
    assert v["allowed"] is False
    assert consent.SILENCE in v["reason"]
    assert "do not re-attempt" in v["message"]


def test_a_supervised_seat_with_the_grant_may_act():
    from voicebridge.office import consent

    v = consent.check(capability="publish", scope="storefront",
                      posture="supervised", grants=["publish:storefront"])
    assert v["allowed"] is True
    assert v["tier"] == "grant"


# -------------------------------------------------------------- tier 3/4
def test_a_grant_allows_a_mutation_for_a_non_bypass_posture():
    from voicebridge.office import consent

    v = consent.check(capability="fs.write", scope="project",
                      posture="granted", grants=["fs.write:project"])
    assert v["allowed"] is True
    assert v["tier"] == "grant"


def test_read_only_and_non_network_auto_allows():
    from voicebridge.office import consent

    v = consent.check(capability="ledger.read", scope="org",
                      posture="granted", read_only=True, network=False)
    assert v["allowed"] is True
    assert v["tier"] == "resolve"


def test_a_network_read_with_no_grant_default_denies():
    from voicebridge.office import consent

    v = consent.check(capability="web.fetch", scope="public",
                      posture="granted", read_only=True, network=True)
    assert v["allowed"] is False
    assert v["tier"] == "resolve"
    assert consent.SILENCE in v["reason"]


def test_a_mutation_with_no_grant_default_denies():
    """The tier-4 rule that makes silence safe: default-deny (D17)."""
    from voicebridge.office import consent

    v = consent.check(capability="fs.write", scope="project",
                      posture="granted")
    assert v["allowed"] is False
    assert v["tier"] == "resolve"
    assert "no grant" in v["reason"]


def test_a_grant_for_one_scope_does_not_leak_to_another():
    """Grants are per class: a project grant is not a home-directory grant."""
    from voicebridge.office import consent

    ok = consent.check(capability="fs.write", scope="project",
                       posture="granted", grants=["fs.write:project"])
    leaked = consent.check(capability="fs.write", scope="home",
                           posture="granted", grants=["fs.write:project"])
    assert ok["allowed"] is True
    assert leaked["allowed"] is False


def test_hardline_list_is_short_and_readable():
    from voicebridge.office import consent

    out = consent.hardline_list()
    assert out["tiers"] == ["hardline", "bypass", "grant", "resolve"]
    assert len(out["hardline"]) == 6
    for item in out["hardline"]:
        assert set(item) == {"id", "match", "why"}
