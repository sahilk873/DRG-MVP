from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping

from .models import AssertionStatus, DocumentationStatus


@dataclass(frozen=True, slots=True)
class ConceptCode:
    """A terminology mapping without coupling the graph to one vocabulary."""

    system: str
    code: str
    display: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ConceptCode":
        _exact_keys(data, {"system", "code", "display"}, "concept")
        return cls(*(_nonempty(data[key], f"concept.{key}") for key in ("system", "code", "display")))


@dataclass(frozen=True, slots=True)
class OntologyEntity:
    entity_id: str
    entity_type: str
    label: str
    concept: ConceptCode | None = None
    properties: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OntologyEntity":
        required = {"entity_id", "entity_type", "label", "properties"}
        allowed = required | {"concept"}
        _keys(data, required, allowed, "ontology entity")
        properties = data["properties"]
        if not isinstance(properties, Mapping):
            raise ValueError("ontology entity properties must be an object")
        concept_data = data.get("concept")
        if concept_data is not None and not isinstance(concept_data, Mapping):
            raise ValueError("ontology entity concept must be an object or null")
        return cls(
            entity_id=_nonempty(data["entity_id"], "entity_id"),
            entity_type=_nonempty(data["entity_type"], "entity_type"),
            label=_nonempty(data["label"], "label"),
            concept=ConceptCode.from_dict(concept_data) if concept_data is not None else None,
            properties=dict(properties),
        )


