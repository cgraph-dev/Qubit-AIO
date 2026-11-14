"""Representative CRM adapter — official API only, dry-run by default.

A CRM is a system of record, so writes are idempotent and auditable. Upserts
and activity logging need no approval; creating a lead is a targeting change
and requires an approval token. No network client is created here.
"""
from __future__ import annotations

from typing import Any, Mapping

from .base import (
    ADAPTER_BAD_INPUT_CODE,
    AdapterError,
    AdapterResult,
    ApprovalToken,
    BaseAdapter,
    Transport,
)

APPROVAL_CREATE_LEAD = "crm.lead.create"


class CrmAdapter(BaseAdapter):
    platform_id = "crm"
    credential_keys = ("api_key",)
    default_rate_limit = 60
    default_rate_window_seconds = 60.0

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        credentials: Mapping[str, Any] | None = None,
        dry_run: bool = True,
        rate_limit: int | None = None,
        rate_window_seconds: float | None = None,
        clock: Any = None,
    ) -> None:
        super().__init__(
            transport=transport,
            credentials=credentials,
            dry_run=dry_run,
            rate_limit=rate_limit,
            rate_window_seconds=rate_window_seconds,
            clock=clock,
        )

    def get_contact(self, external_id: str) -> AdapterResult:
        target = str(external_id or "").strip()
        if not target:
            raise AdapterError(
                ADAPTER_BAD_INPUT_CODE,
                "CRM: external_id is required",
                platform=self.platform_id,
                operation="contact.get",
            )
        return self._read(
            "contact.get", path=f"/crm/contacts/{target}", policy_action="read"
        )

    def upsert_contact(
        self, contact: Mapping[str, Any], *, idempotency_key: str
    ) -> AdapterResult:
        payload = dict(contact)
        if not str(payload.get("external_id") or "").strip():
            raise AdapterError(
                ADAPTER_BAD_INPUT_CODE,
                "CRM: contact.external_id is required",
                platform=self.platform_id,
                operation="contact.upsert",
            )
        return self._mutate(
            "contact.upsert",
            payload,
            idempotency_key=idempotency_key,
            path="/crm/contacts",
            method="POST",
            policy_action="upsert",
        )

    def log_activity(
        self, activity: Mapping[str, Any], *, idempotency_key: str
    ) -> AdapterResult:
        payload = dict(activity)
        if not str(payload.get("contact_id") or "").strip():
            raise AdapterError(
                ADAPTER_BAD_INPUT_CODE,
                "CRM: activity.contact_id is required",
                platform=self.platform_id,
                operation="activity.log",
            )
        return self._mutate(
            "activity.log",
            payload,
            idempotency_key=idempotency_key,
            path="/crm/activities",
            method="POST",
            policy_action="write",
        )

    def create_lead(
        self,
        lead: Mapping[str, Any],
        *,
        idempotency_key: str,
        approval: ApprovalToken | None = None,
    ) -> AdapterResult:
        payload = dict(lead)
        if not str(payload.get("name") or "").strip():
            raise AdapterError(
                ADAPTER_BAD_INPUT_CODE,
                "CRM: lead.name is required",
                platform=self.platform_id,
                operation="lead.create",
            )
        return self._mutate(
            "lead.create",
            payload,
            idempotency_key=idempotency_key,
            path="/crm/leads",
            method="POST",
            policy_action="targeting_changes",
            approval_action=APPROVAL_CREATE_LEAD,
            approval=approval,
        )


__all__ = ["APPROVAL_CREATE_LEAD", "CrmAdapter"]
