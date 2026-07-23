from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Any, Mapping, Protocol, runtime_checkable

from .models import Claim, EncounterCase
from .integrations import CapabilityDescriptor, CapabilityKind

#: Severity ranking used to pick the most severe tier deterministically.
_SEVERITY_RANK = {"none": 0, "cc": 1, "mcc": 2}
_EXECUTABLE_GROUPER_STATUSES = frozenset({"approved", "approved-for-demo"})


@dataclass(frozen=True, slots=True)
class GroupingDerivationStep:
    """One deterministic step in how a DRG and payment were derived (for audit/explainability)."""

    step: str
    value: str
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.step or not self.value:
            raise ValueError("grouping derivation step requires a step and value")

    def to_dict(self) -> dict[str, str]:
        return {"step": self.step, "value": self.value, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class GroupingResult:
    drg: str
    estimated_payment_cents: int
    grouper_version: str
    derivation: tuple[GroupingDerivationStep, ...] = ()

    def __post_init__(self) -> None:
        if not self.drg or not self.grouper_version:
            raise ValueError("grouping result requires a DRG and grouper version")
        if isinstance(self.estimated_payment_cents, bool) or self.estimated_payment_cents < 0:
            raise ValueError("estimated payment must be a non-negative integer number of cents")

    def to_dict(self) -> dict[str, Any]:
        return {
            "drg": self.drg,
            "estimated_payment_cents": self.estimated_payment_cents,
            "grouper_version": self.grouper_version,
            "derivation": [step.to_dict() for step in self.derivation],
        }


@runtime_checkable
class Grouper(Protocol):
    """Boundary for a licensed, versioned DRG grouper and contract-aware pricer."""

    def group(self, case: EncounterCase, claim: Claim) -> GroupingResult: ...


def derivation_pair(baseline: GroupingResult, simulated: GroupingResult) -> dict[str, list[dict[str, str]]]:
    """Serialize the baseline (current) and simulated grouping derivations for a finding.

    This is the reviewer-facing explanation of *why* a DRG (and its payment) changed:
    the deterministic severity → tier → pricing steps for the current claim and the
    reviewed candidate. Pure serialization of already-produced grouper output.
    """
    return {
        "current": [step.to_dict() for step in baseline.derivation],
        "simulated": [step.to_dict() for step in simulated.derivation],
    }


@dataclass(frozen=True, slots=True)
class GroupingTier:
    severity: str
    drg: str
    title: str
    relative_weight_micros: int


@dataclass(frozen=True, slots=True)
class GroupingSelector:
    """Deterministic, data-driven criteria for when a grouping definition applies to a case.

    A definition with no criteria (an empty selector) never matches on its own — it is only
    reachable as the registry's registered default. When any criterion is set it must match
    the case exactly (all set criteria must hold). Selection is specialty-agnostic: it keys
    off structural case attributes (the bound ontology, or a metadata service line) and never
    hardcodes a particular clinical specialty.
    """

    ontology_ids: frozenset[str] = field(default_factory=frozenset)
    service_lines: frozenset[str] = field(default_factory=frozenset)

    def is_empty(self) -> bool:
        return not self.ontology_ids and not self.service_lines

    def matches(self, case: EncounterCase) -> bool:
        if self.is_empty():
            return False
        if self.ontology_ids and case.ontology.ontology_id not in self.ontology_ids:
            return False
        if self.service_lines:
            service_line = case.metadata.get("service_line")
            if not isinstance(service_line, str) or service_line not in self.service_lines:
                return False
        return True

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "GroupingSelector":
        if data is None:
            return cls()
        if not isinstance(data, Mapping):
            raise ValueError("grouping definition applies_to must be an object")
        allowed = {"ontology_ids", "service_lines"}
        if unknown := set(data) - allowed:
            raise ValueError(f"grouping definition applies_to has unknown keys: {sorted(unknown)}")
        return cls(
            ontology_ids=_string_frozenset(data.get("ontology_ids", []), "applies_to.ontology_ids"),
            service_lines=_string_frozenset(data.get("service_lines", []), "applies_to.service_lines"),
        )


def _string_frozenset(raw: Any, name: str) -> frozenset[str]:
    if not isinstance(raw, list):
        raise ValueError(f"grouping definition {name} must be an array")
    values: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"grouping definition {name} must be non-empty strings")
        values.append(item)
    return frozenset(values)


