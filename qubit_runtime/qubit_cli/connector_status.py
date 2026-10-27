"""Read-only connector readiness rows for ``qubit connectors status``.

The report answers one question per connector: can a read-only call be made
right now, and if not, why? It never opens a network connection, never starts
an OAuth flow, and never prints a token, an environment value, or file bytes.

Credential paths are resolved exactly like the connectors themselves resolve
them (``connector.token_path`` → ``qubit_constants.get_qubit_home()``), so a
profile-scoped ``QUBIT_HOME`` reports that profile's files.

Statuses are the closed set a caller may show verbatim:

- ``available``          the credential file is present and usable, or the
                         Apple connector's host platform is supported
- ``missing_credential`` the documented token file is absent
- ``not_supported``      the host platform has no Apple framework
- ``error``              the file exists but could not be used/checked
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, Optional

STATUS_AVAILABLE = "available"
STATUS_MISSING_CREDENTIAL = "missing_credential"
STATUS_NOT_SUPPORTED = "not_supported"
STATUS_ERROR = "error"

STATUSES = (
    STATUS_AVAILABLE,
    STATUS_MISSING_CREDENTIAL,
    STATUS_NOT_SUPPORTED,
    STATUS_ERROR,
)


@dataclass(frozen=True)
class ConnectorStatus:
    """One readiness row. ``token_path`` is a path, never file contents."""

    connector: str
    label: str
    status: str
    hint: str
    token_path: str = ""
    platform: str = ""
    required_framework: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "connector": self.connector,
            "label": self.label,
            "status": self.status,
            "hint": self.hint,
            "token_path": self.token_path,
            "platform": self.platform,
            "required_framework": self.required_framework,
        }


def _credential_row(
    *,
    connector: str,
    label: str,
    connector_cls: Any,
    available_hint: str,
    missing_hint: str,
    unusable_hint: str,
) -> ConnectorStatus:
    """Classify one token-file connector without ever reading the token text."""
    try:
        client = connector_cls()
        path = client.token_path
    except Exception as exc:  # noqa: BLE001 - a report must never crash
        return ConnectorStatus(
            connector=connector,
            label=label,
            status=STATUS_ERROR,
            hint=f"could not resolve the token path ({type(exc).__name__})",
        )

    path_text = str(path)
    try:
        present = path.exists()
    except OSError:
        present = False
    if not present:
        return ConnectorStatus(
            connector=connector,
            label=label,
            status=STATUS_MISSING_CREDENTIAL,
            hint=missing_hint,
            token_path=path_text,
        )

    try:
        configured = bool(client.configured())
    except Exception as exc:  # noqa: BLE001
        return ConnectorStatus(
            connector=connector,
            label=label,
            status=STATUS_ERROR,
            hint=f"the token file could not be checked ({type(exc).__name__})",
            token_path=path_text,
        )
    if configured:
        return ConnectorStatus(
            connector=connector,
            label=label,
            status=STATUS_AVAILABLE,
            hint=available_hint,
            token_path=path_text,
        )
    return ConnectorStatus(
        connector=connector,
        label=label,
        status=STATUS_ERROR,
        hint=unusable_hint,
        token_path=path_text,
    )


def _apple_row(
    *,
    connector: str,
    label: str,
    required_framework: str,
    platform: Optional[str],
) -> ConnectorStatus:
    """Apple connectors are macOS-only; no credential file exists."""
    from connectors import apple_common

    host = platform or apple_common.current_platform()
    if host != apple_common.SUPPORTED_PLATFORM:
        return ConnectorStatus(
            connector=connector,
            label=label,
            status=STATUS_NOT_SUPPORTED,
            hint=(
                f"requires macOS ({apple_common.SUPPORTED_PLATFORM}) with "
                f"{required_framework}; this host is {host}"
            ),
            platform=host,
            required_framework=required_framework,
        )
    return ConnectorStatus(
        connector=connector,
        label=label,
        status=STATUS_AVAILABLE,
        hint=(
            "macOS host; the caller must inject a read-only "
            f"{required_framework} adapter"
        ),
        platform=host,
        required_framework=required_framework,
    )


def collect_status(*, platform: Optional[str] = None) -> list[ConnectorStatus]:
    """Return one deterministic readiness row per read-only connector."""
    from connectors import apple_calendar, apple_contacts, apple_health, apple_music
    from connectors.dropbox import DropboxConnector
    from connectors.google_calendar import GoogleCalendarConnector
    from connectors.google_contacts import GoogleContactsConnector

    rows = [
        _credential_row(
            connector="google_contacts",
            label="Google Contacts",
            connector_cls=GoogleContactsConnector,
            available_hint="google_token.json is present with a credential",
            missing_hint=(
                "run the google-workspace setup to write "
                "$QUBIT_HOME/google_token.json"
            ),
            unusable_hint=(
                "google_token.json exists but has no usable credential; "
                "re-run the google-workspace setup"
            ),
        ),
        _credential_row(
            connector="google_calendar",
            label="Google Calendar",
            connector_cls=GoogleCalendarConnector,
            available_hint="google_token.json is present with a credential",
            missing_hint=(
                "run the google-workspace setup to write "
                "$QUBIT_HOME/google_token.json"
            ),
            unusable_hint=(
                "google_token.json exists but has no usable credential; "
                "re-run the google-workspace setup"
            ),
        ),
        _credential_row(
            connector="dropbox",
            label="Dropbox",
            connector_cls=DropboxConnector,
            available_hint="dropbox_token.json is present with an access token",
            missing_hint=(
                "write a Dropbox access token to $QUBIT_HOME/dropbox_token.json"
            ),
            unusable_hint=(
                "dropbox_token.json exists but has no access token; this "
                "connector does not perform OAuth exchange or refresh"
            ),
        ),
        _apple_row(
            connector="apple_contacts",
            label=apple_contacts.CONNECTOR_NAME,
            required_framework=apple_contacts.REQUIRED_FRAMEWORK,
            platform=platform,
        ),
        _apple_row(
            connector="apple_calendar",
            label=apple_calendar.CONNECTOR_NAME,
            required_framework=apple_calendar.REQUIRED_FRAMEWORK,
            platform=platform,
        ),
        _apple_row(
            connector="apple_health",
            label=apple_health.CONNECTOR_NAME,
            required_framework=apple_health.REQUIRED_FRAMEWORK,
            platform=platform,
        ),
        _apple_row(
            connector="apple_music",
            label=apple_music.CONNECTOR_NAME,
            required_framework=apple_music.REQUIRED_FRAMEWORK,
            platform=platform,
        ),
    ]
    return rows


def render_text(rows: list[ConnectorStatus]) -> str:
    """Human-readable table: one line per connector, no secrets."""
    if not rows:
        return "No read-only connectors are declared."
    id_width = max(len(row.connector) for row in rows)
    status_width = max(len(row.status) for row in rows)
    lines: list[str] = []
    for row in rows:
        parts = [row.connector.ljust(id_width), row.status.ljust(status_width), row.label]
        if row.token_path:
            parts.append(row.token_path)
        if row.platform:
            parts.append(f"platform={row.platform}")
        if row.required_framework:
            parts.append(f"framework={row.required_framework}")
        parts.append(row.hint)
        lines.append("  ".join(parts))
    return "\n".join(lines)


def render_json(rows: list[ConnectorStatus]) -> str:
    """Stable JSON document for scripts; contains paths and reasons only."""
    return json.dumps(
        {"connectors": [row.as_dict() for row in rows]},
        indent=2,
        sort_keys=True,
    )


def detect_platform() -> str:
    """The host platform the Apple rows should reason about."""
    return sys.platform
