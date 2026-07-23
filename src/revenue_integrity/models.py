from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from .financial import ClaimLine, FinancialSnapshot

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


class LifecycleState(StrEnum):
    """Where an encounter sits relative to billing.

    ``retrospective`` is the default so every legacy case/fixture (which omits the field)
    is unchanged. Prospective/concurrent encounters have not billed yet, so a raised
    finding can be surfaced as a pre-bill query rather than a retrospective correction.
    """

    PROSPECTIVE = "prospective"
    CONCURRENT = "concurrent"
    RETROSPECTIVE = "retrospective"
    POST_BILL = "post_bill"


class Disposition(StrEnum):
    CODING_REVIEW = "coding_review"
    CDI_QUERY = "cdi_query"
    CHARGE_REVIEW = "charge_review"
    COMPLIANCE_REVIEW = "compliance_review"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NO_OPPORTUNITY = "no_opportunity"


class ImpactStatus(StrEnum):
    ESTIMATED = "estimated"
    NOT_APPLICABLE = "not_applicable"
    UNAVAILABLE = "unavailable"


class RuleDomain(StrEnum):
    """The governed peer domains. Each is structurally walled off from the other:

    ``revenue_integrity`` rules may propose claim changes but carry no clinical action
    fields; ``clinical_care_gap`` rules carry clinical action fields but may never carry a
    claim-mutating proposed change. The wall is enforced at parse time in ``rules.py``.
    """

    REVENUE_INTEGRITY = "revenue_integrity"
    CLINICAL_CARE_GAP = "clinical_care_gap"


class GapDomain(StrEnum):
    """What kind of clinical care gap an analytics finding surfaces.

    Analytics only *identify* the gap; clinicians decide. A gap never mutates a claim,
    assigns a DRG, computes reimbursement, or bypasses review.
    """

    MISSING_ACTION = "missing_action"
    DELAYED_ACTION = "delayed_action"
    INCOMPLETE_FOLLOW_THROUGH = "incomplete_follow_through"


class ExceptionType(StrEnum):
    """Documented clinical reasons a surfaced gap may be a legitimate non-gap.

    These describe *evidence-grounded* exceptions a human reviewer confirms; the engine
    only records that an exception check was applied, never auto-closes on its basis.
    """

    PATIENT_REFUSAL = "patient_refusal"
    CONTRAINDICATION = "contraindication"
    TRANSFER = "transfer"
    HOSPICE = "hospice"
    OUTSIDE_CARE = "outside_care"
    DOCUMENTED_JUDGMENT = "documented_judgment"


class ClinicalUrgency(StrEnum):
    """How time-sensitive the recommended clinical action is (analytics signal only)."""

    ROUTINE = "routine"
    SAME_DAY = "same_day"
    URGENT = "urgent"
    EMERGENT = "emergent"


