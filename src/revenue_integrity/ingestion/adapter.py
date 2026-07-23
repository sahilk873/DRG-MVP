from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..ontology import OntologyDefinition, OntologyGraph
from .models import (
    AdapterDefinition,
    CollectionBinding,
    DegradationPolicy,
    Expression,
    IngestionPolicy,
    ResourceDefinition,
    RowCondition,
    StructuredProjection,
)
from .profiling import BulkProfile, profile_directory
from .readers import ReaderRegistry, default_reader_registry
from .transforms import evaluate, evaluate_required_string


ADAPTER_RUNTIME_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class AdapterRunReport:
    adapter_id: str
    adapter_version: str
    source_schema_fingerprint: str
    input_manifest_digest: str
    input_rows_by_resource: Mapping[str, int]
    output_cases: int
    output_documents: int
    output_evidence: int
    output_entities: int
    output_relations: int
    output_assertions: int
    quarantined: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "adapter_runtime_version": ADAPTER_RUNTIME_VERSION,
            "source_schema_fingerprint": self.source_schema_fingerprint,
            "input_manifest_digest": self.input_manifest_digest,
            "input_rows_by_resource": dict(self.input_rows_by_resource),
            "output_cases": self.output_cases,
            "output_documents": self.output_documents,
            "output_evidence": self.output_evidence,
            "output_entities": self.output_entities,
            "output_relations": self.output_relations,
            "output_assertions": self.output_assertions,
            "quarantined": [dict(item) for item in self.quarantined],
            "quarantined_count": len(self.quarantined),
            "status": "succeeded",
        }


class _Quarantine:
    """Collects recoverable per-row/per-encounter failures when degradation mode is 'quarantine'."""

    def __init__(self, policy: DegradationPolicy) -> None:
        self.active = policy.mode == "quarantine"
        self._max = policy.max_quarantined
        self.records: list[dict[str, Any]] = []
        self.ids: set[str] = set()

    def record(self, reason: str, location: str, encounter_id: str, detail: str) -> None:
        self.records.append({"reason": reason, "location": location, "encounter_id": encounter_id, "detail": detail})
        if len(self.records) > self._max:
            raise ValueError(
                f"quarantine circuit breaker tripped: {len(self.records)} rows exceeded max_quarantined {self._max}"
            )

    def quarantine_encounter(self, encounter_id: str, reason: str, location: str, detail: str) -> None:
        self.ids.add(encounter_id)
        self.record(reason, location, encounter_id, detail)

    def is_quarantined(self, encounter_id: str) -> bool:
        return encounter_id in self.ids


@dataclass(frozen=True, slots=True)
class AdapterRunResult:
    source_bundles: tuple[Mapping[str, Any], ...]
    report: AdapterRunReport