@dataclass(frozen=True, slots=True)
class OntologyRelation:
    relation_id: str
    predicate: str
    source_id: str
    target_id: str
    assertion_status: AssertionStatus
    documentation_status: DocumentationStatus
    confidence: float
    evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OntologyRelation":
        required = {
            "relation_id",
            "predicate",
            "source_id",
            "target_id",
            "assertion_status",
            "documentation_status",
            "confidence",
            "evidence_ids",
        }
        _keys(data, required, required | {"contradicting_evidence_ids"}, "ontology relation")
        confidence = data["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("ontology relation confidence must be between 0 and 1")
        return cls(
            relation_id=_nonempty(data["relation_id"], "relation_id"),
            predicate=_nonempty(data["predicate"], "predicate"),
            source_id=_nonempty(data["source_id"], "source_id"),
            target_id=_nonempty(data["target_id"], "target_id"),
            assertion_status=AssertionStatus(data["assertion_status"]),
            documentation_status=DocumentationStatus(data["documentation_status"]),
            confidence=float(confidence),
            evidence_ids=_string_array(data["evidence_ids"], "ontology relation evidence_ids"),
            contradicting_evidence_ids=_string_array(
                data.get("contradicting_evidence_ids", []),
                "ontology relation contradicting_evidence_ids",
            ),
        )


@dataclass(frozen=True, slots=True)
class OntologyGraph:
    """Patient-specific instances and evidence-grounded edges."""

    ontology_id: str
    ontology_version: str
    entities: tuple[OntologyEntity, ...]
    relations: tuple[OntologyRelation, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OntologyGraph":
        required = {"ontology_id", "ontology_version", "entities", "relations"}
        _exact_keys(data, required, "ontology graph")
        if not isinstance(data["entities"], list) or not isinstance(data["relations"], list):
            raise ValueError("ontology graph entities and relations must be arrays")
        graph = cls(
            ontology_id=_nonempty(data["ontology_id"], "ontology_id"),
            ontology_version=_nonempty(data["ontology_version"], "ontology_version"),
            entities=tuple(OntologyEntity.from_dict(_mapping(item, "ontology entity")) for item in data["entities"]),
            relations=tuple(OntologyRelation.from_dict(_mapping(item, "ontology relation")) for item in data["relations"]),
        )
        graph.validate_internal_references()
        return graph

    def validate_internal_references(self) -> None:
        entity_ids = [entity.entity_id for entity in self.entities]
        relation_ids = [relation.relation_id for relation in self.relations]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("ontology entity IDs must be unique")
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("ontology relation IDs must be unique")
        known = set(entity_ids)
        for relation in self.relations:
            if relation.source_id not in known or relation.target_id not in known:
                raise ValueError(f"ontology relation {relation.relation_id} has a dangling entity reference")


@dataclass(frozen=True, slots=True)
class ClassDefinition:
    class_id: str
    label: str
    parent: str | None
    abstract: bool
    value_set: str | None


@dataclass(frozen=True, slots=True)
class RelationDefinition:
    relation_id: str
    domain: tuple[str, ...]
    range: tuple[str, ...]
    requires_evidence: bool


@dataclass(frozen=True, slots=True)
class OntologyDefinition:
    """Versioned type system used to validate any supported clinical graph."""

    ontology_id: str
    version: str
    status: str
    classes: Mapping[str, ClassDefinition]
    relations: Mapping[str, RelationDefinition]
    value_sets: Mapping[str, tuple[str, ...]]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OntologyDefinition":
        if not isinstance(data, Mapping):
            raise ValueError("ontology definition must be an object")
        if not isinstance(data.get("classes"), list) or not isinstance(data.get("relations"), list):
            raise ValueError("ontology definition classes and relations must be arrays")

        class_definitions: dict[str, ClassDefinition] = {}
        raw_value_sets = data.get("value_sets", {})
        if not isinstance(raw_value_sets, Mapping):
            raise ValueError("ontology definition value_sets must be an object")
        value_sets = {
            _nonempty(value_set_id, "value set ID"): _nonempty_string_array(values, f"{value_set_id} values")
            for value_set_id, values in raw_value_sets.items()
        }
        for raw_item in data["classes"]:
            item = _mapping(raw_item, "class definition")
            class_id = _nonempty(item.get("class_id"), "class_id")
            if class_id in class_definitions:
                raise ValueError(f"duplicate ontology class: {class_id}")
            label = _nonempty(item.get("label"), f"{class_id}.label")
            parent = item.get("parent")
            if parent is not None:
                parent = _nonempty(parent, f"{class_id}.parent")
            abstract = item.get("abstract", False)
            if not isinstance(abstract, bool):
                raise ValueError(f"{class_id}.abstract must be a boolean")
            value_set = item.get("value_set")
            if value_set is not None:
                value_set = _nonempty(value_set, f"{class_id}.value_set")
            class_definitions[class_id] = ClassDefinition(class_id, label, parent, abstract, value_set)

        relation_definitions: dict[str, RelationDefinition] = {}
        for raw_item in data["relations"]:
            item = _mapping(raw_item, "relation definition")
            relation_id = _nonempty(item.get("relation_id"), "relation_id")
            if relation_id in relation_definitions:
                raise ValueError(f"duplicate ontology relation: {relation_id}")
            requires_evidence = item.get("requires_evidence")
            if not isinstance(requires_evidence, bool):
                raise ValueError(f"{relation_id}.requires_evidence must be a boolean")
            relation_definitions[relation_id] = RelationDefinition(
                relation_id,
                _nonempty_string_array(item.get("domain"), f"{relation_id}.domain"),
                _nonempty_string_array(item.get("range"), f"{relation_id}.range"),
                requires_evidence,
            )

        status = _nonempty(data.get("status"), "status")
        if status not in {"draft", "clinical-review-required", "approved"}:
            raise ValueError(f"unsupported ontology definition status: {status}")
        definition = cls(
            ontology_id=_nonempty(data.get("ontology_id"), "ontology_id"),
            version=_nonempty(data.get("version"), "version"),
            status=status,
            classes=class_definitions,
            relations=relation_definitions,
            value_sets=value_sets,
        )
        definition._validate_definition()
        return definition

    def validate_graph(self, graph: OntologyGraph, evidence_ids: set[str]) -> None:
        if graph.ontology_id != self.ontology_id or graph.ontology_version != self.version:
            raise ValueError("ontology graph definition ID or version does not match the configured ontology")
        entities = {entity.entity_id: entity for entity in graph.entities}
        for entity in graph.entities:
            definition = self.classes.get(entity.entity_type)
            if definition is None:
                raise ValueError(f"unknown ontology class: {entity.entity_type}")
            if definition.abstract:
                raise ValueError(f"ontology entity cannot instantiate abstract class: {entity.entity_type}")
            if definition.value_set is not None:
                value = entity.properties.get("value")
                if value not in self.value_sets[definition.value_set]:
                    raise ValueError(
                        f"ontology entity {entity.entity_id} value is not in value set {definition.value_set}"
                    )
        for relation in graph.relations:
            definition = self.relations.get(relation.predicate)
            if definition is None:
                raise ValueError(f"unknown ontology predicate: {relation.predicate}")
            source_type = entities[relation.source_id].entity_type
            target_type = entities[relation.target_id].entity_type
            if not any(self.is_a(source_type, allowed) for allowed in definition.domain):
                raise ValueError(f"relation {relation.relation_id} has invalid source type {source_type}")
            if not any(self.is_a(target_type, allowed) for allowed in definition.range):
                raise ValueError(f"relation {relation.relation_id} has invalid target type {target_type}")
            if definition.requires_evidence and not relation.evidence_ids:
                raise ValueError(f"relation {relation.relation_id} requires evidence")
            if overlap := set(relation.evidence_ids) & set(relation.contradicting_evidence_ids):
                raise ValueError(
                    f"relation {relation.relation_id} cites evidence as both supporting and contradicting: "
                    f"{sorted(overlap)}"
                )
            referenced = set(relation.evidence_ids + relation.contradicting_evidence_ids)
            if unknown := referenced - evidence_ids:
                raise ValueError(f"relation {relation.relation_id} references unknown evidence: {sorted(unknown)}")

    def is_a(self, class_id: str, expected: str) -> bool:
        current: str | None = class_id
        seen: set[str] = set()
        while current is not None and current not in seen:
            if current == expected:
                return True
            seen.add(current)
            definition = self.classes.get(current)
            current = definition.parent if definition else None
        return False

    def _validate_definition(self) -> None:
        if not self.classes:
            raise ValueError("ontology definition must contain at least one class")
        for definition in self.classes.values():
            if definition.parent is not None and definition.parent not in self.classes:
                raise ValueError(f"class {definition.class_id} has unknown parent {definition.parent}")
            if definition.value_set is not None and definition.value_set not in self.value_sets:
                raise ValueError(f"class {definition.class_id} has unknown value set {definition.value_set}")
            lineage: set[str] = set()
            current: str | None = definition.class_id
            while current is not None:
                if current in lineage:
                    raise ValueError(f"ontology class hierarchy contains a cycle at {current}")
                lineage.add(current)
                current_definition = self.classes.get(current)
                current = current_definition.parent if current_definition else None
        for definition in self.relations.values():
            for class_id in definition.domain + definition.range:
                if class_id not in self.classes:
                    raise ValueError(f"relation {definition.relation_id} references unknown class {class_id}")


_BUILTIN_ONTOLOGIES: Mapping[tuple[str, str], str] = {
    ("wound-care-encounter-ontology", "1.0.0-draft"): "data/wound_care_ontology_v1.json",
}


def load_builtin_ontology(ontology_id: str, version: str) -> OntologyDefinition:
    """Load a packaged definition; callers can inject any custom definition instead."""

    resource_name = _BUILTIN_ONTOLOGIES.get((ontology_id, version))
    if resource_name is None:
        raise ValueError(
            f"no built-in ontology definition for {ontology_id!r} version {version!r}; "
            "provide ontology_definition explicitly"
        )
    resource = files("revenue_integrity").joinpath(resource_name)
    return OntologyDefinition.from_dict(json.loads(resource.read_text(encoding="utf-8")))


def load_ontology_definition(path: str | Path) -> OntologyDefinition:
    """Load a custom versioned ontology definition from a JSON file."""

    return OntologyDefinition.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _string_array(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{name} must be an array of non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{name} must not contain duplicates")
    return tuple(value)


def _nonempty_string_array(value: Any, name: str) -> tuple[str, ...]:
    parsed = _string_array(value, name)
    if not parsed:
        raise ValueError(f"{name} must contain at least one item")
    return parsed


def _keys(data: Mapping[str, Any], required: set[str], allowed: set[str], name: str) -> None:
    if missing := required - set(data):
        raise ValueError(f"{name} missing required fields: {sorted(missing)}")
    if unknown := set(data) - allowed:
        raise ValueError(f"{name} contains unknown fields: {sorted(unknown)}")


def _exact_keys(data: Mapping[str, Any], expected: set[str], name: str) -> None:
    _keys(data, expected, expected, name)