class GapStatus(StrEnum):
    """Lifecycle of a clinical_care_gap finding. Defaults to ``open``; a gap is never
    auto-closed by the engine — closure/exception/withdrawal are human-driven downstream."""

    OPEN = "open"
    ROUTED = "routed"
    CLOSED = "closed"
    EXCEPTION = "exception"
    WITHDRAWN = "withdrawn"


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
    ingestion: Mapping[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExtractionProvenance":
        fields = ("framework", "model_id", "agent_id", "extracted_at", "schema_version")
        required = fields + ("extraction_policy",)
        allowed = required + ("ingestion",)
        _validate_keys(data, required=required, allowed=allowed, object_name="provenance")
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
        ingestion = data.get("ingestion")
        if ingestion is not None:
            ingestion = _mapping(ingestion, "provenance.ingestion")
            ingestion_fields = (
                "framework", "adapter_id", "adapter_version", "source_schema_fingerprint",
                "input_manifest_digest", "transformed_at", "runtime_version",
            )
            _validate_keys(
                ingestion,
                required=ingestion_fields,
                allowed=ingestion_fields,
                object_name="provenance.ingestion",
            )
            if ingestion["framework"] != "deterministic-adapter":
                raise ValueError("provenance.ingestion.framework must be 'deterministic-adapter'")
            for name in ("source_schema_fingerprint", "input_manifest_digest"):
                value = _nonempty_string(ingestion[name], f"provenance.ingestion.{name}")
                if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                    raise ValueError(f"provenance.ingestion.{name} must be a lowercase SHA-256 digest")
            _parse_iso_datetime(str(ingestion["transformed_at"]), "provenance.ingestion.transformed_at")
            for name in ("adapter_id", "adapter_version", "runtime_version"):
                _nonempty_string(ingestion[name], f"provenance.ingestion.{name}")
        provenance = cls(
            **{name: _nonempty_string(data[name], f"provenance.{name}") for name in fields},
            extraction_policy=parsed_policy,
            ingestion=dict(ingestion) if ingestion is not None else None,
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
    source_locator: Mapping[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Evidence":
        fields = ("evidence_id", "document_id", "author_role", "recorded_at", "text")
        _validate_keys(data, required=fields, allowed=fields + ("source_locator",), object_name="evidence")
        recorded_at = _nonempty_string(data["recorded_at"], "evidence.recorded_at")
        _parse_iso_datetime(recorded_at, "evidence.recorded_at")
        source_locator = data.get("source_locator")
        if source_locator is not None:
            source_locator = _mapping(source_locator, "evidence.source_locator")
            locator_fields = (
                "adapter_id", "adapter_version", "resource", "path", "row_number",
                "source_record_id", "field_names",
            )
            _validate_keys(
                source_locator,
                required=locator_fields,
                allowed=locator_fields + ("sheet",),
                object_name="evidence.source_locator",
            )
            for name in ("adapter_id", "adapter_version", "resource", "path", "source_record_id"):
                _nonempty_string(source_locator[name], f"evidence.source_locator.{name}")
            locator_path = str(source_locator["path"])
            if locator_path.startswith(("/", "~")) or ".." in locator_path.split("/") or "\\" in locator_path:
                raise ValueError("evidence.source_locator.path must be a safe relative path")
            row_number = source_locator["row_number"]
            if isinstance(row_number, bool) or not isinstance(row_number, int) or row_number <= 0:
                raise ValueError("evidence.source_locator.row_number must be a positive integer")
            locator_fields = _unique_strings(source_locator["field_names"], "evidence.source_locator.field_names")
            if not locator_fields:
                raise ValueError("evidence.source_locator.field_names must not be empty")
            if "sheet" in source_locator:
                _nonempty_string(source_locator["sheet"], "evidence.source_locator.sheet")
        return cls(
            evidence_id=_nonempty_string(data["evidence_id"], "evidence.evidence_id"),
            document_id=_nonempty_string(data["document_id"], "evidence.document_id"),
            author_role=_nonempty_string(data["author_role"], "evidence.author_role"),
            recorded_at=recorded_at,
            text=_nonempty_string(data["text"], "evidence.text"),
            source_locator=dict(source_locator) if source_locator is not None else None,
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
class SizeMeasurement:
    """A structured wound dimension (centimetres). All arithmetic on measurements is
    deterministic Python — a language model never computes area, change, or trend."""

    length_cm: float
    width_cm: float
    depth_cm: float | None = None

    def __post_init__(self) -> None:
        for name in ("length_cm", "width_cm"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"size_measurement.{name} must be a non-negative number")
        if self.depth_cm is not None and (
            isinstance(self.depth_cm, bool) or not isinstance(self.depth_cm, (int, float)) or self.depth_cm < 0
        ):
            raise ValueError("size_measurement.depth_cm must be a non-negative number when present")

    @property
    def area_cm2(self) -> float:
        """Planar area (length x width). Deterministic; used by temporal/pct-change analytics."""
        return float(self.length_cm) * float(self.width_cm)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SizeMeasurement":
        _validate_keys(
            data,
            required=("length_cm", "width_cm"),
            allowed=("length_cm", "width_cm", "depth_cm"),
            object_name="size_measurement",
        )
        depth = data.get("depth_cm")
        return cls(
            length_cm=_number(data["length_cm"], "size_measurement.length_cm"),
            width_cm=_number(data["width_cm"], "size_measurement.width_cm"),
            depth_cm=None if depth is None else _number(depth, "size_measurement.depth_cm"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"length_cm": self.length_cm, "width_cm": self.width_cm}
        if self.depth_cm is not None:
            payload["depth_cm"] = self.depth_cm
        return payload


@dataclass(frozen=True, slots=True)
class WoundAssessment:
    """One dated point in a wound's longitudinal timeline.

    ``observed_at`` is the grounded Day-0 anchor a temporal operator reads; ``compared_with_id``
    links to the prior assessment (the ontology ``comparedWith`` relation) so deterministic
    Python can compute size trends across the series. ``subject_entity_id`` optionally binds the
    assessment to a ``WoundAssessment`` ontology entity so a rule scoped to that entity can read
    the engine-derived longitudinal facts. All fields are evidence-grounded.
    """

    assessment_id: str
    observed_at: str
    size: SizeMeasurement | None = None
    tissue: str | None = None
    exudate: str | None = None
    periwound: str | None = None
    standard_care_documented: bool = False
    provider_reassessment: bool = False
    compared_with_id: str | None = None
    subject_entity_id: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)
    evidence_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WoundAssessment":
        required = ("assessment_id", "observed_at", "evidence_ids")
        allowed = required + (
            "size", "tissue", "exudate", "periwound", "standard_care_documented",
            "provider_reassessment", "compared_with_id", "subject_entity_id", "attributes",
        )
        _validate_keys(data, required=required, allowed=allowed, object_name="wound_assessment")
        observed_at = _nonempty_string(data["observed_at"], "wound_assessment.observed_at")
        _parse_iso_datetime(observed_at, "wound_assessment.observed_at")
        size_payload = data.get("size")
        size = (
            SizeMeasurement.from_dict(_mapping(size_payload, "wound_assessment.size"))
            if size_payload is not None
            else None
        )
        for flag in ("standard_care_documented", "provider_reassessment"):
            if flag in data and not isinstance(data[flag], bool):
                raise ValueError(f"wound_assessment.{flag} must be a boolean")
        attributes = data.get("attributes", {})
        if not isinstance(attributes, Mapping):
            raise ValueError("wound_assessment.attributes must be an object")
        return cls(
            assessment_id=_nonempty_string(data["assessment_id"], "wound_assessment.assessment_id"),
            observed_at=observed_at,
            size=size,
            tissue=_optional_string(data.get("tissue"), "wound_assessment.tissue"),
            exudate=_optional_string(data.get("exudate"), "wound_assessment.exudate"),
            periwound=_optional_string(data.get("periwound"), "wound_assessment.periwound"),
            standard_care_documented=bool(data.get("standard_care_documented", False)),
            provider_reassessment=bool(data.get("provider_reassessment", False)),
            compared_with_id=_optional_string(data.get("compared_with_id"), "wound_assessment.compared_with_id"),
            subject_entity_id=_optional_string(data.get("subject_entity_id"), "wound_assessment.subject_entity_id"),
            attributes=dict(attributes),
            evidence_ids=_unique_strings(data["evidence_ids"], "wound_assessment.evidence_ids"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "assessment_id": self.assessment_id,
            "observed_at": self.observed_at,
            "evidence_ids": list(self.evidence_ids),
        }
        if self.size is not None:
            payload["size"] = self.size.to_dict()
        for name in ("tissue", "exudate", "periwound", "compared_with_id", "subject_entity_id"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        if self.standard_care_documented:
            payload["standard_care_documented"] = True
        if self.provider_reassessment:
            payload["provider_reassessment"] = True
        if self.attributes:
            payload["attributes"] = dict(self.attributes)
        return payload


@dataclass(frozen=True, slots=True)
class EpisodeRecord:
    """A care episode spanning one or more encounters. Optional; a legacy single-encounter
    case omits it entirely. Timing math over the episode is deterministic Python only."""

    episode_id: str
    patient_id: str
    episode_start: str
    episode_end: str | None = None
    encounter_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EpisodeRecord":
        required = ("episode_id", "patient_id", "episode_start")
        allowed = required + ("episode_end", "encounter_ids")
        _validate_keys(data, required=required, allowed=allowed, object_name="episode")
        episode_start = _nonempty_string(data["episode_start"], "episode.episode_start")
        start = _parse_iso_datetime(episode_start, "episode.episode_start")
        episode_end = data.get("episode_end")
        if episode_end is not None:
            episode_end = _nonempty_string(episode_end, "episode.episode_end")
            end = _parse_iso_datetime(episode_end, "episode.episode_end")
            if start > end:
                raise ValueError("episode.episode_start must not be after episode.episode_end")
        return cls(
            episode_id=_nonempty_string(data["episode_id"], "episode.episode_id"),
            patient_id=_nonempty_string(data["patient_id"], "episode.patient_id"),
            episode_start=episode_start,
            episode_end=episode_end,
            encounter_ids=_unique_strings(data.get("encounter_ids", []), "episode.encounter_ids"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "episode_id": self.episode_id,
            "patient_id": self.patient_id,
            "episode_start": self.episode_start,
        }
        if self.episode_end is not None:
            payload["episode_end"] = self.episode_end
        if self.encounter_ids:
            payload["encounter_ids"] = list(self.encounter_ids)
        return payload


POA_VALUES = frozenset({"Y", "N", "U", "W"})


@dataclass(frozen=True, slots=True)
class DiagnosisDetail:
    """Per-diagnosis coding fidelity: sequence (1 = principal) and present-on-admission indicator."""

    code: str
    sequence: int
    poa: str

    def __post_init__(self) -> None:
        _nonempty_string(self.code, "diagnosis_detail.code")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence <= 0:
            raise ValueError("diagnosis_detail.sequence must be a positive integer")
        if self.poa not in POA_VALUES:
            raise ValueError("diagnosis_detail.poa must be one of Y, N, U, W")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DiagnosisDetail":
        _validate_keys(data, required=("code", "sequence", "poa"), allowed=("code", "sequence", "poa"), object_name="diagnosis_detail")
        return cls(code=_nonempty_string(data["code"], "diagnosis_detail.code"), sequence=data["sequence"], poa=str(data["poa"]))

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "sequence": self.sequence, "poa": self.poa}


@dataclass(frozen=True, slots=True)
class Claim:
    diagnoses: tuple[str, ...]
    procedures: tuple[str, ...]
    charges: tuple[str, ...]
    drg: str | None = None
    allowed_amount_cents: int | None = None
    # Additive, optional fidelity (backward-compatible; absent on legacy 2.0.0 cases).
    diagnosis_details: tuple[DiagnosisDetail, ...] = ()
    charge_lines: tuple[ClaimLine, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Claim":
        required = ("diagnoses", "procedures", "charges")
        allowed = required + ("drg", "allowed_amount_cents", "diagnosis_details", "charge_lines")
        _validate_keys(data, required=required, allowed=allowed, object_name="claim")
        amount = data.get("allowed_amount_cents")
        if amount is not None and (isinstance(amount, bool) or not isinstance(amount, int) or amount < 0):
            raise ValueError("claim.allowed_amount_cents must be a non-negative integer")
        drg = data.get("drg")
        if drg is not None:
            drg = _nonempty_string(drg, "claim.drg")
        diagnoses = _unique_strings(data["diagnoses"], "claim.diagnoses")

        details = tuple(
            DiagnosisDetail.from_dict(_mapping(item, "claim.diagnosis_details item"))
            for item in _list(data.get("diagnosis_details", []), "claim.diagnosis_details")
        )
        sequences = [detail.sequence for detail in details]
        if len(sequences) != len(set(sequences)):
            raise ValueError("claim.diagnosis_details sequences must be unique")
        if unknown := {detail.code for detail in details} - set(diagnoses):
            raise ValueError(f"claim.diagnosis_details reference codes absent from diagnoses: {sorted(unknown)}")

        charge_lines = tuple(
            ClaimLine.from_dict(_mapping(item, "claim.charge_lines item"))
            for item in _list(data.get("charge_lines", []), "claim.charge_lines")
        )
        line_ids = [line.line_id for line in charge_lines]
        if len(line_ids) != len(set(line_ids)):
            raise ValueError("claim.charge_lines line IDs must be unique")

        return cls(
            diagnoses=diagnoses,
            procedures=_unique_strings(data["procedures"], "claim.procedures"),
            charges=_unique_strings(data["charges"], "claim.charges"),
            drg=drg,
            allowed_amount_cents=amount,
            diagnosis_details=details,
            charge_lines=charge_lines,
        )

    def principal_diagnosis(self) -> str | None:
        """The sequence-1 diagnosis, when diagnosis_details are present."""
        for detail in self.diagnosis_details:
            if detail.sequence == 1:
                return detail.code
        return None

    def charges_from_lines(self) -> tuple[str, ...]:
        """Deterministic reduction of charge_lines to ordered unique charge codes."""
        return tuple(dict.fromkeys(line.code for line in self.charge_lines))


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
    # Additive, optional payer-side context (backward-compatible; absent on legacy 2.0.0 cases).
    financial: FinancialSnapshot | None = None
    # Additive, optional encounter-lifecycle position. Defaults to retrospective so legacy
    # cases (which omit the field) route exactly as before.
    lifecycle_state: LifecycleState = LifecycleState.RETROSPECTIVE
    # Additive, optional longitudinal timeline. ``assessments`` is a time-ordered series of
    # dated wound assessments (each optionally linked to the prior via comparedWith); ``episode``
    # frames the encounter(s) in a care episode. Both are empty/None on legacy 2.0.0 cases, so
    # existing single-encounter cases parse and serialize exactly as before.
    assessments: tuple[WoundAssessment, ...] = ()
    episode: EpisodeRecord | None = None

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
        allowed = required + ("metadata", "financial", "lifecycle_state", "assessments", "episode", "documents")
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
        financial_payload = data.get("financial")
        financial = (
            FinancialSnapshot.from_dict(_mapping(financial_payload, "case.financial"))
            if financial_payload is not None
            else None
        )
        lifecycle_raw = data.get("lifecycle_state")
        if lifecycle_raw is None:
            lifecycle_state = LifecycleState.RETROSPECTIVE
        else:
            try:
                lifecycle_state = LifecycleState(lifecycle_raw)
            except ValueError as exc:
                raise ValueError(
                    "case.lifecycle_state must be one of "
                    f"{sorted(state.value for state in LifecycleState)}"
                ) from exc
        assessments = tuple(
            WoundAssessment.from_dict(_mapping(item, "case.assessments item"))
            for item in _list(data.get("assessments", []), "case.assessments")
        )
        episode_payload = data.get("episode")
        episode = (
            EpisodeRecord.from_dict(_mapping(episode_payload, "case.episode"))
            if episode_payload is not None
            else None
        )
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
            financial=financial,
            lifecycle_state=lifecycle_state,
            assessments=assessments,
            episode=episode,
        )
        if case.schema_version != SUPPORTED_SCHEMA_VERSION:
            raise ValueError(f"unsupported case schema_version: {case.schema_version}")
        if case.provenance.schema_version != case.schema_version:
            raise ValueError("case and provenance schema versions must match")
        case.validate_lineage()
        case.validate_financial_lineage()
        case.validate_longitudinal_lineage()
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
        ingestion = self.provenance.ingestion
        for evidence in self.evidence:
            locator = evidence.source_locator
            if locator is None:
                continue
            if ingestion is None:
                raise ValueError(f"evidence {evidence.evidence_id} has a source locator without ingestion provenance")
            if (
                locator["adapter_id"] != ingestion["adapter_id"]
                or locator["adapter_version"] != ingestion["adapter_version"]
            ):
                raise ValueError(f"evidence {evidence.evidence_id} source locator does not match ingestion provenance")
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

    def validate_longitudinal_lineage(self) -> None:
        """Fail closed on a malformed longitudinal timeline.

        Assessment IDs must be unique; every cited evidence_id must resolve to case evidence;
        each ``compared_with_id`` must reference another assessment in the series; each
        ``subject_entity_id`` must reference a known ontology entity; and the series must be
        time-ordered by ``observed_at`` (the grounded Day-0 anchor temporal operators read).
        Legacy cases with no ``assessments`` are unaffected (the loop body never runs).
        """
        if not self.assessments:
            return
        known_evidence = {item.evidence_id for item in self.evidence}
        entity_ids = {entity.entity_id for entity in self.ontology.entities}
        assessment_ids = [a.assessment_id for a in self.assessments]
        if len(assessment_ids) != len(set(assessment_ids)):
            raise ValueError("assessment_id values must be unique")
        known_assessments = set(assessment_ids)
        previous: datetime | None = None
        for assessment in self.assessments:
            if not assessment.evidence_ids:
                raise ValueError(f"assessment {assessment.assessment_id} must cite supporting evidence")
            if unknown := set(assessment.evidence_ids) - known_evidence:
                raise ValueError(
                    f"assessment {assessment.assessment_id} references unknown evidence: {sorted(unknown)}"
                )
            if assessment.compared_with_id is not None:
                if assessment.compared_with_id == assessment.assessment_id:
                    raise ValueError(
                        f"assessment {assessment.assessment_id} compared_with_id must reference another assessment"
                    )
                if assessment.compared_with_id not in known_assessments:
                    raise ValueError(
                        f"assessment {assessment.assessment_id} compared_with_id references unknown assessment "
                        f"{assessment.compared_with_id}"
                    )
            if assessment.subject_entity_id is not None and assessment.subject_entity_id not in entity_ids:
                raise ValueError(
                    f"assessment {assessment.assessment_id} subject_entity_id references unknown ontology entity "
                    f"{assessment.subject_entity_id}"
                )
            observed = _parse_iso_datetime(assessment.observed_at, f"assessment {assessment.assessment_id} observed_at")
            if previous is not None and observed < previous:
                raise ValueError("case.assessments must be ordered by non-decreasing observed_at")
            previous = observed
        if self.episode is not None and self.episode.encounter_ids:
            if self.encounter_id not in self.episode.encounter_ids:
                raise ValueError("case.episode.encounter_ids must include the case encounter_id")

    def validate_financial_lineage(self) -> None:
        """Fail closed when payer-side denial references cannot resolve to a claim charge line.

        Only enforced when both a financial snapshot and claim charge lines are present; every
        Denial.line_id and every Remittance denial reference must resolve to a known line/denial.
        Legacy cases without financial context (or without charge_lines) are unaffected.
        """
        if self.financial is None:
            return
        if not self.claim.charge_lines:
            return
        known_line_ids = {line.line_id for line in self.claim.charge_lines}
        known_denial_ids = {denial.denial_id for denial in self.financial.denials}
        for denial in self.financial.denials:
            if unknown := set(denial.line_ids) - known_line_ids:
                raise ValueError(
                    f"denial {denial.denial_id} references unknown charge line(s): {sorted(unknown)}"
                )
        for remittance in self.financial.remittances:
            if unknown := set(remittance.denial_ids) - known_denial_ids:
                raise ValueError(
                    f"remittance {remittance.remittance_id} references unknown denial(s): {sorted(unknown)}"
                )


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
    estimated_impact_cents: int | None
    impact_status: ImpactStatus
    grouper_version: str
    derivation: Mapping[str, Any] = field(default_factory=dict)
    # Additive, read-only line-level lineage: the claim charge lines this finding is bound
    # to (empty for findings not tied to specific charge lines). Never model-supplied.
    charge_line_refs: tuple[str, ...] = ()
    # ---- clinical_care_gap domain (all OPTIONAL; None/empty on revenue_integrity findings) ----
    # These are set only for findings emitted from the walled-off clinical_care_gap domain.
    # They carry analytics signal for clinicians; they never mutate a claim or bypass review.
    gap_domain: "GapDomain | None" = None
    expected_action: str | None = None
    actual_action: str | None = None
    timing_window_days: int | float | None = None
    alert_urgency: "ClinicalUrgency | None" = None
    recommended_action: str | None = None
    clinical_impact: str | None = None
    # Each item: {"exception_type": ExceptionType, "evidence_id": str, "status": str}
    exception_checks: tuple[Mapping[str, Any], ...] = ()
    gap_status: "GapStatus | None" = None
    closed_at: str | None = None
    barrier_code: str | None = None

    def is_clinical_care_gap(self) -> bool:
        return self.gap_domain is not None

    def __post_init__(self) -> None:
        if self.impact_status is ImpactStatus.ESTIMATED and self.estimated_impact_cents is None:
            raise ValueError("estimated finding impact requires estimated_impact_cents")
        if self.impact_status in {ImpactStatus.UNAVAILABLE, ImpactStatus.NOT_APPLICABLE} and self.estimated_impact_cents is not None:
            raise ValueError("unavailable or not-applicable finding impact cannot carry an estimate")
        if self.estimated_impact_cents is not None and (
            isinstance(self.estimated_impact_cents, bool)
            or not isinstance(self.estimated_impact_cents, int)
        ):
            raise ValueError("finding estimated_impact_cents must be an integer or null")
        # Clinical-care-gap semantics: analytics identify gaps, clinicians decide.
        if self.is_clinical_care_gap():
            if not self.requires_human_review:
                raise ValueError("clinical_care_gap findings must require human review")
            if dict(self.proposed_change):
                raise ValueError("clinical_care_gap findings must not carry a claim-mutating proposed change")
            # gap_status defaults to open for a clinical-care-gap finding.
            if self.gap_status is None:
                object.__setattr__(self, "gap_status", GapStatus.OPEN)
        elif self.gap_status is not None:
            raise ValueError("gap_status is only valid on clinical_care_gap findings")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
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
            "impact_status": self.impact_status.value,
            "grouper_version": self.grouper_version,
            "derivation": {key: list(value) for key, value in self.derivation.items()},
            "charge_line_refs": list(self.charge_line_refs),
        }
        # Emit clinical_care_gap fields only when set, so revenue_integrity findings and
        # their serialized shape are byte-for-byte unchanged (backward-compatible contract).
        if self.gap_domain is not None:
            payload["gap_domain"] = self.gap_domain.value
        if self.expected_action is not None:
            payload["expected_action"] = self.expected_action
        if self.actual_action is not None:
            payload["actual_action"] = self.actual_action
        if self.timing_window_days is not None:
            payload["timing_window_days"] = self.timing_window_days
        if self.alert_urgency is not None:
            payload["alert_urgency"] = self.alert_urgency.value
        if self.recommended_action is not None:
            payload["recommended_action"] = self.recommended_action
        if self.clinical_impact is not None:
            payload["clinical_impact"] = self.clinical_impact
        if self.exception_checks:
            payload["exception_checks"] = [
                {
                    "exception_type": (
                        check["exception_type"].value
                        if isinstance(check.get("exception_type"), ExceptionType)
                        else check.get("exception_type")
                    ),
                    "evidence_id": check.get("evidence_id"),
                    "status": check.get("status"),
                }
                for check in self.exception_checks
            ]
        if self.gap_status is not None:
            payload["gap_status"] = self.gap_status.value
        if self.closed_at is not None:
            payload["closed_at"] = self.closed_at
        if self.barrier_code is not None:
            payload["barrier_code"] = self.barrier_code
        return payload


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


def _optional_string(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _nonempty_string(value, name)


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
