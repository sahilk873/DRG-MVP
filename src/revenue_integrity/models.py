from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

SUPPORTED_SCHEMA_VERSION = "2.0.0"
EXTRACTION_POLICY_FIELDS = (
    "max_documents",
    "max_document_characters",
    "max_total_document_characters",
    "max_evidence_items",
    "max_evidence_characters",
    "max_total_evidence_characters",
    "max_entities",
    "max_relations",
    "max_assertions",
)


class AssertionStatus(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    UNCERTAIN = "uncertain"
    HISTORICAL = "historical"


class DocumentationStatus(StrEnum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    CONFLICTED = "conflicted"
    ABSENT = "absent"


class Disposition(StrEnum):
    CODING_REVIEW = "coding_review"
    CDI_QUERY = "cdi_query"
    CHARGE_REVIEW = "charge_review"
    COMPLIANCE_REVIEW = "compliance_review"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NO_OPPORTUNITY = "no_opportunity"


@dataclass(frozen=True, slots=True)
class CaseValidationLimits:
    max_evidence_items: int = 2_000
    max_evidence_characters: int = 2_000
    max_total_evidence_characters: int = 250_000
    max_ontology_entities: int = 2_000
    max_ontology_relations: int = 5_000
    max_assertions: int = 2_000

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"case validation limit {name} must be a positive integer")


DEFAULT_CASE_VALIDATION_LIMITS = CaseValidationLimits()


@dataclass(frozen=True, slots=True)
class ExtractionProvenance:
    framework: str
    model_id: str
    agent_id: str
    extracted_at: str
    schema_version: str
    extraction_policy: Mapping[str, int]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExtractionProvenance":
        fields = ("framework", "model_id", "agent_id", "extracted_at", "schema_version")
        required = fields + ("extraction_policy",)
        _validate_keys(data, required=required, allowed=required, object_name="provenance")
        _parse_iso_datetime(str(data["extracted_at"]), "provenance.extracted_at")
        policy = _mapping(data["extraction_policy"], "provenance.extraction_policy")
        _validate_keys(
            policy,
            required=EXTRACTION_POLICY_FIELDS,
            allowed=EXTRACTION_POLICY_FIELDS,
            object_name="provenance.extraction_policy",
        )
        parsed_policy: dict[str, int] = {}
        for name in EXTRACTION_POLICY_FIELDS:
            value = policy[name]
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"provenance.extraction_policy.{name} must be a positive integer")
            parsed_policy[name] = value
        provenance = cls(
            **{name: _nonempty_string(data[name], f"provenance.{name}") for name in fields},
            extraction_policy=parsed_policy,
        )
        if provenance.framework != "mastra":
            raise ValueError("provenance.framework must be 'mastra'")
        return provenance


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    document_id: str
    author_role: str
    recorded_at: str
    text: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Evidence":
        fields = ("evidence_id", "document_id", "author_role", "recorded_at", "text")
        _validate_keys(data, required=fields, allowed=fields, object_name="evidence")
        recorded_at = _nonempty_string(data["recorded_at"], "evidence.recorded_at")
        _parse_iso_datetime(recorded_at, "evidence.recorded_at")
        return cls(
            evidence_id=_nonempty_string(data["evidence_id"], "evidence.evidence_id"),
            document_id=_nonempty_string(data["document_id"], "evidence.document_id"),
            author_role=_nonempty_string(data["author_role"], "evidence.author_role"),
            recorded_at=recorded_at,
            text=_nonempty_string(data["text"], "evidence.text"),
        )


@dataclass(frozen=True, slots=True)
class Assertion:
    assertion_id: str
    subject_id: str
    concept: str
    status: AssertionStatus
    documentation_status: DocumentationStatus
    confidence: float
    attributes: Mapping[str, Any] = field(default_factory=dict)
    evidence_ids: tuple[str, ...] = ()
    contradicting_evidence_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Assertion":
        required = (
            "assertion_id", "subject_id", "concept", "status", "documentation_status",
            "confidence", "attributes", "evidence_ids",
        )
        allowed = required + ("contradicting_evidence_ids",)
        _validate_keys(data, required=required, allowed=allowed, object_name="assertion")
        confidence = _number(data["confidence"], "assertion.confidence")
        if not 0 <= confidence <= 1:
            raise ValueError("assertion.confidence must be between 0 and 1")
        attributes = data["attributes"]
        if not isinstance(attributes, Mapping):
            raise ValueError("assertion.attributes must be an object")
        return cls(
            assertion_id=_nonempty_string(data["assertion_id"], "assertion.assertion_id"),
            subject_id=_nonempty_string(data["subject_id"], "assertion.subject_id"),
            concept=_nonempty_string(data["concept"], "assertion.concept"),
            status=AssertionStatus(data["status"]),
            documentation_status=DocumentationStatus(data["documentation_status"]),
            confidence=confidence,
            attributes=dict(attributes),
            evidence_ids=_unique_strings(data["evidence_ids"], "assertion.evidence_ids"),
            contradicting_evidence_ids=_unique_strings(
                data.get("contradicting_evidence_ids", []), "assertion.contradicting_evidence_ids"
            ),
        )


