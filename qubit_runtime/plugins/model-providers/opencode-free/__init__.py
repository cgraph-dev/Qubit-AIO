"""OpenCode Free provider profile: the free tier on the Zen relay.

KEYLESS: the relay serves free-tier models anonymously and rejects any bearer it
does not recognize, so this provider never sends a credential. Select it through
the free/opencode-free alias.
"""

from typing import Any

from qubit_cli.version_info import get_version_info
from providers import register_provider
from providers.base import ProviderProfile


class OpenCodeFreeProfile(ProviderProfile):
    """OpenCode Free — keyless, with Ox Alpha reasoning controls.

    Ox Alpha is also reachable through opencode-zen with the same wire contract;
    the translation lives in the zen plugin and is resolved through the
    registered zen profile's module so the two providers cannot drift.
    """

    def build_api_kwargs_extras(
        self, *, reasoning_config: dict | None = None, model: str | None = None, **context
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            import sys

            from providers import get_provider_profile

            zen_module = sys.modules[type(get_provider_profile("opencode-zen")).__module__]
            return zen_module._build_ox_alpha_reasoning_extras(reasoning_config, model)
        except Exception:
            return {}, {}


opencode_free = OpenCodeFreeProfile(
    name="opencode-free", aliases=("free", "opencode_free"),
    env_vars=(),  # keyless — nothing to configure
    base_url="https://opencode.ai/zen/v1", display_name="OpenCode Free",
    description="OpenCode free models — keyless, no account needed",
    default_headers={
        "Authorization": "",
        "HTTP-Referer": "https://github.com/cgraph-dev/Qubit-AIO",
        "X-Title": "Qubit Agent",
        "User-Agent": f"QubitAgent/{get_version_info().base_version}",
    },
    default_aux_model="laguna-s-2.1-free",
)

register_provider(opencode_free)
