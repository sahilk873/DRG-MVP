import json
from pathlib import Path
import unittest

from revenue_integrity.models import EncounterCase
from revenue_integrity.ontology import OntologyDefinition, load_builtin_ontology


ROOT = Path(__file__).parents[1]


def fixture():
    return json.loads((ROOT / "examples/case_pressure_injury.json").read_text())


class OntologyValidationTests(unittest.TestCase):
    def test_builtin_definition_accepts_subclass_relations(self):
        definition = load_builtin_ontology("wound-care-encounter-ontology", "1.1.0-draft")
        case = EncounterCase.from_dict(fixture(), ontology_definition=definition)
        self.assertTrue(definition.is_a("PressureInjury", "Wound"))
        self.assertEqual(case.ontology.entities[3].entity_type, "PressureInjury")

    def test_abstract_class_cannot_be_instantiated(self):
        payload = fixture()
        payload["ontology"]["entities"][3]["entity_type"] = "ClinicalEntity"
        with self.assertRaisesRegex(ValueError, "abstract class"):
            EncounterCase.from_dict(payload)

    def test_relation_domain_violation_is_rejected(self):
        payload = fixture()
        relation = next(item for item in payload["ontology"]["relations"] if item["predicate"] == "hasStage")
        relation["source_id"] = "root:patient"
        with self.assertRaisesRegex(ValueError, "invalid source type"):
            EncounterCase.from_dict(payload)

    def test_entity_value_sets_are_enforced(self):
        payload = fixture()
        stage = next(item for item in payload["ontology"]["entities"] if item["entity_type"] == "PressureInjuryStage")
        stage["properties"]["value"] = "9"
        with self.assertRaisesRegex(ValueError, "not in value set"):
            EncounterCase.from_dict(payload)

    def test_semantic_definition_drift_is_rejected_without_version_change(self):
        payload = fixture()
        definition_payload = json.loads(
            (ROOT / "src/revenue_integrity/data/wound_care_ontology_v1.json").read_text()
        )
        pressure_injury = next(
            item for item in definition_payload["classes"] if item["class_id"] == "PressureInjury"
        )
        pressure_injury["label"] = "Changed label that also changes the agent contract"
        changed_definition = OntologyDefinition.from_dict(definition_payload)

        self.assertNotEqual(payload["ontology"]["ontology_digest"], changed_definition.digest)
        with self.assertRaisesRegex(ValueError, "digest"):
            EncounterCase.from_dict(payload, ontology_definition=changed_definition)

    def test_evidence_required_by_relation_definition(self):
        payload = fixture()
        relation = next(item for item in payload["ontology"]["relations"] if item["predicate"] == "hasStage")
        relation["evidence_ids"] = []
        with self.assertRaisesRegex(ValueError, "requires evidence"):
            EncounterCase.from_dict(payload)

    def test_relation_evidence_cannot_support_and_contradict(self):
        payload = fixture()
        relation = next(item for item in payload["ontology"]["relations"] if item["predicate"] == "hasStage")
        relation["contradicting_evidence_ids"] = ["EV-001"]
        with self.assertRaisesRegex(ValueError, "both supporting and contradicting"):
            EncounterCase.from_dict(payload)

    def test_custom_definition_can_be_injected_without_engine_changes(self):
        definition = OntologyDefinition.from_dict({
            "ontology_id": "general-observation-ontology",
            "version": "1",
            "status": "draft",
            "structural_graph": {
                "entities": [
                    {"entity_id": "root:patient", "entity_type": "Patient", "label": "Patient", "properties": {}},
                    {"entity_id": "root:encounter", "entity_type": "Encounter", "label": "Encounter", "properties": {}},
                    {"entity_id": "root:claim", "entity_type": "Claim", "label": "Claim", "properties": {}},
                ],
                "relations": [
                    {"relation_id": "rel:patient-encounter", "predicate": "hasEncounter", "source_id": "root:patient", "target_id": "root:encounter", "assertion_status": "present", "documentation_status": "explicit", "confidence": 1, "evidence_ids": []},
                    {"relation_id": "rel:encounter-claim", "predicate": "hasClaim", "source_id": "root:encounter", "target_id": "root:claim", "assertion_status": "present", "documentation_status": "explicit", "confidence": 1, "evidence_ids": []},
                ],
            },
            "classes": [
                {"class_id": "Entity", "label": "Entity", "abstract": True},
                {"class_id": "Patient", "label": "Patient", "parent": "Entity"},
                {"class_id": "Encounter", "label": "Encounter", "parent": "Entity"},
                {"class_id": "Claim", "label": "Claim", "parent": "Entity"},
                {"class_id": "Observation", "label": "Observation", "parent": "Entity"},
            ],
            "relations": [
                {"relation_id": "hasEncounter", "domain": ["Patient"], "range": ["Encounter"], "requires_evidence": False},
                {"relation_id": "hasClaim", "domain": ["Encounter"], "range": ["Claim"], "requires_evidence": False},
                {"relation_id": "hasObservation", "domain": ["Encounter"], "range": ["Observation"], "requires_evidence": True},
            ],
        })
        payload = fixture()
        payload["ontology"] = {
            "ontology_id": "general-observation-ontology",
            "ontology_version": "1",
            "ontology_digest": definition.digest,
            "entities": [
                {"entity_id": "root:patient", "entity_type": "Patient", "label": "Patient", "properties": {}},
                {"entity_id": "root:encounter", "entity_type": "Encounter", "label": "Encounter", "properties": {}},
                {"entity_id": "root:claim", "entity_type": "Claim", "label": "Claim", "properties": {}},
                {"entity_id": "observation:1", "entity_type": "Observation", "label": "Observation", "properties": {}},
            ],
            "relations": [
                {"relation_id": "rel:patient-encounter", "predicate": "hasEncounter", "source_id": "root:patient", "target_id": "root:encounter", "assertion_status": "present", "documentation_status": "explicit", "confidence": 1, "evidence_ids": []},
                {"relation_id": "rel:encounter-claim", "predicate": "hasClaim", "source_id": "root:encounter", "target_id": "root:claim", "assertion_status": "present", "documentation_status": "explicit", "confidence": 1, "evidence_ids": []},
                {"relation_id": "rel:encounter-observation", "predicate": "hasObservation", "source_id": "root:encounter", "target_id": "observation:1", "assertion_status": "present", "documentation_status": "explicit", "confidence": 0.98, "evidence_ids": ["EV-001"]},
            ],
        }
        payload["assertions"][0]["subject_id"] = "observation:1"
        case = EncounterCase.from_dict(payload, ontology_definition=definition)
        self.assertEqual(case.ontology.ontology_id, "general-observation-ontology")

    def test_class_hierarchy_cycle_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "cycle"):
            OntologyDefinition.from_dict({
                "ontology_id": "cyclic",
                "version": "1",
                "status": "draft",
                "structural_graph": {"entities": [], "relations": []},
                "classes": [
                    {"class_id": "A", "label": "A", "parent": "B"},
                    {"class_id": "B", "label": "B", "parent": "A"},
                ],
                "relations": [],
            })

    def test_definition_boolean_fields_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            OntologyDefinition.from_dict({
                "ontology_id": "invalid",
                "version": "1",
                "status": "draft",
                "structural_graph": {"entities": [], "relations": []},
                "classes": [{"class_id": "Entity", "label": "Entity"}],
                "relations": [{
                    "relation_id": "relatedTo",
                    "domain": ["Entity"],
                    "range": ["Entity"],
                    "requires_evidence": "false",
                }],
            })

    def test_unknown_definition_extension_fails_until_contract_is_versioned(self):
        payload = json.loads(
            (ROOT / "src/revenue_integrity/data/wound_care_ontology_v1.json").read_text()
        )
        payload["classes"][0]["unreviewed_semantics"] = True
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            OntologyDefinition.from_dict(payload)


