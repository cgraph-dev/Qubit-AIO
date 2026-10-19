"""WhatsApp Cloud is a core built-in, so its "sufficiently configured?" verdict comes from
`_PLATFORM_CONNECTED_CHECKERS`, not from a plugin PlatformEntry.

Without the `_needs_extra("phone_number_id", "access_token")` row, a YAML-configured Cloud bot
(credentials in `platforms.whatsapp_cloud.extra`, no `token`/`api_key`) read as not connected and
vanished from `get_connected_platforms()` — the prompt's "Connected Platforms" block and
`qubit gateway status` both dropped it. Upstream's checker dict (pin 43faacf) carries the row.
"""

from gateway.config import GatewayConfig, Platform, PlatformConfig


def _gateway(**platforms):
    return GatewayConfig(platforms={
        p: (cfg if isinstance(cfg, PlatformConfig) else PlatformConfig(enabled=True, **cfg))
        for p, cfg in platforms.items()
    })


def test_whatsapp_cloud_is_connected_from_extra_credentials():
    gw = _gateway(whatsapp_cloud={"extra": {"phone_number_id": "123", "access_token": "tok"}})
    assert Platform.WHATSAPP_CLOUD in gw.get_connected_platforms()


def test_whatsapp_cloud_needs_both_extra_keys():
    for extra in ({"phone_number_id": "123"}, {"access_token": "tok"}, {}):
        gw = _gateway(whatsapp_cloud={"extra": extra})
        assert Platform.WHATSAPP_CLOUD not in gw.get_connected_platforms(), extra
