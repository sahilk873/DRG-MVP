from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from string import Formatter
from typing import Any, Mapping


SUPPORTED_READER_FORMATS = frozenset({"csv", "json", "jsonl", "xlsx"})
SUPPORTED_OPERATIONS = frozenset({
    "trim", "lower", "upper", "integer", "number", "boolean", "datetime", "split", "map",
})


@dataclass(frozen=True, slots=True)
class IngestionPolicy:
    max_files: int = 5_000
    max_file_bytes: int = 1_000_000_000
    max_total_bytes: int = 20_000_000_000
    max_archive_entries: int = 10_000
    max_archive_expanded_bytes: int = 2_000_000_000
    max_archive_compression_ratio: int = 200
    max_profile_rows_per_artifact: int = 10_000
    max_profile_columns_per_artifact: int = 1_000
    max_runtime_rows_per_resource: int = 2_000_000
    max_output_cases: int = 250_000
    sample_rows_per_artifact: int = 5
    max_sample_value_characters: int = 512
    max_total_sample_characters_per_artifact: int = 20_000

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"ingestion policy {name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class Operation:
    op: str
    delimiter: str | None = None
    format: str | None = None
    timezone: str | None = None
    values: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Operation":
        _keys(data, {"op"}, {"op", "delimiter", "format", "timezone", "values"}, "operation")
        op = _nonempty(data["op"], "operation.op")
        if op not in SUPPORTED_OPERATIONS:
            raise ValueError(f"unsupported transform operation: {op}")
        delimiter = data.get("delimiter")
        if delimiter is not None:
            delimiter = _nonempty(delimiter, "operation.delimiter")
        format_name = data.get("format")
        if format_name is not None:
            format_name = _nonempty(format_name, "operation.format")
        timezone = data.get("timezone")
        if timezone is not None:
            timezone = _nonempty(timezone, "operation.timezone")
        values = data.get("values", {})
        if not isinstance(values, Mapping):
            raise ValueError("operation.values must be an object")
        if op == "split" and delimiter is None:
            raise ValueError("split operation requires delimiter")
        if op == "map" and not values:
            raise ValueError("map operation requires non-empty values")
        if op != "split" and delimiter is not None:
            raise ValueError(f"{op} operation does not accept delimiter")
        if op != "map" and values:
            raise ValueError(f"{op} operation does not accept values")
        if op != "datetime" and (format_name is not None or timezone is not None):
            raise ValueError(f"{op} operation does not accept format or timezone")
        return cls(op=op, delimiter=delimiter, format=format_name, timezone=timezone, values=dict(values))


