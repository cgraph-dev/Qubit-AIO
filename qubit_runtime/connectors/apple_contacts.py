"""Read-only Apple Contacts connector.

On macOS a caller injects a Contacts/``CNContactStore``-shaped adapter exposing
``list_contacts(limit=)`` and ``search_contacts(query=, limit=)``. The connector
maps rows to bounded :class:`AppleContact` records. It has no create, update,
delete or message path and never writes to Contacts.

On any other platform both operations raise ``not_supported`` with the host
platform and the required framework before the adapter is touched.

Field shape was informed by OpenJarvis ``0583901``
(``src/openjarvis/connectors/apple_contacts.py``, Apache-2.0); no upstream code
is copied. See ``connectors/APPLE_PROVENANCE.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from connectors.apple_common import (
    MAX_ID_CHARS,
    MAX_TEXT_CHARS,
    AppleConnectorError,
    REASON_INVALID_REQUEST,
    backend_rows,
    bounded_text,
    bounded_values,
    coerce_limit,
    current_platform,
    ensure_supported,
    map_backend_error,
    normalize_date,
    require_backend,
)

CONNECTOR_NAME = "Apple Contacts"
REQUIRED_FRAMEWORK = "Contacts (CNContactStore)"
SUPPORTED_PLATFORM = "darwin"

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_QUERY_CHARS = 256
MAX_EMAILS = 5
MAX_PHONES = 5


@dataclass(frozen=True)
class AppleContact:
    contact_id: str
    name: str
    first_name: str = ""
    last_name: str = ""
    organization: str = ""
    job_title: str = ""
    department: str = ""
    nickname: str = ""
    birthday: str = ""
    emails: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "contact_id": self.contact_id,
            "name": self.name,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "organization": self.organization,
            "job_title": self.job_title,
            "department": self.department,
            "nickname": self.nickname,
            "birthday": self.birthday,
            "emails": list(self.emails),
            "phones": list(self.phones),
        }


def _contact_from_row(row: dict[str, Any]) -> Optional[AppleContact]:
    first = bounded_text(row.get("first_name") or row.get("given_name"))
    middle = bounded_text(row.get("middle_name"))
    last = bounded_text(row.get("last_name") or row.get("family_name"))
    organization = bounded_text(
        row.get("organization") or row.get("company"), MAX_TEXT_CHARS)
    name = bounded_text(
        row.get("name") or row.get("display_name"), MAX_TEXT_CHARS)
    if not name:
        name = " ".join(part for part in (first, middle, last) if part)[
            :MAX_TEXT_CHARS]
    if not name:
        name = organization
    if not name and not organization:
        return None
    contact_id = bounded_text(
        row.get("contact_id")
        or row.get("identifier")
        or row.get("id")
        or row.get("unique_id"),
        MAX_ID_CHARS,
    )
    if not contact_id:
        contact_id = name[:MAX_ID_CHARS]
    return AppleContact(
        contact_id=contact_id,
        name=name,
        first_name=first,
        last_name=last,
        organization=organization,
        job_title=bounded_text(row.get("job_title") or row.get("title")),
        department=bounded_text(row.get("department")),
        nickname=bounded_text(row.get("nickname")),
        birthday=normalize_date(row.get("birthday")),
        emails=bounded_values(row.get("emails"), limit=MAX_EMAILS),
        phones=bounded_values(row.get("phones"), limit=MAX_PHONES),
    )


class AppleContactsConnector:
    """Narrow read-only Contacts surface over an injected framework adapter."""

    connector_id = "apple_contacts"

    def __init__(self, *, platform: Optional[str] = None, backend: Any = None) -> None:
        self._platform = platform or current_platform()
        self._backend = backend

    @property
    def platform(self) -> str:
        return self._platform

    def available(self) -> bool:
        return self._platform == SUPPORTED_PLATFORM and self._backend is not None

    def _rows(self, call: Any) -> list[AppleContact]:
        try:
            payload = call()
        except AppleConnectorError:
            raise
        except Exception as exc:
            raise map_backend_error(
                exc,
                connector=CONNECTOR_NAME,
                required_framework=REQUIRED_FRAMEWORK,
                platform=self._platform,
            ) from None
        rows = backend_rows(
            payload,
            connector=CONNECTOR_NAME,
            required_framework=REQUIRED_FRAMEWORK,
            platform=self._platform,
        )
        contacts: list[AppleContact] = []
        for row in rows:
            contact = _contact_from_row(row)
            if contact is not None:
                contacts.append(contact)
        return contacts

    def list_contacts(self, *, limit: object = DEFAULT_LIMIT) -> list[AppleContact]:
        ensure_supported(
            platform=self._platform,
            required_framework=REQUIRED_FRAMEWORK,
            connector=CONNECTOR_NAME,
        )
        bounded = coerce_limit(limit, default=DEFAULT_LIMIT, maximum=MAX_LIMIT)
        backend = require_backend(
            self._backend,
            platform=self._platform,
            required_framework=REQUIRED_FRAMEWORK,
            connector=CONNECTOR_NAME,
        )
        return self._rows(lambda: backend.list_contacts(limit=bounded))[:bounded]

    def search_contacts(
        self,
        query: object,
        *,
        limit: object = DEFAULT_LIMIT,
    ) -> list[AppleContact]:
        ensure_supported(
            platform=self._platform,
            required_framework=REQUIRED_FRAMEWORK,
            connector=CONNECTOR_NAME,
        )
        text = bounded_text(query, MAX_QUERY_CHARS)
        if not text:
            raise AppleConnectorError(
                REASON_INVALID_REQUEST,
                "query is required for Apple Contacts search",
                platform=self._platform,
                required_framework=REQUIRED_FRAMEWORK,
            )
        bounded = coerce_limit(limit, default=DEFAULT_LIMIT, maximum=MAX_LIMIT)
        backend = require_backend(
            self._backend,
            platform=self._platform,
            required_framework=REQUIRED_FRAMEWORK,
            connector=CONNECTOR_NAME,
        )
        return self._rows(
            lambda: backend.search_contacts(query=text, limit=bounded))[:bounded]