@dataclass(frozen=True, slots=True)
class GroupingDefinition:
    """A governed, versioned, data-driven demo grouping table.

    Deterministic and integer-cent only. It is a FAKE integration artifact: its version
    must contain ``not-for-billing`` so it can never be mistaken for a licensed grouper.
    """

    grouper_id: str
    version: str
    status: str
    base_rate_cents: int
    tiers: tuple[GroupingTier, ...]
    severity_codes: Mapping[str, str] = field(default_factory=dict)
    severity_prefixes: Mapping[str, str] = field(default_factory=dict)
    #: Diagnoses that, when NOT present on admission (poa == 'N'), do not drive
    #: MCC/CC severity — the hospital-acquired-condition (HAC) exclusion. Only
    #: applied when the claim carries per-diagnosis ``diagnosis_details``.
    hac_codes: frozenset[str] = field(default_factory=frozenset)
    #: Deterministic criteria selecting which cases this definition applies to. Empty
    #: means the definition is only reachable as a registry default (legacy behavior).
    applies_to: GroupingSelector = field(default_factory=GroupingSelector)

    def __post_init__(self) -> None:
        if not self.grouper_id.strip() or not self.version.strip():
            raise ValueError("grouping definition requires a grouper_id and version")
        if "not-for-billing" not in self.version:
            raise ValueError("demo grouping definition version must contain 'not-for-billing'")
        if self.status not in _EXECUTABLE_GROUPER_STATUSES:
            raise ValueError(f"grouping definition status {self.status!r} is not executable")
        if isinstance(self.base_rate_cents, bool) or not isinstance(self.base_rate_cents, int) or self.base_rate_cents <= 0:
            raise ValueError("grouping definition base_rate_cents must be a positive integer")
        if not self.tiers:
            raise ValueError("grouping definition requires at least one tier")
        severities = [tier.severity for tier in self.tiers]
        drgs = [tier.drg for tier in self.tiers]
        if len(severities) != len(set(severities)) or len(drgs) != len(set(drgs)):
            raise ValueError("grouping definition tiers must have unique severities and DRGs")
        for tier in self.tiers:
            if tier.severity not in _SEVERITY_RANK:
                raise ValueError(f"unknown severity {tier.severity!r} in grouping definition")
            if isinstance(tier.relative_weight_micros, bool) or not isinstance(tier.relative_weight_micros, int) or tier.relative_weight_micros <= 0:
                raise ValueError("grouping tier relative_weight_micros must be a positive integer")
        if "none" not in severities:
            raise ValueError("grouping definition must define a 'none' (default) tier")
        for mapping, name in ((self.severity_codes, "codes"), (self.severity_prefixes, "prefixes")):
            for severity in mapping.values():
                if severity not in _SEVERITY_RANK:
                    raise ValueError(f"unknown severity {severity!r} in diagnosis_severity.{name}")
        for code in self.hac_codes:
            if not isinstance(code, str) or not code.strip():
                raise ValueError("grouping definition hac_codes must be non-empty strings")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GroupingDefinition":
        if not isinstance(data, Mapping):
            raise ValueError("grouping definition must be an object")
        required = {"grouper_id", "version", "status", "base_rate_cents", "tiers"}
        if missing := required - set(data):
            raise ValueError(f"grouping definition missing fields: {sorted(missing)}")
        raw_tiers = data["tiers"]
        if not isinstance(raw_tiers, list) or not raw_tiers:
            raise ValueError("grouping definition tiers must be a non-empty array")
        tiers = tuple(
            GroupingTier(
                severity=str(item["severity"]),
                drg=str(item["drg"]),
                title=str(item.get("title", item["drg"])),
                relative_weight_micros=item["relative_weight_micros"],
            )
            for item in raw_tiers
        )
        severity = data.get("diagnosis_severity", {}) or {}
        raw_hac = data.get("hac_codes", []) or []
        if not isinstance(raw_hac, list):
            raise ValueError("grouping definition hac_codes must be an array")
        return cls(
            grouper_id=str(data["grouper_id"]),
            version=str(data["version"]),
            status=str(data["status"]),
            base_rate_cents=data["base_rate_cents"],
            tiers=tiers,
            severity_codes=dict(severity.get("codes", {}) or {}),
            severity_prefixes=dict(severity.get("prefixes", {}) or {}),
            hac_codes=frozenset(str(code) for code in raw_hac),
            applies_to=GroupingSelector.from_dict(data.get("applies_to")),
        )

    def tier_for(self, severity: str) -> GroupingTier:
        for tier in self.tiers:
            if tier.severity == severity:
                return tier
        raise ValueError(f"grouping definition has no tier for severity {severity!r}")


def load_grouping_definition(resource_name: str = "data/demo_grouping_v1.json") -> GroupingDefinition:
    """Load a packaged, governed grouping definition (mirrors load_builtin_ontology)."""
    resource = files("revenue_integrity").joinpath(resource_name)
    return GroupingDefinition.from_dict(json.loads(resource.read_text(encoding="utf-8")))


@dataclass(frozen=True, slots=True)
class GroupingRegistry:
    """An ordered, governed collection of grouping definitions with a deterministic default.

    Makes the demo grouper specialty-agnostic: multiple versioned definitions can be
    registered (e.g. one per bound ontology / service line), and the applicable one is
    selected for a case by evaluating each definition's ``applies_to`` selector in
    registration order. The first match wins deterministically; when none match, the
    ``default`` definition is used. The default is the same single pressure-injury
    definition shipped today, so the demo case regroups to an identical result.
    """

    default: GroupingDefinition
    definitions: tuple[GroupingDefinition, ...] = ()

    def __post_init__(self) -> None:
        ids = [self.default.grouper_id, *(d.grouper_id for d in self.definitions)]
        if len(ids) != len(set(ids)):
            raise ValueError("grouping registry definitions must have unique grouper_ids")

    def select(self, case: EncounterCase) -> GroupingDefinition:
        for definition in self.definitions:
            if definition.applies_to.matches(case):
                return definition
        return self.default

    @classmethod
    def single(cls, definition: GroupingDefinition | None = None) -> "GroupingRegistry":
        return cls(default=definition or load_grouping_definition())


