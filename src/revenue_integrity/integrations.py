from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from importlib.resources import files
from typing import Any, Mapping, Protocol, runtime_checkable


class CapabilityKind(StrEnum):
    SOURCE_ADAPTER = "source_adapter"
    TERMINOLOGY = "terminology"
    GROUPER_PRICER = "grouper_pricer"


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    component_id: str
    version: str
    kind: CapabilityKind
    production_ready: bool
    supported_formats: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.component_id, str)
            or not self.component_id.strip()
            or not isinstance(self.version, str)
            or not self.version.strip()
        ):
            raise ValueError("capability requires component_id and version")
        if not isinstance(self.kind, CapabilityKind):
            raise ValueError("capability kind is unsupported")
        if len(self.supported_formats) != len(set(self.supported_formats)):
            raise ValueError("supported formats must be unique")
        if not isinstance(self.production_ready, bool):
            raise ValueError("production_ready must be a boolean")
        if any(not isinstance(item, str) or not item.strip() for item in self.supported_formats):
            raise ValueError("supported formats must be non-empty strings")


@runtime_checkable
class GovernedCapability(Protocol):
    descriptor: CapabilityDescriptor


class CapabilityRegistry:
    """Dependency registry that refuses demo or unapproved adapters in production."""

    def __init__(self, *, production: bool = False) -> None:
        self.production = production
        self._components: dict[tuple[CapabilityKind, str], GovernedCapability] = {}

    def register(self, component: GovernedCapability) -> None:
        descriptor = component.descriptor
        if self.production and not descriptor.production_ready:
            raise ValueError(f"component {descriptor.component_id!r} is not approved for production")
        key = (descriptor.kind, descriptor.component_id)
        if key in self._components:
            raise ValueError(f"capability already registered: {descriptor.kind}/{descriptor.component_id}")
        self._components[key] = component

    def resolve(self, kind: CapabilityKind, component_id: str) -> GovernedCapability:
        try:
            return self._components[(kind, component_id)]
        except KeyError as exc:
            raise LookupError(f"capability not registered: {kind}/{component_id}") from exc


@dataclass(frozen=True, slots=True)
class TerminologyResult:
    source_system: str
    source_code: str
    target_system: str
    target_code: str | None
    equivalence: str
    mapping_version: str


class TerminologyService(Protocol):
    descriptor: CapabilityDescriptor

    def normalize(self, *, system: str, code: str, target_system: str, context: Mapping[str, Any]) -> TerminologyResult: ...


class UnavailableTerminologyService:
    descriptor = CapabilityDescriptor("terminology-unavailable", "1", CapabilityKind.TERMINOLOGY, False)

    def normalize(self, *, system: str, code: str, target_system: str, context: Mapping[str, Any]) -> TerminologyResult:
        raise RuntimeError("no governed terminology service is configured")


@dataclass(frozen=True, slots=True)
class EquivalenceTable:
    """A governed, digest-stamped synonym -> canonical equivalence table.

    The table only folds *equivalent* terms onto a single canonical value; it can never
    fabricate a target for a value it does not know. ``digest`` is a stable sha256 over the
    canonical (sorted, minified) JSON of the governed fields so that any change to the
    mappings, id, version, or status changes the digest and forces re-governance.
    """

    table_id: str
    version: str
    status: str
    equivalences: Mapping[str, str]

    _APPROVED_STATUSES = ("approved", "approved-for-demo")

    def __post_init__(self) -> None:
        for name, value in (("table_id", self.table_id), ("version", self.version), ("status", self.status)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"equivalence table requires a non-empty {name}")
        if self.status not in self._APPROVED_STATUSES:
            raise ValueError(f"equivalence table status {self.status!r} is not approved")
        if not isinstance(self.equivalences, Mapping) or not self.equivalences:
            raise ValueError("equivalence table requires a non-empty equivalences mapping")
        normalized: dict[str, str] = {}
        for synonym, canonical in self.equivalences.items():
            if not isinstance(synonym, str) or not synonym.strip():
                raise ValueError("equivalence synonyms must be non-empty strings")
            if not isinstance(canonical, str) or not canonical.strip():
                raise ValueError("equivalence canonical values must be non-empty strings")
            key = synonym.strip().casefold()
            if key in normalized and normalized[key] != canonical:
                raise ValueError(f"conflicting canonical values for synonym {synonym!r}")
            normalized[key] = canonical
        # dataclass is frozen; install the case-folded index via object.__setattr__
        object.__setattr__(self, "equivalences", dict(normalized))

    @property
    def digest(self) -> str:
        payload = {
            "table_id": self.table_id,
            "version": self.version,
            "status": self.status,
            "equivalences": self.equivalences,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EquivalenceTable":
        if not isinstance(data, Mapping):
            raise ValueError("equivalence table definition must be a mapping")
        required = {"table_id", "version", "status", "equivalences"}
        if missing := required - set(data):
            raise ValueError(f"equivalence table is missing fields: {sorted(missing)}")
        return cls(
            table_id=str(data["table_id"]),
            version=str(data["version"]),
            status=str(data["status"]),
            equivalences=data["equivalences"],
        )


def load_equivalence_table(
    resource_name: str = "data/terminology/wound_care_terms_v1.json",
) -> EquivalenceTable:
    """Load a packaged, governed equivalence table (mirrors ``load_grouping_definition``)."""
    resource = files("revenue_integrity").joinpath(resource_name)
    return EquivalenceTable.from_dict(json.loads(resource.read_text(encoding="utf-8")))


class TableTerminologyService:
    """Deterministic, governed synonym normalizer (an audited pre-pass, NOT authoritative).

    ``normalize(value)`` folds a known synonym to its canonical form and passes any unknown
    value through unchanged (equivalent-only fold — it never fabricates a canonical value).
    Matching is case-insensitive on the trimmed input; the returned canonical value is the
    governed table's exact spelling. This service is intentionally NOT wired into the
    engine's authoritative coding/billing path: use it only as a documented, audited
    pre-pass whose provenance (``table_version`` / ``table_digest``) can be recorded.
    """

    def __init__(self, table: EquivalenceTable | None = None) -> None:
        self._table = table or load_equivalence_table()

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            component_id=f"terminology-table-{self._table.table_id}",
            version=self._table.version,
            kind=CapabilityKind.TERMINOLOGY,
            production_ready=False,
        )

    @property
    def table_version(self) -> str:
        return self._table.version

    @property
    def table_digest(self) -> str:
        return self._table.digest

    def normalize(self, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("normalize requires a string value")
        return self._table.equivalences.get(value.strip().casefold(), value)
