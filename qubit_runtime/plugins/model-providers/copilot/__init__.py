"""Copilot / GitHub Models provider profile.

Core routes GPT-5+/Codex -> codex_responses and Claude -> anthropic_messages;
this profile covers the chat_completions remainder: editor attribution headers
(copilot_default_headers()) and catalog-gated GitHub Models reasoning.
"""

from typing import Any

from providers import register_provider
from providers.base import ProviderProfile


class CopilotProfile(ProviderProfile):
    """GitHub Copilot / GitHub Models — editor headers + reasoning."""

    # Copilot's Responses/chat wires return top-level ``reasoning_text`` / ``reasoning_opaque``
    # carriers (its Gemini route also signs tool calls). Capture and replay them only here.
    message_carrier_opaque = "reasoning_opaque"
    message_carrier_text = "reasoning_text"
    replays_tool_call_signatures = True

    def replay_message_carriers(self, values: dict[str, Any], *, model: str | None, base_url: str) -> dict[str, Any]:
        """Copilot's Gemini 3 messages carry an opaque reasoning blob and its readable text."""
        return {key: values[key] for key in (self.message_carrier_opaque, self.message_carrier_text) if key in values}

    def replays_function_thought_signature(self, *, model: str | None, base_url: str) -> bool:
        """Copilot's Gemini 3 variant signs the tool call inside ``function``."""
        return str(model or "").lower().startswith("gemini")

    def client_headers(
        self, *, base_url: str, api_key: str = "", has_images: bool = False,
    ) -> dict[str, str] | None:
        """Editor/initiator headers (and the vision-only header for image clients) on the Copilot host."""
        from utils import base_url_host_matches

        if not base_url_host_matches(str(base_url or ""), "githubcopilot.com"):
            return None
        from qubit_cli.copilot_auth import copilot_request_headers

        return copilot_request_headers(is_agent_turn=True, is_vision=has_images)

    def build_api_kwargs_extras(
        self, *, model: str | None = None, reasoning_config: dict | None = None,
        supports_reasoning: bool = False, **ctx,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not (supports_reasoning and model):
            return {}, {}
        try:
            from qubit_cli.models import clamp_github_reasoning_effort, github_model_reasoning_efforts

            supported = github_model_reasoning_efforts(model)
            if not supported:
                return {}, {}
            if not reasoning_config:
                return {"reasoning": {"effort": "medium"}}, {}
            # Never drop straight to medium, which inverted the ladder (ultra < high). See #74295.
            effort = clamp_github_reasoning_effort(reasoning_config.get("effort"), supported)
            return {"reasoning": {"effort": effort}}, {}
        except Exception:
            return {}, {}


copilot = CopilotProfile(
    name="copilot", aliases=("github-copilot", "github-models", "github-model", "github"),
    env_vars=("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"), base_url="https://api.githubcopilot.com",
    auth_type="copilot",
    hostnames=("api.githubcopilot.com", "githubcopilot.com", "models.github.ai"),
    # Dataclass fields: class-level overrides are shadowed by the generated __init__, so set them here.
    message_carrier_opaque="reasoning_opaque", message_carrier_text="reasoning_text",
    replays_tool_call_signatures=True,
    # Copilot API host family (individual, business, enterprise) plus GitHub Models.
    metadata_hostnames=("models.inference.ai.azure.com",),
    # The backend binds replayed Responses item ids to a connection: a stale id 401s even when short
    # (#32716). It also has no prompt cache key and no server-side compaction.
    responses_replay_item_ids=False,
    responses_prompt_cache_key=False,
    responses_issuer_kind="github_responses",
    responses_trusts_completed_status=False,
    responses_native_compaction=False,
)

register_provider(copilot)