@dataclass(frozen=True, slots=True)
class Claim:
    diagnoses: tuple[str, ...]
    procedures: tuple[str, ...]
    charges: tuple[str, ...]
    drg: str | None = None
    allowed_amount_cents: int | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Claim":
        required = ("diagnoses", "procedures", "charges")
        allowed = required + ("drg", "allowed_amount_cents")
        _validate_keys(data, required=required, allowed=allowed, object_name="claim")
        amount = data.get("allowed_amount_cents")
        if amount is not None and (isinstance(amount, bool) or not isinstance(amount, int) or amount < 0):
            raise ValueError("claim.allowed_amount_cents must be a non-negative integer")
        drg = data.get("drg")
        if drg is not None:
            drg = _nonempty_string(drg, "claim.drg")
        return cls(
            diagnoses=_unique_strings(data["diagnoses"], "claim.diagnoses"),
            procedures=_unique_strings(data["procedures"], "claim.procedures"),
            charges=_unique_strings(data["charges"], "claim.charges"),
            drg=drg,
            allowed_amount_cents=amount,
        )


@dataclass(frozen=True, slots=True)
class EncounterCase:
    schema_version: str
    case_id: str
    patient_id: str
    encounter_id: str
    admitted_at: str
    discharged_at: str
    evidence: tuple[Evidence, ...]
    ontology: "OntologyGraph"
    assertions: tuple[Assertion, ...]
    claim: Claim
    provenance: ExtractionProvenance
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        ontology_definition: "OntologyDefinition | None" = None,
        validation_limits: CaseValidationLimits | None = None,
    ) -> "EncounterCase":
        required = (
            "schema_version", "case_id", "patient_id", "encounter_id", "admitted_at", "discharged_at",
            "evidence", "ontology", "assertions", "claim", "provenance",
        )
        allowed = required + ("metadata",)
        _validate_keys(data, required=required, allowed=allowed, object_name="case")
        _validate_case_limits(data, validation_limits or DEFAULT_CASE_VALIDATION_LIMITS)
        admitted_at = _nonempty_string(data["admitted_at"], "case.admitted_at")
        discharged_at = _nonempty_string(data["discharged_at"], "case.discharged_at")
        admitted = _parse_iso_datetime(admitted_at, "case.admitted_at")
        discharged = _parse_iso_datetime(discharged_at, "case.discharged_at")
        if admitted > discharged:
            raise ValueError("case.admitted_at must not be after case.discharged_at")
        metadata = data.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ValueError("case.metadata must be an object")
        from .ontology import OntologyGraph, load_builtin_ontology

        ontology = OntologyGraph.from_dict(_mapping(data["ontology"], "case.ontology"))
        case = cls(
            schema_version=_nonempty_string(data["schema_version"], "case.schema_version"),
            case_id=_nonempty_string(data["case_id"], "case.case_id"),
            patient_id=_nonempty_string(data["patient_id"], "case.patient_id"),
            encounter_id=_nonempty_string(data["encounter_id"], "case.encounter_id"),
            admitted_at=admitted_at,
            discharged_at=discharged_at,
            evidence=tuple(
                Evidence.from_dict(_mapping(item, "case.evidence item"))
                for item in _list(data["evidence"], "case.evidence")
            ),
            ontology=ontology,
            assertions=tuple(
                Assertion.from_dict(_mapping(item, "case.assertions item"))
                for item in _list(data["assertions"], "case.assertions")
            ),
            claim=Claim.from_dict(_mapping(data["claim"], "case.claim")),
            provenance=ExtractionProvenance.from_dict(_mapping(data["provenance"], "case.provenance")),
            metadata=dict(metadata),
        )
        if case.schema_version != SUPPORTED_SCHEMA_VERSION:
            raise ValueError(f"unsupported case schema_version: {case.schema_version}")
        if case.provenance.schema_version != case.schema_version:
            raise ValueError("case and provenance schema versions must match")
        case.validate_lineage()
        definition = ontology_definition or load_builtin_ontology(
            case.ontology.ontology_id,
            case.ontology.ontology_version,
        )
        definition.validate_graph(case.ontology, {item.evidence_id for item in case.evidence})
        return case

    def validate_lineage(self) -> None:
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_id values must be unique")
        known = set(evidence_ids)
        assertion_ids: set[str] = set()
        ontology_entity_ids = {entity.entity_id for entity in self.ontology.entities}
        for assertion in self.assertions:
            if assertion.assertion_id in assertion_ids:
                raise ValueError("assertion_id values must be unique")
            assertion_ids.add(assertion.assertion_id)
            if assertion.subject_id not in ontology_entity_ids:
                raise ValueError(
                    f"assertion {assertion.assertion_id} references unknown ontology subject {assertion.subject_id}"
                )
            referenced = set(assertion.evidence_ids + assertion.contradicting_evidence_ids)
            if not assertion.evidence_ids:
                raise ValueError(f"assertion {assertion.assertion_id} must cite supporting evidence")
            if overlap := set(assertion.evidence_ids) & set(assertion.contradicting_evidence_ids):
                raise ValueError(f"assertion {assertion.assertion_id} cites evidence as both supporting and contradicting: {sorted(overlap)}")
            if unknown := referenced - known:
                raise ValueError(f"assertion {assertion.assertion_id} references unknown evidence: {sorted(unknown)}")


