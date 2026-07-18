from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
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
