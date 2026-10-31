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

    def replay_message_carriers(self, values: dict[str, Any], *, model: str | None, base_url: str) -> dict[str, Any]:
        """Copilot's Gemini 3 messages carry an opaque reasoning blob and its readable text."""
        return {key: values[key] for key in (self.message_carrier_opaque, self.message_carrier_text) if key in values}

    def replays_function_thought_signature(self, *, model: str | None, base_url: str) -> bool:
        """Copilot's Gemini 3 variant signs the tool call inside ``function``."""
        return str(model or "").lower().startswith("gemini")

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
    hostnames=("api.githubcopilot.com", "models.github.ai"),
    message_carrier_opaque="reasoning_opaque", message_carrier_text="reasoning_text",
    auth_type="copilot",
)

register_provider(copilot)