def run_adapter(
    input_directory: str | Path,
    adapter: AdapterDefinition,
    ontology_definition: OntologyDefinition,
    *,
    policy: IngestionPolicy | None = None,
    registry: ReaderRegistry | None = None,
    now: Callable[[], datetime] | None = None,
    degradation: DegradationPolicy | None = None,
) -> AdapterRunResult:
    """Execute an approved adapter without invoking a model or generated code."""

    quarantine = _Quarantine(degradation or DegradationPolicy())
    if adapter.status not in {"approved-for-demo", "approved"}:
        raise ValueError("adapter must be approved before deterministic execution")
    if (
        adapter.ontology.ontology_id != ontology_definition.ontology_id
        or adapter.ontology.version != ontology_definition.version
        or adapter.ontology.digest != ontology_definition.digest
    ):
        raise ValueError("adapter ontology binding does not match the configured ontology definition")

    root = Path(input_directory).resolve(strict=True)
    resolved_policy = policy or IngestionPolicy()
    resolved_registry = registry or default_reader_registry()
    profile = profile_directory(root, resolved_policy, resolved_registry)
    if profile.schema_fingerprint != adapter.source_schema_fingerprint:
        raise ValueError(
            "source schema drift detected: adapter fingerprint "
            f"{adapter.source_schema_fingerprint}, input fingerprint {profile.schema_fingerprint}"
        )
    _validate_expression_fields(adapter, profile)

    cache = {
        name: tuple(resolved_registry.iter_rows(root, resource, resolved_policy))
        for name, resource in adapter.resources.items()
    }
    encounter_rows = tuple(row for row in cache[adapter.encounter.resource] if _matches(row, adapter.encounter.where))
    if len(encounter_rows) > resolved_policy.max_output_cases:
        raise ValueError(f"adapter output exceeds max_output_cases ({resolved_policy.max_output_cases})")

    cases: dict[str, dict[str, Any]] = {}
    for row in encounter_rows:
        encounter_id = evaluate_required_string(adapter.encounter.encounter_id, row, "encounter.encounter_id")
        if encounter_id in cases:
            if quarantine.active:
                quarantine.record("duplicate_encounter", "encounter", encounter_id, "duplicate encounter_id; first row kept")
                continue
            raise ValueError(f"duplicate encounter ID produced by adapter: {encounter_id}")
        bundle = {
            "case_id": evaluate_required_string(adapter.encounter.case_id, row, "encounter.case_id"),
            "patient_id": evaluate_required_string(adapter.encounter.patient_id, row, "encounter.patient_id"),
            "encounter_id": encounter_id,
            "admitted_at": _iso_datetime(evaluate(adapter.encounter.admitted_at, row, "encounter.admitted_at"), "encounter.admitted_at"),
            "discharged_at": _iso_datetime(evaluate(adapter.encounter.discharged_at, row, "encounter.discharged_at"), "encounter.discharged_at"),
            "metadata": {
                key: evaluate(expression, row, f"encounter.metadata.{key}")
                for key, expression in adapter.encounter.metadata.items()
            },
            "documents": [],
            "claim": {"diagnoses": [], "procedures": [], "charges": []},
            "structured_extraction": _empty_extraction(ontology_definition),
        }
        if datetime.fromisoformat(bundle["admitted_at"]) > datetime.fromisoformat(bundle["discharged_at"]):
            if quarantine.active:
                quarantine.record("admission_after_discharge", "encounter", encounter_id, "admission occurs after discharge")
                continue
            raise ValueError(f"encounter {encounter_id} admission occurs after discharge")
        cases[encounter_id] = bundle

    _attach_documents(cases, adapter, cache, quarantine)
    _attach_claims(cases, adapter, cache, quarantine)
    _attach_structured_extractions(cases, adapter, cache, ontology_definition, quarantine)

    for quarantined_id in quarantine.ids:
        cases.pop(quarantined_id, None)

    transformed = (now or (lambda: datetime.now(timezone.utc)))()
    if transformed.tzinfo is None:
        raise ValueError("adapter transform clock must return a timezone-aware datetime")
    transformed_at = transformed.isoformat()
    for bundle in cases.values():
        bundle["ingestion_provenance"] = {
            "framework": "deterministic-adapter",
            "adapter_id": adapter.adapter_id,
            "adapter_version": adapter.version,
            "source_schema_fingerprint": profile.schema_fingerprint,
            "input_manifest_digest": profile.input_manifest_digest,
            "transformed_at": transformed_at,
            "runtime_version": ADAPTER_RUNTIME_VERSION,
        }

    bundles = tuple(cases[key] for key in sorted(cases))
    report = AdapterRunReport(
        adapter_id=adapter.adapter_id,
        adapter_version=adapter.version,
        source_schema_fingerprint=profile.schema_fingerprint,
        input_manifest_digest=profile.input_manifest_digest,
        input_rows_by_resource={name: len(rows) for name, rows in sorted(cache.items())},
        output_cases=len(bundles),
        output_documents=sum(len(bundle["documents"]) for bundle in bundles),
        output_evidence=sum(len(bundle["structured_extraction"]["evidence"]) for bundle in bundles),
        output_entities=sum(len(bundle["structured_extraction"]["ontology"]["entities"]) for bundle in bundles),
        output_relations=sum(len(bundle["structured_extraction"]["ontology"]["relations"]) for bundle in bundles),
        output_assertions=sum(len(bundle["structured_extraction"]["assertions"]) for bundle in bundles),
        quarantined=tuple(quarantine.records),
    )
    return AdapterRunResult(bundles, report)