class DenialEventOntologyTests(unittest.TestCase):
    def test_builtin_denial_ontology_loads_and_defines_denial_event(self):
        definition = load_builtin_ontology("denial-event-ontology", "1.0.0-draft")
        self.assertEqual(definition.ontology_id, "denial-event-ontology")
        self.assertIn("DenialEvent", definition.classes)
        # DenialEvent is a concrete FinancialEntity subject usable as finding lineage.
        self.assertTrue(definition.is_a("DenialEvent", "FinancialEntity"))
        self.assertFalse(definition.classes["DenialEvent"].abstract)
        self.assertEqual(len(definition.digest), 64)

    def test_denial_ontology_digest_is_self_consistent(self):
        # Loading recomputes and self-verifies the structural graph digest; a second
        # load produces a stable digest (no drift).
        first = load_builtin_ontology("denial-event-ontology", "1.0.0-draft")
        second = load_builtin_ontology("denial-event-ontology", "1.0.0-draft")
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(
            first.structural_graph.ontology_digest, first.digest
        )

    def test_tampered_denial_ontology_digest_is_rejected(self):
        definition = load_builtin_ontology("denial-event-ontology", "1.0.0-draft")
        tampered_graph = json.loads(json.dumps({
            "ontology_id": definition.ontology_id,
            "ontology_version": definition.version,
            # A digest that does not match the recomputed definition digest.
            "ontology_digest": "0" * 64,
            "entities": [
                {"entity_id": "root:patient", "entity_type": "Patient", "label": "Patient", "properties": {}},
                {"entity_id": "root:encounter", "entity_type": "Encounter", "label": "Encounter", "properties": {}},
                {"entity_id": "root:claim", "entity_type": "Claim", "label": "Claim", "properties": {}},
            ],
            "relations": [],
        }))
        from revenue_integrity.ontology import OntologyGraph

        graph = OntologyGraph.from_dict(tampered_graph)
        with self.assertRaisesRegex(ValueError, "digest"):
            definition.validate_graph(graph, set())
