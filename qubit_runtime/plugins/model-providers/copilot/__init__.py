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

    def replays_function_thought_signature(self, *, model=None, base_url="") -> bool:
        return True

    def replay_message_carriers(self, values, *, model, base_url):
        return {k: v for k, v in values.items() if k in ("reasoning_opaque", "reasoning_text") and v}

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
)

register_provider(copilot)