def _attach_documents(
    cases: Mapping[str, dict[str, Any]],
    adapter: AdapterDefinition,
    cache: Mapping[str, tuple[Mapping[str, Any], ...]],
    quarantine: "_Quarantine",
) -> None:
    known_document_ids: dict[str, set[str]] = {encounter_id: set() for encounter_id in cases}
    for binding_index, binding in enumerate(adapter.documents):
        for row in cache[binding.resource]:
            if not _matches(row, binding.where):
                continue
            location = f"documents[{binding_index}]"
            encounter_id = evaluate_required_string(binding.encounter_id, row, f"{location}.encounter_id")
            bundle = _known_case(cases, encounter_id, location, quarantine)
            if bundle is None:
                continue
            document_id = evaluate_required_string(binding.document_id, row, f"{location}.document_id")
            if document_id in known_document_ids[encounter_id]:
                if quarantine.active:
                    quarantine.record("duplicate_document", location, encounter_id, f"duplicate document {document_id}")
                    continue
                raise ValueError(f"duplicate document ID for encounter {encounter_id}: {document_id}")
            known_document_ids[encounter_id].add(document_id)
            bundle["documents"].append({
                "document_id": document_id,
                "author_role": evaluate_required_string(binding.author_role, row, f"{location}.author_role"),
                "recorded_at": _iso_datetime(evaluate(binding.recorded_at, row, f"{location}.recorded_at"), f"{location}.recorded_at"),
                "text": evaluate_required_string(binding.text, row, f"{location}.text"),
            })


def _attach_claims(
    cases: Mapping[str, dict[str, Any]],
    adapter: AdapterDefinition,
    cache: Mapping[str, tuple[Mapping[str, Any], ...]],
    quarantine: "_Quarantine",
) -> None:
    claimed: set[str] = set()
    for row in cache[adapter.claim.resource]:
        if not _matches(row, adapter.claim.where):
            continue
        encounter_id = evaluate_required_string(adapter.claim.encounter_id, row, "claim.encounter_id")
        bundle = _known_case(cases, encounter_id, "claim", quarantine)
        if bundle is None:
            continue
        if encounter_id in claimed:
            if quarantine.active:
                quarantine.quarantine_encounter(encounter_id, "multiple_claims", "claim", "encounter has more than one claim row")
                continue
            raise ValueError(f"multiple claim rows linked to encounter {encounter_id}")
        claimed.add(encounter_id)
        if adapter.claim.drg is not None:
            bundle["claim"]["drg"] = _optional_string(evaluate(adapter.claim.drg, row, "claim.drg"), "claim.drg")
        if adapter.claim.allowed_amount_cents is not None:
            amount = evaluate(adapter.claim.allowed_amount_cents, row, "claim.allowed_amount_cents")
            if amount is not None and (isinstance(amount, bool) or not isinstance(amount, int) or amount < 0):
                raise ValueError("claim.allowed_amount_cents must produce a non-negative integer or null")
            bundle["claim"]["allowed_amount_cents"] = amount
    if missing := set(cases) - claimed - quarantine.ids:
        if quarantine.active:
            for encounter_id in sorted(missing):
                quarantine.quarantine_encounter(encounter_id, "no_claim", "claim", "encounter has no claim row")
        else:
            raise ValueError(f"encounters without exactly one claim row: {sorted(missing)}")
    for field_name in ("diagnoses", "procedures", "charges"):
        binding = getattr(adapter.claim, field_name)
        if binding is not None:
            _attach_collection(cases, cache, binding, field_name, quarantine)


def _attach_collection(
    cases: Mapping[str, dict[str, Any]],
    cache: Mapping[str, tuple[Mapping[str, Any], ...]],
    binding: CollectionBinding,
    field_name: str,
    quarantine: "_Quarantine",
) -> None:
    for row in cache[binding.resource]:
        if not _matches(row, binding.where):
            continue
        encounter_id = evaluate_required_string(binding.encounter_id, row, f"claim.{field_name}.encounter_id")
        bundle = _known_case(cases, encounter_id, f"claim.{field_name}", quarantine)
        if bundle is None:
            continue
        value = evaluate_required_string(binding.value, row, f"claim.{field_name}.value")
        if value not in bundle["claim"][field_name]:
            bundle["claim"][field_name].append(value)


