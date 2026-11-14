"""P5-08 — multi-entity-ready books and the business factory.

The factory creates *structure*, never inherited power. Owner, tenant, legal
entity, business and brand are separate records:

* **Owner** is an ``owner`` principal in the tenant identity registry.
* **Tenant** is the durable scope every record carries.
* **Legal entity** is a book-owning record (personal first, then companies).
* **Business** is an operating unit bound to exactly one legal entity.
* **Brand** is presentation identity, recorded separately and never an
  authority.

Cloning a business or a template copies structural metadata only. Grants,
principals, roles, budgets, bank details, credentials, agents, session tokens
and treasury/ledger rows are **never** copied implicitly. Asking for any of
them is a typed refusal, not a best effort.

No new database: legal-entity and brand records are event-sourced in
``bos_event``; businesses are ordinary ``bos_business`` rows; every entity has
its own ledger namespace on the existing ledger table. Personal books are
created first so a company can never appear without a personal baseline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from . import eventlog, finance, identity
from .errors import BAD_INPUT_CODE, NOT_FOUND_CODE, BusinessError
from .ids import fingerprint, new_id
from .util import json_loads, now_iso, redact, require_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .store import BusinessStore

FACTORY_INHERIT_REFUSED_CODE = "BUSINESS_FACTORY_INHERIT_REFUSED"
FACTORY_PERSONAL_FIRST_CODE = "BUSINESS_FACTORY_PERSONAL_FIRST_REQUIRED"
FACTORY_ENTITY_MISMATCH_CODE = "BUSINESS_FACTORY_ENTITY_MISMATCH"
FACTORY_TENANT_MISMATCH_CODE = "BUSINESS_FACTORY_TENANT_MISMATCH"
FACTORY_BAD_TEMPLATE_CODE = "BUSINESS_FACTORY_BAD_TEMPLATE"

ENTITY_KINDS = ("personal", "company")

#: Structural metadata a clone may copy. Everything else is either derived
#: (legal entity, namespace) or authority-shaped and therefore refused.
STRUCTURAL_KEYS = (
    "category",
    "industry",
    "description",
    "locale",
    "timezone",
    "tags",
    "template",
)

#: Keys that mean "give the clone the source's power or money". Their presence
#: in template/metadata is an implicit inheritance request and is refused.
FORBIDDEN_KEYS = frozenset(
    {
        "authority",
        "authority_grants",
        "grants",
        "roles",
        "principals",
        "budget",
        "budgets",
        "bank",
        "bank_account",
        "bank_accounts",
        "payout",
        "payouts",
        "payout_account",
        "credentials",
        "credential",
        "password",
        "passwords",
        "client_secret",
        "private_key",
        "private_keys",
        "secrets",
        "secret",
        "tokens",
        "token",
        "access_token",
        "refresh_token",
        "api_keys",
        "api_key",
        "auth",
        "oauth",
        "agents",
        "treasury",
        "treasury_account",
        "wallet",
        "wallets",
        "sessions",
        "auth_sessions",
        "leases",
        "policies",
        "inherit_authority",
        "inherit_budget",
        "inherit_credentials",
        "inherit_agents",
        "inherit_treasury",
        "inherit_bank",
    }
)

_NOT_COPIED: tuple[dict[str, str], ...] = (
    {
        "class": "authority",
        "reason": "cloning never inherits grants, roles or principals",
    },
    {
        "class": "budget",
        "reason": "cloning never inherits a budget",
    },
    {
        "class": "bank",
        "reason": "cloning never inherits bank or payout details",
    },
    {
        "class": "credentials",
        "reason": "cloning never inherits credentials or secrets",
    },
    {
        "class": "agents",
        "reason": "cloning never inherits agents or their sessions",
    },
    {
        "class": "treasury",
        "reason": "cloning never inherits treasury or ledger history",
    },
    {
        "class": "artifacts",
        "reason": "cloning never inherits receipts, artifacts or jobs",
    },
)


def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    try:
        return require_text(value, label, max_len=max_len)
    except ValueError as exc:
        raise BusinessError(BAD_INPUT_CODE, str(exc)) from exc


def _currency(value: Any) -> str:
    clean = _text(value, "currency", max_len=8).upper()
    if len(clean) != 3 or not clean.isalpha():
        raise BusinessError(BAD_INPUT_CODE, "currency must be a 3-letter code")
    return clean


def _key(prefix: str, *parts: Any) -> str:
    return prefix + ":" + fingerprint([str(part) for part in parts])[:48]


def _walk_forbidden(value: Any, path: str = "") -> str | None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).strip().lower()
            here = f"{path}.{key}" if path else key
            if key in FORBIDDEN_KEYS:
                return here
            found = _walk_forbidden(item, here)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found = _walk_forbidden(item, f"{path}[{index}]")
            if found is not None:
                return found
    return None


def _safe_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    if metadata is None:
        return {}
    if not isinstance(metadata, Mapping):
        raise BusinessError(BAD_INPUT_CODE, "metadata must be a mapping")
    body = dict(metadata)
    forbidden = _walk_forbidden(body)
    if forbidden is not None:
        raise BusinessError(
            FACTORY_INHERIT_REFUSED_CODE,
            f"metadata key {forbidden!r} requests inherited authority, money or"
            " credentials; a clone or a new business never receives those"
            " implicitly",
        )
    return redact(body)


@dataclass(frozen=True, slots=True)
class LegalEntity:
    legal_entity_id: str
    tenant_id: str
    name: str
    kind: str
    jurisdiction: str | None
    currency: str
    registration_id: str | None
    tax_id: str | None
    created_at: str
    metadata: dict[str, Any]

    @property
    def is_personal(self) -> bool:
        return self.kind == "personal"

    def as_dict(self) -> dict[str, Any]:
        return {
            "legal_entity_id": self.legal_entity_id,
            "tenant_id": self.tenant_id,
            "name": self.name,
            "kind": self.kind,
            "jurisdiction": self.jurisdiction,
            "currency": self.currency,
            "registration_id": self.registration_id,
            "tax_id": self.tax_id,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class Brand:
    brand_id: str
    tenant_id: str
    business_id: str
    name: str
    slug: str
    palette: dict[str, Any]
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "brand_id": self.brand_id,
            "tenant_id": self.tenant_id,
            "business_id": self.business_id,
            "name": self.name,
            "slug": self.slug,
            "palette": dict(self.palette),
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class FactoryResult:
    tenant_id: str
    legal_entity_id: str | None
    business_id: str
    brand_id: str | None
    created: dict[str, Any]
    not_copied: tuple[dict[str, str], ...]
    events: tuple[str, ...]
    source_business_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "legal_entity_id": self.legal_entity_id,
            "business_id": self.business_id,
            "brand_id": self.brand_id,
            "source_business_id": self.source_business_id,
            "created": dict(self.created),
            "not_copied": [dict(row) for row in self.not_copied],
            "events": list(self.events),
        }


def _append(
    store: "BusinessStore",
    tenant_id: str,
    aggregate_id: str,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    business_id: str,
    actor: str,
    key: str,
) -> Any:
    return eventlog.append_event(
        store,
        tenant_id,
        aggregate_id,
        event_type,
        dict(payload),
        actor=actor,
        idempotency_key=key,
        business_id=business_id,
    )


def _events(
    store: "BusinessStore", tenant_id: str, event_type: str
) -> list[dict[str, Any]]:
    with store.read() as conn:
        store._require_tenant(conn, tenant_id)
        rows = conn.execute(
            "SELECT * FROM bos_event WHERE tenant_id=? AND type=?"
            " ORDER BY occurred_at, seq",
            (tenant_id, event_type),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        payload = json_loads(row["payload_json"], {})
        if isinstance(payload, Mapping):
            result.append(dict(payload))
    return result


def list_legal_entities(
    store: "BusinessStore", tenant_id: str
) -> list[LegalEntity]:
    latest: dict[str, dict[str, Any]] = {}
    for payload in _events(store, tenant_id, "finance.legal_entity.created"):
        entity_id = str(payload.get("legal_entity_id") or "")
        if entity_id:
            latest[entity_id] = payload
    entities = [
        LegalEntity(
            legal_entity_id=str(payload.get("legal_entity_id") or ""),
            tenant_id=tenant_id,
            name=str(payload.get("name") or ""),
            kind=str(payload.get("kind") or ""),
            jurisdiction=payload.get("jurisdiction"),
            currency=str(payload.get("currency") or ""),
            registration_id=payload.get("registration_id"),
            tax_id=payload.get("tax_id"),
            created_at=str(payload.get("created_at") or ""),
            metadata=dict(payload.get("metadata") or {}),
        )
        for payload in latest.values()
    ]
    entities.sort(key=lambda item: (item.created_at, item.legal_entity_id))
    return entities


def get_legal_entity(
    store: "BusinessStore", tenant_id: str, legal_entity_id: str
) -> LegalEntity:
    for entity in list_legal_entities(store, tenant_id):
        if entity.legal_entity_id == legal_entity_id:
            return entity
    raise BusinessError(
        NOT_FOUND_CODE,
        f"no legal entity {legal_entity_id!r} in tenant {tenant_id!r}",
    )


def personal_entity(
    store: "BusinessStore", tenant_id: str
) -> LegalEntity | None:
    for entity in list_legal_entities(store, tenant_id):
        if entity.is_personal:
            return entity
    return None


def _require_personal(
    store: "BusinessStore", tenant_id: str
) -> LegalEntity:
    entity = personal_entity(store, tenant_id)
    if entity is None:
        raise BusinessError(
            FACTORY_PERSONAL_FIRST_CODE,
            "personal books must exist before any company entity or business;"
            " use create_personal_books first",
        )
    return entity


def _anchor_business(
    store: "BusinessStore", tenant_id: str
) -> str:
    businesses = store.list_businesses(tenant_id)
    if not businesses:
        raise BusinessError(
            FACTORY_PERSONAL_FIRST_CODE,
            "a tenant needs a personal business before it can record an entity;"
            " use create_personal_books first",
        )
    return businesses[0].business_id


def create_personal_books(
    store: "BusinessStore",
    *,
    owner_name: str,
    tenant_name: str | None = None,
    currency: str = "USD",
    jurisdiction: str | None = None,
    owner_external_ref: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    actor: str = "system",
) -> FactoryResult:
    """Create a tenant with personal books, an owner and a personal brand."""
    owner = _text(owner_name, "owner name", max_len=200)
    name = _text(tenant_name or f"{owner} (Personal)", "tenant name", max_len=200)
    code = _currency(currency)
    safe = _safe_metadata(metadata)
    tenant = store.create_tenant(name)
    principal = identity.create_principal(
        store,
        tenant.tenant_id,
        kind="owner",
        external_ref=owner_external_ref or owner,
        actor=actor,
    )
    entity_id = "ent_" + fingerprint(
        {"tenant_id": tenant.tenant_id, "kind": "personal"}
    )[:26].lower()
    business = store.create_business(
        tenant.tenant_id,
        f"{owner} Personal",
        slug="personal",
        currency=code,
        metadata={
            **safe,
            "kind": "personal",
            "legal_entity_id": entity_id,
            "book_namespace": f"entity:{entity_id}",
        },
    )
    entity_payload = {
        "legal_entity_id": entity_id,
        "tenant_id": tenant.tenant_id,
        "name": owner,
        "kind": "personal",
        "jurisdiction": jurisdiction,
        "currency": code,
        "registration_id": None,
        "tax_id": None,
        "metadata": safe,
        "created_at": business.created_at,
    }
    entity_event = _append(
        store,
        tenant.tenant_id,
        f"entity:{entity_id}",
        "finance.legal_entity.created",
        entity_payload,
        business_id=business.business_id,
        actor=actor,
        key=_key("factory.entity.create", tenant.tenant_id, entity_id),
    )
    brand = create_brand(
        store,
        tenant.tenant_id,
        business.business_id,
        name=f"{owner} Personal",
        slug="personal",
        actor=actor,
    )
    business_event = _append(
        store,
        tenant.tenant_id,
        f"business:{business.business_id}",
        "factory.business.created",
        {
            "business_id": business.business_id,
            "tenant_id": tenant.tenant_id,
            "legal_entity_id": entity_id,
            "brand_id": brand.brand_id,
            "kind": "personal",
            "template": None,
        },
        business_id=business.business_id,
        actor=actor,
        key=_key("factory.business.create", tenant.tenant_id, business.business_id),
    )
    store.audit(
        tenant.tenant_id,
        actor=actor,
        action="factory.personal.create",
        resource_type="business",
        resource_id=business.business_id,
        business_id=business.business_id,
        detail={"owner": owner, "legal_entity_id": entity_id},
    )
    return FactoryResult(
        tenant_id=tenant.tenant_id,
        legal_entity_id=entity_id,
        business_id=business.business_id,
        brand_id=brand.brand_id,
        created={
            "tenant": tenant.tenant_id,
            "owner_principal": principal.principal_id,
            "legal_entity": entity_id,
            "business": business.business_id,
            "brand": brand.brand_id,
            "book_namespace": f"entity:{entity_id}",
        },
        not_copied=(),
        events=(entity_event.event_id, business_event.event_id),
    )


def create_legal_entity(
    store: "BusinessStore",
    tenant_id: str,
    *,
    name: str,
    kind: str = "company",
    jurisdiction: str | None = None,
    currency: str = "USD",
    registration_id: str | None = None,
    tax_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    actor: str = "system",
) -> LegalEntity:
    """Create a company (or idempotently return the personal) entity."""
    clean_kind = _text(kind, "entity kind", max_len=20).lower()
    if clean_kind not in ENTITY_KINDS:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"entity kind must be one of {', '.join(ENTITY_KINDS)}",
        )
    label = _text(name, "legal entity name", max_len=200)
    code = _currency(currency)
    safe = _safe_metadata(metadata)
    store.require_tenant(tenant_id)
    personal = _require_personal(store, tenant_id)
    if clean_kind == "personal":
        return personal
    entity_id = new_id("ent")
    anchor = _anchor_business(store, tenant_id)
    payload = {
        "legal_entity_id": entity_id,
        "tenant_id": tenant_id,
        "name": label,
        "kind": "company",
        "jurisdiction": jurisdiction,
        "currency": code,
        "registration_id": registration_id,
        "tax_id": tax_id,
        "metadata": safe,
        "created_at": now_iso(),
    }
    event = _append(
        store,
        tenant_id,
        f"entity:{entity_id}",
        "finance.legal_entity.created",
        payload,
        business_id=anchor,
        actor=actor,
        key=_key("factory.entity.create", tenant_id, entity_id),
    )
    payload["created_at"] = event.occurred_at
    store.audit(
        tenant_id,
        actor=actor,
        action="factory.entity.create",
        resource_type="legal_entity",
        resource_id=entity_id,
        business_id=anchor,
        detail={"name": label, "kind": "company"},
    )
    return LegalEntity(
        legal_entity_id=entity_id,
        tenant_id=tenant_id,
        name=label,
        kind="company",
        jurisdiction=jurisdiction,
        currency=code,
        registration_id=registration_id,
        tax_id=tax_id,
        created_at=event.occurred_at,
        metadata=safe,
    )


def create_brand(
    store: "BusinessStore",
    tenant_id: str,
    business_id: str,
    *,
    name: str,
    slug: str | None = None,
    palette: Mapping[str, Any] | None = None,
    actor: str = "system",
) -> Brand:
    """Create (or replay) the one brand record for a business."""
    store.require_business(tenant_id, business_id)
    label = _text(name, "brand name", max_len=200)
    raw_slug = _text(slug or label, "brand slug", max_len=64).lower()
    clean_slug = "-".join(
        part for part in "".join(
            char if (char.isalnum() or char == "-") else "-"
            for char in raw_slug
        ).split("-") if part
    )[:64]
    if not clean_slug:
        raise BusinessError(BAD_INPUT_CODE, "a brand needs a slug")
    brand_id = "brd_" + fingerprint(
        {"tenant_id": tenant_id, "business_id": business_id}
    )[:26].lower()
    payload = {
        "brand_id": brand_id,
        "tenant_id": tenant_id,
        "business_id": business_id,
        "name": label,
        "slug": clean_slug,
        "palette": redact(dict(palette or {})),
    }
    event = _append(
        store,
        tenant_id,
        f"brand:{brand_id}",
        "factory.brand.created",
        payload,
        business_id=business_id,
        actor=actor,
        key=_key("factory.brand.create", tenant_id, business_id),
    )
    store.audit(
        tenant_id,
        actor=actor,
        action="factory.brand.create",
        resource_type="brand",
        resource_id=brand_id,
        business_id=business_id,
        detail={"name": label, "slug": clean_slug},
    )
    return Brand(
        brand_id=brand_id,
        tenant_id=tenant_id,
        business_id=business_id,
        name=label,
        slug=clean_slug,
        palette=dict(payload["palette"]),
        created_at=event.occurred_at,
    )


def brand_for_business(
    store: "BusinessStore", tenant_id: str, business_id: str
) -> Brand | None:
    latest: dict[str, Any] | None = None
    for payload in _events(store, tenant_id, "factory.brand.created"):
        if str(payload.get("business_id") or "") == business_id:
            latest = payload
    if latest is None:
        return None
    return Brand(
        brand_id=str(latest.get("brand_id") or ""),
        tenant_id=tenant_id,
        business_id=business_id,
        name=str(latest.get("name") or ""),
        slug=str(latest.get("slug") or ""),
        palette=dict(latest.get("palette") or {}),
        created_at="",
    )


def _structural(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        return {}
    return {
        key: redact(value)
        for key, value in metadata.items()
        if key in STRUCTURAL_KEYS
    }


def create_business(
    store: "BusinessStore",
    tenant_id: str,
    *,
    name: str,
    slug: str | None = None,
    currency: str = "USD",
    legal_entity_id: str | None = None,
    brand: Mapping[str, Any] | None = None,
    template: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    actor: str = "system",
) -> FactoryResult:
    """Create a business under an entity; personal books are required first."""
    label = _text(name, "business name", max_len=200)
    code = _currency(currency)
    store.require_tenant(tenant_id)
    personal = _require_personal(store, tenant_id)
    entity_id = legal_entity_id or personal.legal_entity_id
    entity = get_legal_entity(store, tenant_id, entity_id)
    if entity.tenant_id != tenant_id:
        raise BusinessError(
            FACTORY_TENANT_MISMATCH_CODE,
            "the legal entity belongs to a different tenant",
        )
    safe = _safe_metadata(metadata)
    template_body: Mapping[str, Any] | None = None
    if template is not None:
        if not isinstance(template, Mapping):
            raise BusinessError(FACTORY_BAD_TEMPLATE_CODE, "template must be a mapping")
        forbidden = _walk_forbidden(template)
        if forbidden is not None:
            raise BusinessError(
                FACTORY_INHERIT_REFUSED_CODE,
                f"template key {forbidden!r} would inherit authority, money or"
                " credentials; structure only may be cloned",
            )
        template_body = dict(template)
        safe.update(_structural(template_body))
    namespace = f"entity:{entity.legal_entity_id}"
    business = store.create_business(
        tenant_id,
        label,
        slug=slug,
        currency=code,
        metadata={
            **safe,
            "legal_entity_id": entity.legal_entity_id,
            "book_namespace": namespace,
        },
    )
    brand_spec = dict(brand or {})
    new_brand = create_brand(
        store,
        tenant_id,
        business.business_id,
        name=str(brand_spec.get("name") or label),
        slug=brand_spec.get("slug") or label,
        palette=brand_spec.get("palette"),
        actor=actor,
    )
    event = _append(
        store,
        tenant_id,
        f"business:{business.business_id}",
        "factory.business.created",
        {
            "business_id": business.business_id,
            "tenant_id": tenant_id,
            "legal_entity_id": entity.legal_entity_id,
            "brand_id": new_brand.brand_id,
            "kind": "company" if not entity.is_personal else "personal",
            "template": (
                str(template_body.get("template") or template_body.get("name") or "")
                if template_body
                else None
            ),
        },
        business_id=business.business_id,
        actor=actor,
        key=_key("factory.business.create", tenant_id, business.business_id),
    )
    store.audit(
        tenant_id,
        actor=actor,
        action="factory.business.create",
        resource_type="business",
        resource_id=business.business_id,
        business_id=business.business_id,
        detail={"name": label, "legal_entity_id": entity.legal_entity_id},
    )
    return FactoryResult(
        tenant_id=tenant_id,
        legal_entity_id=entity.legal_entity_id,
        business_id=business.business_id,
        brand_id=new_brand.brand_id,
        created={
            "legal_entity": entity.legal_entity_id,
            "business": business.business_id,
            "brand": new_brand.brand_id,
            "book_namespace": namespace,
        },
        not_copied=(),
        events=(event.event_id,),
    )


def create_from_template(
    store: "BusinessStore",
    tenant_id: str,
    template: Mapping[str, Any],
    *,
    name: str,
    slug: str | None = None,
    currency: str | None = None,
    legal_entity_id: str | None = None,
    brand: Mapping[str, Any] | None = None,
    actor: str = "system",
) -> FactoryResult:
    """Create a business from a structural template; never from its power."""
    if not isinstance(template, Mapping):
        raise BusinessError(FACTORY_BAD_TEMPLATE_CODE, "template must be a mapping")
    forbidden = _walk_forbidden(template)
    if forbidden is not None:
        raise BusinessError(
            FACTORY_INHERIT_REFUSED_CODE,
            f"template key {forbidden!r} requests inherited authority, money or"
            " credentials; templates carry structure only",
        )
    code = currency or str(template.get("currency") or "USD")
    return create_business(
        store,
        tenant_id,
        name=name,
        slug=slug,
        currency=code,
        legal_entity_id=legal_entity_id,
        brand=brand,
        template=template,
        metadata=_structural(template),
        actor=actor,
    )


def _refuse_inheritance(
    *,
    inherit_authority: bool,
    inherit_budget: bool,
    inherit_credentials: bool,
    inherit_agents: bool,
    inherit_treasury: bool,
    inherit_bank: bool,
) -> None:
    requested = [
        name
        for name, flag in (
            ("authority", inherit_authority),
            ("budget", inherit_budget),
            ("credentials", inherit_credentials),
            ("agents", inherit_agents),
            ("treasury", inherit_treasury),
            ("bank", inherit_bank),
        )
        if flag
    ]
    if requested:
        raise BusinessError(
            FACTORY_INHERIT_REFUSED_CODE,
            "cloning never inherits: "
            + ", ".join(requested)
            + "; create explicit grants, budgets or credentials on the clone",
        )


def clone_business(
    store: "BusinessStore",
    tenant_id: str,
    source_business_id: str,
    *,
    name: str,
    slug: str | None = None,
    target_tenant_id: str | None = None,
    target_legal_entity_id: str | None = None,
    brand: Mapping[str, Any] | None = None,
    inherit_authority: bool = False,
    inherit_budget: bool = False,
    inherit_credentials: bool = False,
    inherit_agents: bool = False,
    inherit_treasury: bool = False,
    inherit_bank: bool = False,
    actor: str = "system",
) -> FactoryResult:
    """Clone a business's structure into a fresh business under an entity.

    Authority, budget, bank, credentials, agents, treasury, receipts and jobs
    are never copied. Requesting any of them is a typed refusal.
    """
    _refuse_inheritance(
        inherit_authority=inherit_authority,
        inherit_budget=inherit_budget,
        inherit_credentials=inherit_credentials,
        inherit_agents=inherit_agents,
        inherit_treasury=inherit_treasury,
        inherit_bank=inherit_bank,
    )
    source = store.get_business(tenant_id, source_business_id)
    target_tenant = target_tenant_id or tenant_id
    if target_tenant != tenant_id:
        store.require_tenant(target_tenant)
    source_metadata = source.metadata if isinstance(source.metadata, Mapping) else {}
    source_entity = str(source_metadata.get("legal_entity_id") or "")
    if target_legal_entity_id is not None:
        entity_id = target_legal_entity_id
    elif target_tenant == tenant_id and source_entity:
        entity_id = source_entity
    else:
        entity_id = _require_personal(store, target_tenant).legal_entity_id
    get_legal_entity(store, target_tenant, entity_id)
    source_brand = brand_for_business(store, tenant_id, source_business_id)
    brand_spec = dict(brand or {})
    if source_brand is not None:
        brand_spec.setdefault("name", source_brand.name)
        brand_spec.setdefault("palette", source_brand.palette)
    result = create_business(
        store,
        target_tenant,
        name=name,
        slug=slug,
        currency=source.currency,
        legal_entity_id=entity_id,
        brand=brand_spec,
        metadata=_structural(source_metadata),
        template={
            "template": str(source_metadata.get("template") or "")
            or source.name,
        },
        actor=actor,
    )
    event = _append(
        store,
        target_tenant,
        f"business:{result.business_id}",
        "factory.business.cloned",
        {
            "source_business_id": source_business_id,
            "source_tenant_id": tenant_id,
            "business_id": result.business_id,
            "tenant_id": target_tenant,
            "legal_entity_id": result.legal_entity_id,
            "not_copied": [row["class"] for row in _NOT_COPIED],
        },
        business_id=result.business_id,
        actor=actor,
        key=_key(
            "factory.business.clone",
            target_tenant,
            source_business_id,
            result.business_id,
        ),
    )
    store.audit(
        target_tenant,
        actor=actor,
        action="factory.business.clone",
        resource_type="business",
        resource_id=result.business_id,
        business_id=result.business_id,
        detail={
            "source_business_id": source_business_id,
            "source_tenant_id": tenant_id,
            "not_copied": [row["class"] for row in _NOT_COPIED],
        },
    )
    return FactoryResult(
        tenant_id=target_tenant,
        legal_entity_id=result.legal_entity_id,
        business_id=result.business_id,
        brand_id=result.brand_id,
        created=dict(result.created),
        not_copied=_NOT_COPIED,
        events=tuple(result.events) + (event.event_id,),
        source_business_id=source_business_id,
    )


def business_legal_entity(
    store: "BusinessStore", tenant_id: str, business_id: str
) -> LegalEntity:
    business = store.get_business(tenant_id, business_id)
    metadata = business.metadata if isinstance(business.metadata, Mapping) else {}
    entity_id = str(metadata.get("legal_entity_id") or "")
    if not entity_id:
        raise BusinessError(
            FACTORY_ENTITY_MISMATCH_CODE,
            f"business {business_id!r} is not bound to a legal entity",
        )
    entity = get_legal_entity(store, tenant_id, entity_id)
    return entity


@dataclass(frozen=True, slots=True)
class EntityBooks:
    """A per-legal-entity view over the one ledger table."""

    store: "BusinessStore"
    tenant_id: str
    business_id: str
    legal_entity_id: str
    namespace: str

    def post(
        self,
        *,
        lines: Iterable[Any],
        currency: str,
        idempotency_key: str,
        memo: str = "",
        actor: str = "system",
    ) -> finance.Posting:
        posting, _created = finance.post(
            self.store,
            tenant_id=self.tenant_id,
            business_id=self.business_id,
            lines=lines,
            currency=currency,
            idempotency_key=idempotency_key,
            memo=memo,
            actor=actor,
            namespace=self.namespace,
        )
        return posting

    def summary(self, *, currency: str | None = None) -> dict[str, Any]:
        return finance.summary(
            self.store,
            self.tenant_id,
            business_id=self.business_id,
            currency=currency,
            namespace=self.namespace,
        )

    def period_summary(
        self, period: str, *, currency: str | None = None
    ) -> dict[str, Any]:
        return finance.period_summary(
            self.store,
            self.tenant_id,
            period,
            business_id=self.business_id,
            currency=currency,
            namespace=self.namespace,
        )

    def balanced(self) -> bool:
        return bool(self.summary()["balanced"])


def entity_books(
    store: "BusinessStore",
    tenant_id: str,
    business_id: str,
    *,
    legal_entity_id: str | None = None,
) -> EntityBooks:
    """Return the books view for the business's (or requested) entity."""
    business = store.get_business(tenant_id, business_id)
    metadata = business.metadata if isinstance(business.metadata, Mapping) else {}
    bound = str(metadata.get("legal_entity_id") or "")
    chosen = legal_entity_id or bound
    if not chosen:
        raise BusinessError(
            FACTORY_ENTITY_MISMATCH_CODE,
            f"business {business_id!r} is not bound to a legal entity",
        )
    get_legal_entity(store, tenant_id, chosen)
    if legal_entity_id is not None and bound and chosen != bound:
        raise BusinessError(
            FACTORY_ENTITY_MISMATCH_CODE,
            f"business {business_id!r} is bound to entity {bound!r}, not"
            f" {chosen!r}",
        )
    return EntityBooks(
        store=store,
        tenant_id=tenant_id,
        business_id=business_id,
        legal_entity_id=chosen,
        namespace=f"entity:{chosen}",
    )


def not_copied_defaults() -> tuple[dict[str, str], ...]:
    return _NOT_COPIED


__all__ = [
    "Brand",
    "ENTITY_KINDS",
    "EntityBooks",
    "FACTORY_BAD_TEMPLATE_CODE",
    "FACTORY_ENTITY_MISMATCH_CODE",
    "FACTORY_INHERIT_REFUSED_CODE",
    "FACTORY_PERSONAL_FIRST_CODE",
    "FACTORY_TENANT_MISMATCH_CODE",
    "FORBIDDEN_KEYS",
    "FactoryResult",
    "LegalEntity",
    "STRUCTURAL_KEYS",
    "brand_for_business",
    "business_legal_entity",
    "clone_business",
    "create_brand",
    "create_business",
    "create_from_template",
    "create_legal_entity",
    "create_personal_books",
    "entity_books",
    "get_legal_entity",
    "list_legal_entities",
    "not_copied_defaults",
    "personal_entity",
]