@dataclass(frozen=True, slots=True)
class Expression:
    field_name: str | None = None
    constant: Any = None
    has_constant: bool = False
    template: str | None = None
    operations: tuple[Operation, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Expression":
        _keys(data, set(), {"field", "constant", "template", "operations"}, "expression")
        modes = [key for key in ("field", "constant", "template") if key in data]
        if len(modes) != 1:
            raise ValueError("expression requires exactly one of field, constant or template")
        operations_data = data.get("operations", [])
        if not isinstance(operations_data, list):
            raise ValueError("expression.operations must be an array")
        field_name = _nonempty(data["field"], "expression.field") if "field" in data else None
        template = _nonempty(data["template"], "expression.template") if "template" in data else None
        if template is not None:
            for _, placeholder, format_spec, conversion in Formatter().parse(template):
                if placeholder is not None and (
                    not placeholder
                    or any(char in placeholder for char in ".[]")
                    or bool(format_spec)
                    or conversion is not None
                ):
                    raise ValueError("template placeholders must be simple field names")
        return cls(
            field_name=field_name,
            constant=data.get("constant"),
            has_constant="constant" in data,
            template=template,
            operations=tuple(Operation.from_dict(_mapping(item, "operation")) for item in operations_data),
        )

    @property
    def referenced_fields(self) -> frozenset[str]:
        fields: set[str] = set()
        if self.field_name is not None:
            fields.add(self.field_name)
        if self.template is not None:
            fields.update(
                placeholder
                for _, placeholder, _, _ in Formatter().parse(self.template)
                if placeholder is not None
            )
        return frozenset(fields)


@dataclass(frozen=True, slots=True)
class RowCondition:
    field_name: str
    op: str
    value: Any = None
    has_value: bool = False

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RowCondition":
        _keys(data, {"field", "op"}, {"field", "op", "value"}, "row condition")
        op = _enum(data["op"], {"eq", "not_eq", "in", "not_in", "present", "not_present"}, "row condition.op")
        has_value = "value" in data
        if op in {"present", "not_present"} and has_value:
            raise ValueError(f"row condition {op} does not accept value")
        if op not in {"present", "not_present"} and not has_value:
            raise ValueError(f"row condition {op} requires value")
        if op in {"in", "not_in"} and (not isinstance(data.get("value"), list) or not data["value"]):
            raise ValueError(f"row condition {op} requires a non-empty array value")
        return cls(
            field_name=_nonempty(data["field"], "row condition.field"),
            op=op,
            value=data.get("value"),
            has_value=has_value,
        )


@dataclass(frozen=True, slots=True)
class ResourceDefinition:
    path: str
    format: str
    sheet: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResourceDefinition":
        _keys(data, {"path", "format"}, {"path", "format", "sheet"}, "resource")
        path = _safe_relative_path(data["path"], "resource.path")
        format_name = _nonempty(data["format"], "resource.format")
        if format_name not in SUPPORTED_READER_FORMATS:
            raise ValueError(f"unsupported resource format: {format_name}")
        sheet = data.get("sheet")
        if sheet is not None:
            sheet = _nonempty(sheet, "resource.sheet")
        if format_name == "xlsx" and sheet is None:
            raise ValueError("xlsx resource requires sheet")
        if format_name != "xlsx" and sheet is not None:
            raise ValueError(f"{format_name} resource does not accept sheet")
        return cls(path=path, format=format_name, sheet=sheet)


@dataclass(frozen=True, slots=True)
class CollectionBinding:
    resource: str
    encounter_id: Expression
    value: Expression
    where: tuple[RowCondition, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], name: str) -> "CollectionBinding":
        _keys(data, {"resource", "encounter_id", "value"}, {"resource", "encounter_id", "value", "where"}, name)
        return cls(
            resource=_nonempty(data["resource"], f"{name}.resource"),
            encounter_id=Expression.from_dict(_mapping(data["encounter_id"], f"{name}.encounter_id")),
            value=Expression.from_dict(_mapping(data["value"], f"{name}.value")),
            where=_conditions(data.get("where", []), f"{name}.where"),
        )