@dataclass(frozen=True, slots=True)
class Finding:
    finding_id: str
    rule_id: str
    rule_package_id: str
    rule_package_version: str
    title: str
    disposition: Disposition
    confidence: float
    proposed_change: Mapping[str, Any]
    subject_ids: tuple[str, ...]
    assertion_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...]
    rationale: str
    requires_human_review: bool
    submitted_drg: str | None
    current_drg: str
    simulated_drg: str
    estimated_impact_cents: int
    grouper_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "rule_id": self.rule_id,
            "rule_package_id": self.rule_package_id,
            "rule_package_version": self.rule_package_version,
            "title": self.title,
            "disposition": self.disposition.value,
            "confidence": self.confidence,
            "proposed_change": dict(self.proposed_change),
            "subject_ids": list(self.subject_ids),
            "assertion_ids": list(self.assertion_ids),
            "evidence_ids": list(self.evidence_ids),
            "contradicting_evidence_ids": list(self.contradicting_evidence_ids),
            "rationale": self.rationale,
            "requires_human_review": self.requires_human_review,
            "submitted_drg": self.submitted_drg,
            "current_drg": self.current_drg,
            "simulated_drg": self.simulated_drg,
            "estimated_impact_cents": self.estimated_impact_cents,
            "grouper_version": self.grouper_version,
        }


def _validate_keys(data: Mapping[str, Any], *, required: tuple[str, ...], allowed: tuple[str, ...], object_name: str) -> None:
    missing = sorted(set(required) - set(data))
    unknown = sorted(set(data) - set(allowed))
    if missing:
        raise ValueError(f"{object_name} missing required fields: {missing}")
    if unknown:
        raise ValueError(f"{object_name} contains unknown fields: {unknown}")


def _validate_case_limits(data: Mapping[str, Any], limits: CaseValidationLimits) -> None:
    evidence = _list(data["evidence"], "case.evidence")
    assertions = _list(data["assertions"], "case.assertions")
    ontology = _mapping(data["ontology"], "case.ontology")
    entities = _list(ontology.get("entities"), "case.ontology.entities")
    relations = _list(ontology.get("relations"), "case.ontology.relations")
    counts = (
        (len(evidence), limits.max_evidence_items, "max_evidence_items"),
        (len(assertions), limits.max_assertions, "max_assertions"),
        (len(entities), limits.max_ontology_entities, "max_ontology_entities"),
        (len(relations), limits.max_ontology_relations, "max_ontology_relations"),
    )
    for actual, maximum, name in counts:
        if actual > maximum:
            raise ValueError(f"case exceeds {name} ({maximum})")
    total_evidence_characters = 0
    for item in evidence:
        if not isinstance(item, Mapping) or not isinstance(item.get("text"), str):
            continue
        length = len(item["text"])
        if length > limits.max_evidence_characters:
            raise ValueError(f"case evidence exceeds max_evidence_characters ({limits.max_evidence_characters})")
        total_evidence_characters += length
        if total_evidence_characters > limits.max_total_evidence_characters:
            raise ValueError(
                "case evidence exceeds max_total_evidence_characters "
                f"({limits.max_total_evidence_characters})"
            )


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _nonempty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    return float(value)


def _unique_strings(value: Any, name: str) -> tuple[str, ...]:
    items = _list(value, name)
    parsed = tuple(_nonempty_string(item, name) for item in items)
    if len(parsed) != len(set(parsed)):
        raise ValueError(f"{name} must not contain duplicates")
    return parsed


def _parse_iso_datetime(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed
