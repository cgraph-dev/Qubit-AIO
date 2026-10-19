"""Qubit free-tier JSON-RPC handlers: a renderer reads the profile's local auth state (pull); nothing
is pushed except the boot bootstrap's one ``setup.ready`` event. ``free_tier.status`` answers from the
auth store with zero network and zero side effects; ``free_tier.provision`` is the explicit retry when
the boot bootstrap could not create the identity (desktop-only entry); ``free_tier.ack_notice``
persists the one-time notice flag on the free-tier identity itself, so it dies with that identity.
Bodies are rebound onto server.py's globals (method_ctx.bind_module) and reference them bare.
"""

import logging

from .method_ctx import HandlerRegistry, bind_module

logger = logging.getLogger(__name__)
_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


@method("free_tier.status")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """``{has_guest, enabled, available, notice_pending, model, label}`` for the focused profile.
    ``available`` = an identity exists AND the tier is on: the free tier (connectors, and the model
    when nothing else carries inference) is there for this install. Whether inference actually runs
    on it is a ROUTE question answered by ``setup.runtime_check.free_tier``, never by this flag.
    ``notice_pending`` is true until ``free_tier.ack_notice`` ran for this identity.
    ``nudge_due_in``: seconds until the sign-in offer after a finished task is due (install-wide,
    ``qubit_cli.free_tier_offer``); absent when none is pending.

    A pure read. The identity is created by the boot bootstrap (``free_tier_bootstrap``), never as
    a side effect of a client polling this method (NS-845 Q1.2)."""
    try:
        from qubit_cli import anon_auth
        has_guest = anon_auth.has_guest()
        # ``enabled``: the free tier is on here, OR the welcome trial can still be claimed on this
        # machine (``_trial_claimable``; upstream's reading). Qubit's owner ruling pins the trial
        # structurally unreachable, so the term is False here and ``enabled`` is ``guest_enabled()``.
        enabled = anon_auth.guest_enabled() or _trial_claimable()
        payload = {
            "has_guest": has_guest, "enabled": enabled, "available": has_guest and enabled,
            "notice_pending": bool(has_guest and enabled and anon_auth.guest_notice_pending()),
            "model": anon_auth.GUEST_MODEL, "label": anon_auth.FREE_TIER_LABEL}
        from qubit_cli import free_tier_offer
        if (nudge_due_in := free_tier_offer.offer_due_in()) is not None:
            payload["nudge_due_in"] = nudge_due_in
        if enabled and not has_guest:
            # Why there is no identity, when the last attempt to make one failed:
            # ``{error, error_code, retryable, retry_after}`` (the mint memo's verdict).
            payload.update(anon_auth.last_mint_failure() or {})
        # A browser challenge the account service is waiting on (``anon_challenge``): the same
        # payload the ``free_tier.challenge`` event carried, for a client that connected after it.
        from qubit_cli import anon_challenge
        challenge = anon_challenge.pending_challenge()
        if challenge:
            payload["challenge"] = challenge
        return _ok(rid, payload)
    except Exception as e:
        return _err(rid, 5090, str(e))


def _trial_claimable() -> bool:
    """The welcome trial (free inference on ``qubit/welcome`` for a first install) can be claimed on
    this machine. ``free_tier.status``/``provision`` read it to decide whether the trial is part of
    what ``enabled`` reports. Qubit's owner ruling pins ``anon_auth.on_local_desktop_backend`` to
    False, which is the first blocker in the ladder, so this is False on every install — the
    machinery is kept, deliberately, as the design to rebrand if Qubit ships its own sign-in."""
    from qubit_cli.welcome_trial import claim_blocker
    return claim_blocker() is None


@method("free_tier.provision")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """Explicit retry of the free-tier set-up for the focused profile: adopt the shared store's
    identity, else mint one (blocking, short timeout). The boot bootstrap normally did this already;
    the desktop calls this when the record says the identity is missing (portal down at boot, gate
    turned on later) and the user asks again. The user's click is the one attempt that may run
    inside the mint memo's cooldown. ``{has_guest, enabled}``, plus
    ``{error, error_code, retryable, retry_after}`` when the portal refused."""
    try:
        from qubit_cli import anon_auth
        from qubit_cli import free_tier_bootstrap
        guest_on = anon_auth.guest_enabled()
        # The welcome trial's claim, exactly as upstream asks for it: only when nothing blocks it on
        # this machine. Qubit's owner ruling pins ``on_local_desktop_backend()`` to False, so this is
        # always skipped here — the branch stays as the design to rebrand if Qubit ships its own sign-in.
        if _trial_claimable():
            try:
                from qubit_cli import welcome_trial
                welcome_trial.claim_desktop_free_tier(force=True)
            except Exception as exc:   # memoised by the mint primitive before it re-raised
                logger.info("welcome trial claim failed: %s", exc)
            if not params.get("profile") and free_tier_bootstrap.current_record() is not None:
                free_tier_bootstrap.refresh_record()
        elif guest_on and not anon_auth.has_guest():
            if free_tier_bootstrap.current_record() is not None and not params.get("profile"):
                # The launch profile: refresh the boot record too, so ``setup.status`` and the
                # ``setup.ready`` listeners move with the outcome.
                free_tier_bootstrap.retry_bootstrap_mint(force=True)
            else:
                try:
                    anon_auth.ensure_portal_identity(explicit=True, force=True)
                except Exception as exc:   # memoised by the primitive before it re-raised
                    logger.info("free tier provisioning failed: %s", exc)
        has_guest = anon_auth.has_guest()
        enabled = guest_on or _trial_claimable()
        payload = {"has_guest": has_guest, "enabled": enabled}
        if enabled and not has_guest:
            payload.update(anon_auth.last_mint_failure() or {})
        return _ok(rid, payload)
    except Exception as e:
        return _err(rid, 5092, str(e))


@method("free_tier.ack_notice")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """Mark the availability notice shown on the free-tier identity. ``acked`` is false when there is
    no free-tier identity to mark (nothing to show again either)."""
    try:
        from qubit_cli import anon_auth
        return _ok(rid, {"acked": bool(anon_auth.mark_guest_notice_shown())})
    except Exception as e:
        return _err(rid, 5091, str(e))


@method("free_tier.claim_nudge")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """Claim the due sign-in offer. Claim before showing, so two windows never both show it:
    ``claimed`` is true for exactly one caller each time an offer comes due."""
    try:
        from qubit_cli import free_tier_offer
        return _ok(rid, {"claimed": bool(free_tier_offer.claim_offer())})
    except Exception as e:  # the RPC boundary: report it, never crash the dispatcher
        logger.warning("free_tier.claim_nudge failed", exc_info=True)
        return _err(rid, 5093, str(e))


@method("free_tier.challenge_result")
@_profile_scoped
def _(rid, params: dict) -> dict:
    from qubit_cli.anon_challenge import record_host_outcome
    return _ok(rid, {"accepted": record_host_outcome(params["url"], params.get("attempt", 0), params["outcome"])})


def register(server) -> None:
    bind_module(globals(), server, skip=("_",))