def _attach_structured_extractions(
    cases: Mapping[str, dict[str, Any]],
    adapter: AdapterDefinition,
    cache: Mapping[str, tuple[Mapping[str, Any], ...]],
    ontology_definition: OntologyDefinition,
    quarantine: "_Quarantine",
) -> None:
    for projection in adapter.structured_projections:
        resource = adapter.resources[projection.resource]
        for row in cache[projection.resource]:
            if not _matches(row, projection.where):
                continue
            location = f"projection {projection.projection_id} row {row['_row_number']}"
            encounter_id = evaluate_required_string(projection.encounter_id, row, f"{location}.encounter_id")
            bundle = _known_case(cases, encounter_id, location, quarantine)
            if bundle is None:
                continue
            extraction = bundle["structured_extraction"]
            source_record_id = evaluate_required_string(projection.source_record_id, row, f"{location}.source_record_id")
            evidence_id = evaluate_required_string(projection.evidence.evidence_id, row, f"{location}.evidence_id")
            locator = {
                "adapter_id": adapter.adapter_id,
                "adapter_version": adapter.version,
                "resource": projection.resource,
                "path": resource.path,
                "row_number": row["_row_number"],
                "source_record_id": source_record_id,
                "field_names": list(projection.evidence.field_names),
            }
            if resource.sheet is not None:
                locator["sheet"] = resource.sheet
            evidence = {
                "evidence_id": evidence_id,
                "document_id": evaluate_required_string(projection.evidence.document_id, row, f"{location}.document_id"),
                "author_role": evaluate_required_string(projection.evidence.author_role, row, f"{location}.author_role"),
                "recorded_at": _iso_datetime(evaluate(projection.evidence.recorded_at, row, f"{location}.recorded_at"), f"{location}.recorded_at"),
                "text": evaluate_required_string(projection.evidence.text, row, f"{location}.text"),
                "source_locator": locator,
            }
            _upsert(extraction["evidence"], evidence, "evidence_id", location)
            for entity_projection in projection.entities:
                entity = {
                    "entity_id": evaluate_required_string(entity_projection.entity_id, row, f"{location}.entity_id"),
                    "entity_type": entity_projection.entity_type,
                    "label": evaluate_required_string(entity_projection.label, row, f"{location}.entity.label"),
                    "properties": {
                        key: evaluate(expression, row, f"{location}.entity.properties.{key}")
                        for key, expression in entity_projection.properties.items()
                    },
                }
                _upsert(extraction["ontology"]["entities"], entity, "entity_id", location)
            for relation_projection in projection.relations:
                relation = {
                    "relation_id": evaluate_required_string(relation_projection.relation_id, row, f"{location}.relation_id"),
                    "predicate": relation_projection.predicate,
                    "source_id": evaluate_required_string(relation_projection.source_id, row, f"{location}.relation.source_id"),
                    "target_id": evaluate_required_string(relation_projection.target_id, row, f"{location}.relation.target_id"),
                    "assertion_status": relation_projection.assertion_status,
                    "documentation_status": relation_projection.documentation_status,
                    "confidence": relation_projection.confidence,
                    "evidence_ids": [evidence_id] if relation_projection.cite_evidence else [],
                    "contradicting_evidence_ids": [],
                }
                _upsert(extraction["ontology"]["relations"], relation, "relation_id", location)
            for assertion_projection in projection.assertions:
                assertion = {
                    "assertion_id": evaluate_required_string(assertion_projection.assertion_id, row, f"{location}.assertion_id"),
                    "subject_id": evaluate_required_string(assertion_projection.subject_id, row, f"{location}.assertion.subject_id"),
                    "concept": assertion_projection.concept,
                    "status": assertion_projection.status,
                    "documentation_status": assertion_projection.documentation_status,
                    "confidence": assertion_projection.confidence,
                    "attributes": {
                        key: evaluate(expression, row, f"{location}.assertion.attributes.{key}")
                        for key, expression in assertion_projection.attributes.items()
                    },
                    "evidence_ids": [evidence_id],
                    "contradicting_evidence_ids": [],
                }
                _upsert(extraction["assertions"], assertion, "assertion_id", location)

    for encounter_id, bundle in cases.items():
        if quarantine.is_quarantined(encounter_id):
            continue
        extraction = bundle["structured_extraction"]
        full_graph = {
            **extraction["ontology"],
            "entities": [*_structural_entities(ontology_definition), *extraction["ontology"]["entities"]],
            "relations": [*_structural_relations(ontology_definition), *extraction["ontology"]["relations"]],
        }
        graph = OntologyGraph.from_dict(full_graph)
        evidence_ids = {item["evidence_id"] for item in extraction["evidence"]}
        ontology_definition.validate_graph(graph, evidence_ids)
        entity_ids = {item["entity_id"] for item in full_graph["entities"]}
        for assertion in extraction["assertions"]:
            if assertion["subject_id"] not in entity_ids:
                raise ValueError(f"assertion {assertion['assertion_id']} has unknown subject in encounter {encounter_id}")
            if unknown := set(assertion["evidence_ids"]) - evidence_ids:
                raise ValueError(f"assertion {assertion['assertion_id']} has unknown evidence: {sorted(unknown)}")