@dataclass(frozen=True, slots=True)
class EncounterBinding:
    resource: str
    case_id: Expression
    patient_id: Expression
    encounter_id: Expression
    admitted_at: Expression
    discharged_at: Expression
    metadata: Mapping[str, Expression]
    where: tuple[RowCondition, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EncounterBinding":
        required = {"resource", "case_id", "patient_id", "encounter_id", "admitted_at", "discharged_at"}
        _keys(data, required, required | {"metadata", "where"}, "encounter binding")
        metadata_data = data.get("metadata", {})
        if not isinstance(metadata_data, Mapping):
            raise ValueError("encounter.metadata must be an object")
        return cls(
            resource=_nonempty(data["resource"], "encounter.resource"),
            case_id=_expression(data, "case_id", "encounter"),
            patient_id=_expression(data, "patient_id", "encounter"),
            encounter_id=_expression(data, "encounter_id", "encounter"),
            admitted_at=_expression(data, "admitted_at", "encounter"),
            discharged_at=_expression(data, "discharged_at", "encounter"),
            metadata={
                _nonempty(key, "metadata key"): Expression.from_dict(_mapping(value, f"metadata.{key}"))
                for key, value in metadata_data.items()
            },
            where=_conditions(data.get("where", []), "encounter.where"),
        )


@dataclass(frozen=True, slots=True)
class DocumentBinding:
    resource: str
    encounter_id: Expression
    document_id: Expression
    author_role: Expression
    recorded_at: Expression
    text: Expression
    where: tuple[RowCondition, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DocumentBinding":
        required = {"resource", "encounter_id", "document_id", "author_role", "recorded_at", "text"}
        _keys(data, required, required | {"where"}, "document binding")
        return cls(
            resource=_nonempty(data["resource"], "document.resource"),
            encounter_id=_expression(data, "encounter_id", "document"),
            document_id=_expression(data, "document_id", "document"),
            author_role=_expression(data, "author_role", "document"),
            recorded_at=_expression(data, "recorded_at", "document"),
            text=_expression(data, "text", "document"),
            where=_conditions(data.get("where", []), "document.where"),
        )


@dataclass(frozen=True, slots=True)
class ClaimBinding:
    resource: str
    encounter_id: Expression
    drg: Expression | None
    allowed_amount_cents: Expression | None
    diagnoses: CollectionBinding | None
    procedures: CollectionBinding | None
    charges: CollectionBinding | None
    where: tuple[RowCondition, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ClaimBinding":
        required = {"resource", "encounter_id"}
        allowed = required | {"drg", "allowed_amount_cents", "diagnoses", "procedures", "charges", "where"}
        _keys(data, required, allowed, "claim binding")
        return cls(
            resource=_nonempty(data["resource"], "claim.resource"),
            encounter_id=_expression(data, "encounter_id", "claim"),
            drg=_optional_expression(data, "drg", "claim"),
            allowed_amount_cents=_optional_expression(data, "allowed_amount_cents", "claim"),
            diagnoses=_optional_collection(data, "diagnoses"),
            procedures=_optional_collection(data, "procedures"),
            charges=_optional_collection(data, "charges"),
            where=_conditions(data.get("where", []), "claim.where"),
        )


@dataclass(frozen=True, slots=True)
class EvidenceProjection:
    evidence_id: Expression
    document_id: Expression
    author_role: Expression
    recorded_at: Expression
    text: Expression
    field_names: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceProjection":
        required = {"evidence_id", "document_id", "author_role", "recorded_at", "text", "field_names"}
        _exact_keys(data, required, "evidence projection")
        fields = _string_array(data["field_names"], "evidence.field_names", nonempty=True)
        return cls(
            evidence_id=_expression(data, "evidence_id", "evidence"),
            document_id=_expression(data, "document_id", "evidence"),
            author_role=_expression(data, "author_role", "evidence"),
            recorded_at=_expression(data, "recorded_at", "evidence"),
            text=_expression(data, "text", "evidence"),
            field_names=fields,
        )


@dataclass(frozen=True, slots=True)
class EntityProjection:
    entity_id: Expression
    entity_type: str
    label: Expression
    properties: Mapping[str, Expression]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EntityProjection":
        required = {"entity_id", "entity_type", "label", "properties"}
        _exact_keys(data, required, "entity projection")
        properties = _mapping(data["properties"], "entity.properties")
        return cls(
            entity_id=_expression(data, "entity_id", "entity"),
            entity_type=_nonempty(data["entity_type"], "entity.entity_type"),
            label=_expression(data, "label", "entity"),
            properties={
                _nonempty(key, "entity property key"): Expression.from_dict(_mapping(value, f"properties.{key}"))
                for key, value in properties.items()
            },
        )


@dataclass(frozen=True, slots=True)
class RelationProjection:
    relation_id: Expression
    predicate: str
    source_id: Expression
    target_id: Expression
    assertion_status: str
    documentation_status: str
    confidence: float
    cite_evidence: bool

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RelationProjection":
        required = {
            "relation_id", "predicate", "source_id", "target_id", "assertion_status",
            "documentation_status", "confidence", "cite_evidence",
        }
        _exact_keys(data, required, "relation projection")
        confidence = _confidence(data["confidence"], "relation.confidence")
        if not isinstance(data["cite_evidence"], bool):
            raise ValueError("relation.cite_evidence must be a boolean")
        return cls(
            relation_id=_expression(data, "relation_id", "relation"),
            predicate=_nonempty(data["predicate"], "relation.predicate"),
            source_id=_expression(data, "source_id", "relation"),
            target_id=_expression(data, "target_id", "relation"),
            assertion_status=_enum(data["assertion_status"], {"present", "absent", "uncertain", "historical"}, "relation.assertion_status"),
            documentation_status=_enum(data["documentation_status"], {"explicit", "inferred", "conflicted", "absent"}, "relation.documentation_status"),
            confidence=confidence,
            cite_evidence=data["cite_evidence"],
        )


@dataclass(frozen=True, slots=True)
class AssertionProjection:
    assertion_id: Expression
    subject_id: Expression
    concept: str
    status: str
    documentation_status: str
    confidence: float
    attributes: Mapping[str, Expression]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AssertionProjection":
        required = {
            "assertion_id", "subject_id", "concept", "status", "documentation_status",
            "confidence", "attributes",
        }
        _exact_keys(data, required, "assertion projection")
        attributes = _mapping(data["attributes"], "assertion.attributes")
        return cls(
            assertion_id=_expression(data, "assertion_id", "assertion"),
            subject_id=_expression(data, "subject_id", "assertion"),
            concept=_nonempty(data["concept"], "assertion.concept"),
            status=_enum(data["status"], {"present", "absent", "uncertain", "historical"}, "assertion.status"),
            documentation_status=_enum(data["documentation_status"], {"explicit", "inferred", "conflicted", "absent"}, "assertion.documentation_status"),
            confidence=_confidence(data["confidence"], "assertion.confidence"),
            attributes={
                _nonempty(key, "assertion attribute key"): Expression.from_dict(_mapping(value, f"attributes.{key}"))
                for key, value in attributes.items()
            },
        )


@dataclass(frozen=True, slots=True)
class StructuredProjection:
    projection_id: str
    resource: str
    encounter_id: Expression
    source_record_id: Expression
    evidence: EvidenceProjection
    entities: tuple[EntityProjection, ...]
    relations: tuple[RelationProjection, ...]
    assertions: tuple[AssertionProjection, ...]
    where: tuple[RowCondition, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StructuredProjection":
        required = {
            "projection_id", "resource", "encounter_id", "source_record_id", "evidence",
            "entities", "relations", "assertions",
        }
        _keys(data, required, required | {"where"}, "structured projection")
        for name in ("entities", "relations", "assertions"):
            if not isinstance(data[name], list):
                raise ValueError(f"projection.{name} must be an array")
        return cls(
            projection_id=_nonempty(data["projection_id"], "projection.projection_id"),
            resource=_nonempty(data["resource"], "projection.resource"),
            encounter_id=_expression(data, "encounter_id", "projection"),
            source_record_id=_expression(data, "source_record_id", "projection"),
            evidence=EvidenceProjection.from_dict(_mapping(data["evidence"], "projection.evidence")),
            entities=tuple(EntityProjection.from_dict(_mapping(item, "entity projection")) for item in data["entities"]),
            relations=tuple(RelationProjection.from_dict(_mapping(item, "relation projection")) for item in data["relations"]),
            assertions=tuple(AssertionProjection.from_dict(_mapping(item, "assertion projection")) for item in data["assertions"]),
            where=_conditions(data.get("where", []), "projection.where"),
        )


@dataclass(frozen=True, slots=True)
class OntologyBinding:
    ontology_id: str
    version: str
    digest: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OntologyBinding":
        _exact_keys(data, {"ontology_id", "version", "digest"}, "adapter ontology binding")
        digest = _nonempty(data["digest"], "ontology.digest")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("ontology.digest must be a lowercase SHA-256 digest")
        return cls(
            _nonempty(data["ontology_id"], "ontology.ontology_id"),
            _nonempty(data["version"], "ontology.version"),
            digest,
        )


@dataclass(frozen=True, slots=True)
class AdapterDefinition:
    adapter_id: str
    version: str
    status: str
    source_schema_fingerprint: str
    ontology: OntologyBinding
    resources: Mapping[str, ResourceDefinition]
    encounter: EncounterBinding
    documents: tuple[DocumentBinding, ...]
    claim: ClaimBinding
    structured_projections: tuple[StructuredProjection, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AdapterDefinition":
        required = {
            "adapter_id", "version", "status", "source_schema_fingerprint", "ontology",
            "resources", "encounter", "documents", "claim", "structured_projections",
        }
        _exact_keys(data, required, "adapter definition")
        status = _enum(data["status"], {"draft", "approved-for-demo", "approved"}, "adapter.status")
        fingerprint = _nonempty(data["source_schema_fingerprint"], "source_schema_fingerprint")
        if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
            raise ValueError("source_schema_fingerprint must be a lowercase SHA-256 digest")
        resources_data = _mapping(data["resources"], "adapter.resources")
        if not resources_data:
            raise ValueError("adapter.resources must not be empty")
        if not isinstance(data["documents"], list) or not isinstance(data["structured_projections"], list):
            raise ValueError("adapter documents and structured_projections must be arrays")
        adapter = cls(
            adapter_id=_nonempty(data["adapter_id"], "adapter.adapter_id"),
            version=_nonempty(data["version"], "adapter.version"),
            status=status,
            source_schema_fingerprint=fingerprint,
            ontology=OntologyBinding.from_dict(_mapping(data["ontology"], "adapter.ontology")),
            resources={
                _nonempty(name, "resource name"): ResourceDefinition.from_dict(_mapping(item, f"resource {name}"))
                for name, item in resources_data.items()
            },
            encounter=EncounterBinding.from_dict(_mapping(data["encounter"], "adapter.encounter")),
            documents=tuple(DocumentBinding.from_dict(_mapping(item, "document binding")) for item in data["documents"]),
            claim=ClaimBinding.from_dict(_mapping(data["claim"], "adapter.claim")),
            structured_projections=tuple(
                StructuredProjection.from_dict(_mapping(item, "structured projection"))
                for item in data["structured_projections"]
            ),
        )
        adapter.validate_references()
        return adapter

    def validate_references(self) -> None:
        used = {self.encounter.resource, self.claim.resource}
        used.update(item.resource for item in self.documents)
        used.update(item.resource for item in self.structured_projections)
        for collection in (self.claim.diagnoses, self.claim.procedures, self.claim.charges):
            if collection is not None:
                used.add(collection.resource)
        if unknown := used - set(self.resources):
            raise ValueError(f"adapter references unknown resources: {sorted(unknown)}")
        projection_ids = [item.projection_id for item in self.structured_projections]
        if len(projection_ids) != len(set(projection_ids)):
            raise ValueError("structured projection IDs must be unique")


def _expression(data: Mapping[str, Any], key: str, prefix: str) -> Expression:
    return Expression.from_dict(_mapping(data[key], f"{prefix}.{key}"))


def _optional_expression(data: Mapping[str, Any], key: str, prefix: str) -> Expression | None:
    return _expression(data, key, prefix) if key in data else None


def _optional_collection(data: Mapping[str, Any], key: str) -> CollectionBinding | None:
    return CollectionBinding.from_dict(_mapping(data[key], f"claim.{key}"), f"claim.{key}") if key in data else None


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _safe_relative_path(value: Any, name: str) -> str:
    path = _nonempty(value, name)
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts or path.startswith("~") or "\\" in path:
        raise ValueError(f"{name} must be a safe relative path")
    return parsed.as_posix()


def _keys(data: Mapping[str, Any], required: set[str], allowed: set[str], name: str) -> None:
    if missing := required - set(data):
        raise ValueError(f"{name} missing required fields: {sorted(missing)}")
    if unknown := set(data) - allowed:
        raise ValueError(f"{name} contains unknown fields: {sorted(unknown)}")


def _exact_keys(data: Mapping[str, Any], expected: set[str], name: str) -> None:
    _keys(data, expected, expected, name)


def _string_array(value: Any, name: str, *, nonempty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{name} must be an array of non-empty strings")
    if nonempty and not value:
        raise ValueError(f"{name} must not be empty")
    if len(value) != len(set(value)):
        raise ValueError(f"{name} must not contain duplicates")
    return tuple(value)


def _enum(value: Any, allowed: set[str], name: str) -> str:
    parsed = _nonempty(value, name)
    if parsed not in allowed:
        raise ValueError(f"{name} must be one of {sorted(allowed)}")
    return parsed


def _confidence(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1")
    return float(value)


def _conditions(value: Any, name: str) -> tuple[RowCondition, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return tuple(RowCondition.from_dict(_mapping(item, f"{name} item")) for item in value)