def default_demo_registry() -> GroupingRegistry:
    """The governed demo grouping registry: pressure-injury default + specialty verticals.

    Additive and specialty-agnostic. The default definition is the same single
    pressure-injury table shipped today, so any case that matches no vertical selector
    (including the wound-care demo case) regroups byte-identically. Each additional
    definition carries an ``applies_to`` selector keyed on the bound ontology, so it is
    only ever selected for cases on that ontology. New verticals are registered here as
    fresh, versioned ``not-for-billing`` definitions without disturbing existing ones.
    """
    return GroupingRegistry(
        default=load_grouping_definition(),
        definitions=(load_grouping_definition("data/sepsis_grouping_v1.json"),),
    )


class DeterministicDemoGrouper:
    """Fake, DATA-DRIVEN integration adapter. Never use its values for real coding or billing.

    Behavior is defined entirely by a governed, versioned grouping-definition artifact
    (``data/demo_grouping_v1.json``): the coded diagnoses are scanned against a
    severity table, the most severe tier wins, and the payment is priced from that tier's
    relative weight with integer-cent math. Every result carries a deterministic derivation
    trace so a reviewer can see exactly why a DRG and payment were produced.
    """

    def __init__(
        self,
        definition: GroupingDefinition | None = None,
        *,
        registry: GroupingRegistry | None = None,
    ) -> None:
        if definition is not None and registry is not None:
            raise ValueError("provide either a definition or a registry, not both")
        self._registry = registry or GroupingRegistry.single(definition)

    @property
    def version(self) -> str:
        """Version of the default (pressure-injury demo) definition, preserved for provenance."""
        return self._registry.default.version

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            "deterministic-demo-grouper", self._registry.default.version, CapabilityKind.GROUPER_PRICER,
            False, ("synthetic-encounter-case-v2",),
        )

    def group(self, case: EncounterCase, claim: Claim) -> GroupingResult:
        definition = self._registry.select(case)
        severity, driver, exclusions = self._resolve_severity(definition, claim)
        tier = definition.tier_for(severity)
        payment = definition.base_rate_cents * tier.relative_weight_micros // 1_000_000
        # POA exclusion steps are emitted only when a HAC exclusion actually applied,
        # so the legacy (no diagnosis_details) path stays byte-identical.
        exclusion_steps = tuple(
            GroupingDerivationStep("poa_exclusion", code, "hospital-acquired (poa=N); excluded from severity")
            for code in exclusions
        )
        derivation = (
            *exclusion_steps,
            GroupingDerivationStep("severity_resolution", severity, driver or "no severity-driving diagnosis"),
            GroupingDerivationStep("tier_selection", tier.drg, f"{severity} tier"),
            GroupingDerivationStep("pricing", str(payment), f"base {definition.base_rate_cents}c x {tier.relative_weight_micros} micros"),
        )
        return GroupingResult(tier.drg, payment, definition.version, derivation)

    def _resolve_severity(self, definition: GroupingDefinition, claim: Claim) -> tuple[str, str | None, tuple[str, ...]]:
        """Resolve the most-severe tier deterministically.

        When ``claim.diagnosis_details`` is present, HAC codes that are not present on
        admission (poa == 'N') are excluded from driving severity. When it is empty, the
        codes are scanned exactly as before (byte-identical legacy behavior).
        """
        excluded = self._hac_excluded_codes(definition, claim)
        best_severity = "none"
        best_driver: str | None = None
        for code in claim.diagnoses:
            if code in excluded:
                continue
            severity = self._severity_of(definition, code)
            if _SEVERITY_RANK[severity] > _SEVERITY_RANK[best_severity]:
                best_severity, best_driver = severity, code
        exclusions = tuple(code for code in claim.diagnoses if code in excluded)
        return best_severity, best_driver, exclusions

    @staticmethod
    def _hac_excluded_codes(definition: GroupingDefinition, claim: Claim) -> frozenset[str]:
        if not claim.diagnosis_details:
            return frozenset()
        return frozenset(
            detail.code
            for detail in claim.diagnosis_details
            if detail.poa == "N" and detail.code in definition.hac_codes
        )

    @staticmethod
    def _severity_of(definition: GroupingDefinition, code: str) -> str:
        if code in definition.severity_codes:
            return definition.severity_codes[code]
        best = "none"
        for prefix, severity in definition.severity_prefixes.items():
            if code.startswith(prefix) and _SEVERITY_RANK[severity] > _SEVERITY_RANK[best]:
                best = severity
        return best