def _empty_extraction(definition: OntologyDefinition) -> dict[str, Any]:
    return {
        "evidence": [],
        "ontology": {
            "ontology_id": definition.ontology_id,
            "ontology_version": definition.version,
            "ontology_digest": definition.digest,
            "entities": [],
            "relations": [],
        },
        "assertions": [],
    }


def _structural_entities(definition: OntologyDefinition) -> Iterable[dict[str, Any]]:
    for item in definition.structural_graph.entities:
        result: dict[str, Any] = {
            "entity_id": item.entity_id,
            "entity_type": item.entity_type,
            "label": item.label,
            "properties": dict(item.properties),
        }
        if item.concept is not None:
            result["concept"] = {
                "system": item.concept.system,
                "code": item.concept.code,
                "display": item.concept.display,
            }
        yield result


def _structural_relations(definition: OntologyDefinition) -> Iterable[dict[str, Any]]:
    for item in definition.structural_graph.relations:
        yield {
            "relation_id": item.relation_id,
            "predicate": item.predicate,
            "source_id": item.source_id,
            "target_id": item.target_id,
            "assertion_status": item.assertion_status.value,
            "documentation_status": item.documentation_status.value,
            "confidence": item.confidence,
            "evidence_ids": list(item.evidence_ids),
            "contradicting_evidence_ids": list(item.contradicting_evidence_ids),
        }


def _known_case(
    cases: Mapping[str, dict[str, Any]],
    encounter_id: str,
    location: str,
    quarantine: "_Quarantine",
) -> dict[str, Any] | None:
    bundle = cases.get(encounter_id)
    if bundle is not None and not quarantine.is_quarantined(encounter_id):
        return bundle
    if quarantine.active:
        # Truly-unknown encounter -> record the orphan row; already-quarantined -> silently skip.
        if encounter_id not in cases:
            quarantine.record("unknown_encounter", location, encounter_id, "row references unknown encounter")
        return None
    raise ValueError(f"{location} references unknown encounter {encounter_id}")


def _upsert(items: list[dict[str, Any]], item: dict[str, Any], key: str, location: str) -> None:
    item_id = item[key]
    for existing in items:
        if existing[key] == item_id:
            if existing != item:
                raise ValueError(f"{location} produces conflicting {key} {item_id}")
            return
    items.append(item)


def _iso_datetime(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{location} must produce an ISO 8601 datetime string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{location} must produce an ISO 8601 datetime string") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{location} must include an explicit UTC offset")
    return parsed.isoformat()


def _optional_string(value: Any, location: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} must produce a non-empty string or null")
    return value


def _validate_expression_fields(adapter: AdapterDefinition, profile: BulkProfile) -> None:
    artifacts = {artifact.artifact_id: artifact for artifact in profile.artifacts}
    for resource_name, resource in adapter.resources.items():
        artifact_id = f"{resource.path}#{resource.sheet}" if resource.sheet else resource.path
        artifact = artifacts.get(artifact_id)
        if artifact is None:
            raise ValueError(f"adapter resource {resource_name} does not exist in profiled input: {artifact_id}")
        if artifact.error:
            raise ValueError(f"adapter resource {resource_name} could not be profiled: {artifact.error}")
        known = {column.name for column in artifact.columns}
        for expression, location in _resource_expressions(adapter, resource_name):
            if unknown := expression.referenced_fields - known:
                raise ValueError(f"{location} references unknown fields in {resource_name}: {sorted(unknown)}")
        for projection in adapter.structured_projections:
            if projection.resource == resource_name:
                if unknown := set(projection.evidence.field_names) - known:
                    raise ValueError(
                        f"projection.{projection.projection_id}.evidence.field_names references "
                        f"unknown fields in {resource_name}: {sorted(unknown)}"
                    )


