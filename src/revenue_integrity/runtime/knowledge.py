"""Append-only, hash-chained knowledge store — the system's deterministic memory.

An ``Exemplar`` is one unit of verified experience: an approved adapter mapping, a reusable
transform, an ontology delta, a grounded extraction, or a labeled reviewer outcome. Each is
keyed by a canonical ``content_hash`` and appended to a ledger whose records chain by
``previous_record_hash`` (mirroring ``audit.py``), so the memory is reproducible, tamper-evident,
and replayable. "Self-learning" here means the store grows with verified records and retrieval
improves — never opaque model drift.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping

from ..audit import canonical_hash

EXEMPLAR_KINDS = frozenset(
    {
        "adapter_mapping",
        "transform",
        "reader",
        "ontology_delta",
        "extraction",
        "review_outcome",
        "rule_package",
    }
)

DEFAULT_TENANT_ID = "__default__"


def _normalize_tenant(tenant_id: str | None) -> str:
    """Resolve a tenant scope. ``None`` maps to the single well-defined default tenant so that
    callers that never pass a tenant behave exactly as before (byte-identical chain)."""
    if tenant_id is None:
        return DEFAULT_TENANT_ID
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise ValueError("tenant_id must be a non-empty string")
    return tenant_id


def _normalize_features(features: Iterable[str]) -> tuple[str, ...]:
    """Deterministic feature tokens for retrieval: lowercased, de-duplicated, sorted."""
    tokens = {
        token.strip().lower()
        for token in features
        if isinstance(token, str) and token.strip()
    }
    if not tokens:
        raise ValueError("exemplar requires at least one non-empty feature token")
    return tuple(sorted(tokens))


@dataclass(frozen=True, slots=True)
class Exemplar:
    exemplar_id: str
    kind: str
    features: tuple[str, ...]
    payload: Mapping[str, Any]
    label: str
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.exemplar_id, str) or not self.exemplar_id.strip():
            raise ValueError("exemplar_id must be a non-empty string")
        if self.kind not in EXEMPLAR_KINDS:
            raise ValueError(f"unknown exemplar kind: {self.kind!r}")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("exemplar label must be a non-empty string")
        object.__setattr__(self, "features", _normalize_features(self.features))

    @property
    def content_hash(self) -> str:
        """Content address — identical experience produces an identical hash (idempotent memory)."""
        return canonical_hash({
            "kind": self.kind,
            "features": list(self.features),
            "payload": dict(self.payload),
            "label": self.label,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "exemplar_id": self.exemplar_id,
            "kind": self.kind,
            "features": list(self.features),
            "payload": dict(self.payload),
            "label": self.label,
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Exemplar":
        return cls(
            exemplar_id=data["exemplar_id"],
            kind=data["kind"],
            features=tuple(data["features"]),
            payload=dict(data.get("payload", {})),
            label=data["label"],
            provenance=dict(data.get("provenance", {})),
        )


class KnowledgeStore:
    """Deterministic, append-only, hash-chained ledger of verified experience.

    The ledger is **tenant-scoped**: every exemplar is recorded and retrieved under a
    ``tenant_id`` (defaulting to :data:`DEFAULT_TENANT_ID`). Each tenant owns an independent,
    append-only hash chain, so one tenant's records can never link into, leak into, or tamper
    with another's, and each chain verifies on its own. Callers that never pass a tenant address
    the single default tenant and observe exactly the previous behaviour — a byte-identical chain.
    The record body itself carries no tenant field; isolation is structural (separate chains).
    """

    def __init__(self) -> None:
        # Records and content-hash sets are partitioned per tenant so chains stay independent.
        self._records_by_tenant: dict[str, list[dict[str, Any]]] = {}
        self._seen_by_tenant: dict[str, set[str]] = {}

    def _tenant_records(self, tenant_id: str) -> list[dict[str, Any]]:
        return self._records_by_tenant.setdefault(tenant_id, [])

    def _tenant_seen(self, tenant_id: str) -> set[str]:
        return self._seen_by_tenant.setdefault(tenant_id, set())

    def record(self, exemplar: Exemplar, *, tenant_id: str | None = None) -> dict[str, Any]:
        """Append an exemplar to ``tenant_id``'s chain. Idempotent on content within a tenant:
        re-recording identical experience is a no-op that returns the existing ledger record, so
        replay never duplicates memory. Idempotency is scoped per tenant."""
        tenant = _normalize_tenant(tenant_id)
        records = self._tenant_records(tenant)
        seen = self._tenant_seen(tenant)
        content_hash = exemplar.content_hash
        if content_hash in seen:
            return next(r for r in records if r["content_hash"] == content_hash)
        body = {
            "exemplar": exemplar.to_dict(),
            "content_hash": content_hash,
            "previous_record_hash": records[-1]["record_hash"] if records else None,
        }
        body["record_hash"] = canonical_hash(body)
        records.append(body)
        seen.add(content_hash)
        return body

    def exemplars(self, kind: str | None = None, *, tenant_id: str | None = None) -> list[Exemplar]:
        tenant = _normalize_tenant(tenant_id)
        return [
            Exemplar.from_dict(record["exemplar"])
            for record in self._tenant_records(tenant)
            if kind is None or record["exemplar"]["kind"] == kind
        ]

    def tenants(self) -> list[str]:
        """Deterministic list of tenants that hold at least one record."""
        return sorted(t for t, records in self._records_by_tenant.items() if records)

    def __len__(self) -> int:
        return len(self._tenant_records(DEFAULT_TENANT_ID))

    def __iter__(self) -> Iterator[Exemplar]:
        return iter(self.exemplars())

    def _verify_records(self, records: list[dict[str, Any]]) -> bool:
        previous: str | None = None
        for record in records:
            claimed = record.get("record_hash")
            body = {key: value for key, value in record.items() if key != "record_hash"}
            if not isinstance(claimed, str) or canonical_hash(body) != claimed:
                return False
            if record.get("previous_record_hash") != previous:
                return False
            previous = claimed
        return True

    def verify_chain(self, *, tenant_id: str | None = None) -> bool:
        """Recompute every record hash for a tenant and confirm contiguous linkage.

        If ``tenant_id`` is ``None`` every tenant's chain is verified independently; a single
        broken chain fails the whole check while leaving other tenants' chains untouched."""
        if tenant_id is None and self._records_by_tenant:
            return all(self._verify_records(records) for records in self._records_by_tenant.values())
        return self._verify_records(self._tenant_records(_normalize_tenant(tenant_id)))

    @property
    def digest(self) -> str:
        """Digest of the default tenant's chain (backward-compatible property)."""
        return self.tenant_digest()

    def tenant_digest(self, tenant_id: str | None = None) -> str:
        records = self._tenant_records(_normalize_tenant(tenant_id))
        return records[-1]["record_hash"] if records else canonical_hash([])

    def to_dict(self) -> dict[str, Any]:
        """Serialise all tenants. The default tenant is emitted as the top-level ``records`` list
        (byte-identical to the pre-tenant format); any additional tenants go under ``tenants``."""
        default_records = list(self._tenant_records(DEFAULT_TENANT_ID))
        extra = {
            tenant: list(records)
            for tenant, records in sorted(self._records_by_tenant.items())
            if tenant != DEFAULT_TENANT_ID and records
        }
        data: dict[str, Any] = {"knowledge_schema_version": "1.0.0", "records": default_records}
        if extra:
            data["tenants"] = extra
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "KnowledgeStore":
        store = cls()
        for record in data.get("records", []):
            store.record(Exemplar.from_dict(record["exemplar"]))
        for tenant, records in data.get("tenants", {}).items():
            for record in records:
                store.record(Exemplar.from_dict(record["exemplar"]), tenant_id=tenant)
        return store
