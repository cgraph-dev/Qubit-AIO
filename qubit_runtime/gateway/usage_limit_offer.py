"""Usage-window limit offers: after a refused turn, the plan's other models as a choice.

The limiter (``agent.plan_limits``) says which models still work and how credits could carry
on. A chat that already has buttons gets one picker; any other chat gets one sentence. A tap
switches THIS session only, then resends the refused message through the normal /retry path.
Never a credits button (anyone in the room could spend the owner's credits) and never a
model switch on its own.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# The plan's provider: suggestions are plan models, so a switch names it explicitly.
_PLAN_PROVIDER = "nous"


class GatewayUsageLimitOfferMixin:
    """Limit-offer rendering and the post-delivery schedule/accept path."""

    # ── wording ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _usage_window_chat_wording(refusal: Any, reply: str) -> str:
        """Reword a usage-window refusal for a chat: credits point at the Portal, never /topup."""
        if not isinstance(refusal, dict) or not refusal.get("usage_window_message"):
            return reply
        from agent.plan_limits import portal_wording
        return portal_wording(reply)

    # ── delivery hook ────────────────────────────────────────────────────────────────────

    async def _deliver_turn_response_then_limit_offer(
        self, event, source, session_entry, session_key, run_generation,
        agent_result, agent_messages, response, footer_line, intentional_silence,
    ):
        """Deliver the reply, then offer the plan's alternative models for a usage-window limit."""
        delivered = await self._hmwa_deliver_turn_response(
            event, source, session_entry, session_key, run_generation,
            agent_result, agent_messages, response, footer_line, intentional_silence,
        )
        limit = agent_result.get("usage_limit") if isinstance(agent_result, dict) else None
        if limit:
            self._schedule_usage_limit_offer(
                event, limit, session_key=session_key, generation=run_generation)
        return delivered

    def _schedule_usage_limit_offer(self, event, limit: dict, *, session_key: str,
                                    generation: Optional[int] = None) -> None:
        """Fire the offer only after the reply has landed: a picker under the reply."""
        adapter = self._delivery_adapter_for(event.source) if event is not None else None
        if adapter is not None and hasattr(adapter, "register_post_delivery_callback"):
            async def _deliver() -> None:
                await self._offer_usage_limit_switch(event, limit, generation)
            adapter.register_post_delivery_callback(session_key, _deliver, generation=generation)
            return
        self._track_background_task(self._offer_usage_limit_switch(event, limit, generation))

    # ── rendering ────────────────────────────────────────────────────────────────────────

    def _usage_switch_generation(self, session_key: str) -> Optional[int]:
        """The session's current run generation, when the runner exposes one."""
        try:
            state = self._session_state(session_key)
            return getattr(getattr(state, "persistent", None), "run_generation", None)
        except Exception:
            return None

    async def _offer_usage_limit_switch(self, event, limit: dict,
                                        generation: Optional[int] = None) -> None:
        """Offer the plan's recommended models as a picker, or one line of text."""
        adapter = self._delivery_adapter_for(event.source) if event is not None else None
        if adapter is None:
            return
        suggestions = [s for s in (limit.get("suggestions") or [])
                       if isinstance(s, dict) and s.get("model")]
        if not suggestions:
            await self._send_usage_limit_line(
                event, adapter, limit, "Pick another model with {cmd}.")
            return
        session_key = self._session_key_for_source(event.source)

        async def _on_choice(_chat_id: str, model: str) -> str:
            if generation is not None:
                current = self._usage_switch_generation(session_key)
                if current is not None and current != generation:
                    return ""  # a newer turn ran since the refusal: this offer is stale
            provider = next((s.get("provider") or _PLAN_PROVIDER for s in suggestions
                             if s.get("model") == model), _PLAN_PROVIDER)
            return await self._accept_usage_limit_switch(event, model, provider=provider)

        if getattr(type(adapter), "send_choice_picker", None) is not None:
            choices = [{"value": s["model"],
                        "label": self._suggestion_label(s),
                        "is_current": False} for s in suggestions]
            try:
                sent = await self._try_send_choice_picker(
                    event, session_key, title="Keep going on another model:",
                    choices=choices, on_choice_selected=_on_choice)
            except Exception:
                logger.debug("usage-limit picker failed; falling back to text", exc_info=True)
                sent = False
            if sent:
                return
        await self._send_usage_limit_line(event, adapter, limit, None)

    @staticmethod
    def _suggestion_label(suggestion: dict) -> str:
        label = f"Switch to {suggestion.get('label') or suggestion['model']}"
        note = str(suggestion.get("note") or "").strip()
        return f"{label} ({note})" if note else label

    async def _send_usage_limit_line(self, event, adapter, limit: dict, template: Optional[str]) -> None:
        """Send the one-line text fallback."""
        from agent.plan_limits import limit_next_steps_from_payload
        prefix = "/"
        try:
            prefix = self._typed_command_prefix_for(event.source.platform) or "/"
        except Exception:
            pass
        if template is not None:
            text = template.format(cmd=f"{prefix}model")
        else:
            text = limit_next_steps_from_payload(
                limit, model_command=f"{prefix}model", topup_command=None, portal=True)
        try:
            await adapter.send(event.source.chat_id, text, metadata=self._reply_metadata(event))
        except Exception:
            logger.debug("usage-limit text send failed", exc_info=True)

    # ── accepting a tap ──────────────────────────────────────────────────────────────────

    async def _accept_usage_limit_switch(self, event, model: str,
                                         *, provider: Optional[str] = None) -> str:
        """Switch THIS session to *model* and resend the refused message when the switch held."""
        from gateway.slash_commands_model import _ModelSwitchContext

        source = event.source
        try:
            normalize = getattr(self, "_normalize_source_for_session_key", None)
            if normalize is not None:
                source = await asyncio.to_thread(normalize, event.source)
        except Exception:
            source = event.source
        session_key = self._session_key_for_source(source)
        overrides = getattr(self, "_session_model_overrides", None)
        if overrides is None:
            overrides = self._session_model_overrides = {}
        ctx = _ModelSwitchContext(
            session_key=session_key, source=source,
            config_path=getattr(self, "config_path", None),
            persist_global=False,
        )
        ctx.read_config()
        ctx.apply_override(overrides.get(session_key, {}))
        # A newer turn beginning during the await must stop the /retry: it would rewind it.
        before_generation = self._usage_switch_generation(session_key)
        result, error = await self._perform_model_switch(
            ctx, model, provider or _PLAN_PROVIDER, source)
        if error is not None:
            return error
        reply = await self._commit_model_switch(result, ctx, source=source, picker=True)
        if session_key not in overrides:
            return reply  # the switch did not land; never resend under the old model
        after_generation = self._usage_switch_generation(session_key)
        if (before_generation is not None and after_generation is not None
                and after_generation != before_generation):
            return reply
        adapter = self._delivery_adapter_for(source)

        async def _resend() -> None:
            if adapter is None:
                return
            try:
                from gateway.platforms.event import MessageEvent, MessageType
                await adapter.handle_message(
                    MessageEvent(text="/retry", message_type=MessageType.TEXT, source=source))
            except Exception:
                logger.debug("usage-limit /retry resend failed", exc_info=True)

        self._track_background_task(_resend())
        return reply