def _resource_expressions(adapter: AdapterDefinition, resource_name: str) -> Iterable[tuple[Expression, str]]:
    if adapter.encounter.resource == resource_name:
        yield from _condition_expressions(adapter.encounter.where, "encounter.where")
        for name in ("case_id", "patient_id", "encounter_id", "admitted_at", "discharged_at"):
            yield getattr(adapter.encounter, name), f"encounter.{name}"
        for name, expression in adapter.encounter.metadata.items():
            yield expression, f"encounter.metadata.{name}"
    for index, binding in enumerate(adapter.documents):
        if binding.resource == resource_name:
            yield from _condition_expressions(binding.where, f"documents[{index}].where")
            for name in ("encounter_id", "document_id", "author_role", "recorded_at", "text"):
                yield getattr(binding, name), f"documents[{index}].{name}"
    if adapter.claim.resource == resource_name:
        yield from _condition_expressions(adapter.claim.where, "claim.where")
        yield adapter.claim.encounter_id, "claim.encounter_id"
        for name in ("drg", "allowed_amount_cents"):
            expression = getattr(adapter.claim, name)
            if expression is not None:
                yield expression, f"claim.{name}"
    for name in ("diagnoses", "procedures", "charges"):
        binding = getattr(adapter.claim, name)
        if binding is not None and binding.resource == resource_name:
            yield from _condition_expressions(binding.where, f"claim.{name}.where")
            yield binding.encounter_id, f"claim.{name}.encounter_id"
            yield binding.value, f"claim.{name}.value"
    for projection in adapter.structured_projections:
        if projection.resource == resource_name:
            yield from _condition_expressions(projection.where, f"projection.{projection.projection_id}.where")
            yield from _projection_expressions(projection)


def _projection_expressions(projection: StructuredProjection) -> Iterable[tuple[Expression, str]]:
    prefix = f"projection.{projection.projection_id}"
    yield projection.encounter_id, f"{prefix}.encounter_id"
    yield projection.source_record_id, f"{prefix}.source_record_id"
    for name in ("evidence_id", "document_id", "author_role", "recorded_at", "text"):
        yield getattr(projection.evidence, name), f"{prefix}.evidence.{name}"
    for index, entity in enumerate(projection.entities):
        yield entity.entity_id, f"{prefix}.entities[{index}].entity_id"
        yield entity.label, f"{prefix}.entities[{index}].label"
        for name, expression in entity.properties.items():
            yield expression, f"{prefix}.entities[{index}].properties.{name}"
    for index, relation in enumerate(projection.relations):
        for name in ("relation_id", "source_id", "target_id"):
            yield getattr(relation, name), f"{prefix}.relations[{index}].{name}"
    for index, assertion in enumerate(projection.assertions):
        yield assertion.assertion_id, f"{prefix}.assertions[{index}].assertion_id"
        yield assertion.subject_id, f"{prefix}.assertions[{index}].subject_id"
        for name, expression in assertion.attributes.items():
            yield expression, f"{prefix}.assertions[{index}].attributes.{name}"


def load_adapter(path: str | Path) -> AdapterDefinition:
    return AdapterDefinition.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _condition_expressions(conditions: tuple[RowCondition, ...], prefix: str) -> Iterable[tuple[Expression, str]]:
    for index, condition in enumerate(conditions):
        yield Expression(field_name=condition.field_name), f"{prefix}[{index}].field"


def _matches(row: Mapping[str, Any], conditions: tuple[RowCondition, ...]) -> bool:
    for condition in conditions:
        if condition.field_name not in row:
            raise ValueError(f"row filter references missing field {condition.field_name!r}")
        value = row[condition.field_name]
        present = value is not None and value != ""
        if condition.op == "present" and not present:
            return False
        if condition.op == "not_present" and present:
            return False
        if condition.op == "eq" and value != condition.value:
            return False
        if condition.op == "not_eq" and value == condition.value:
            return False
        if condition.op == "in" and value not in condition.value:
            return False
        if condition.op == "not_in" and value in condition.value:
            return False
    return True
