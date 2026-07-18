from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from revenue_integrity.engine import RuleEngine
from revenue_integrity.grouper import DeterministicDemoGrouper
from revenue_integrity.ingestion.adapter import load_adapter, run_adapter
from revenue_integrity.ingestion.models import AdapterDefinition, IngestionPolicy, ResourceDefinition
from revenue_integrity.ingestion.profiling import profile_directory
from revenue_integrity.ingestion.readers import default_reader_registry, xlsx_sheet_names
from revenue_integrity.models import EncounterCase
from revenue_integrity.ontology import load_builtin_ontology


ROOT = Path(__file__).parents[1]
BULK = ROOT / "examples/bulk/clinic_alpha"
ADAPTER_PATH = ROOT / "examples/adapters/clinic_alpha_wound_care_v1.json"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class BulkIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = load_adapter(ADAPTER_PATH)
        self.ontology = load_builtin_ontology(
            self.adapter.ontology.ontology_id,
            self.adapter.ontology.version,
        )

    def test_bounded_profile_separates_schema_and_content_fingerprints(self):
        profile = profile_directory(BULK)

        self.assertEqual(profile.schema_fingerprint, self.adapter.source_schema_fingerprint)
        self.assertEqual(len(profile.schema_fingerprint), 64)
        self.assertEqual(len(profile.input_manifest_digest), 64)
        self.assertEqual(profile.artifact_count, 6)
        self.assertEqual(
            {artifact.artifact_id for artifact in profile.artifacts},
            {"charges.csv", "claims.csv", "diagnoses.csv", "encounters.csv", "notes.csv", "wound_assessments.csv"},
        )
        with tempfile.TemporaryDirectory() as temporary:
            bulk = Path(temporary) / "bulk"
            shutil.copytree(BULK, bulk)
            (bulk / "README.txt").write_text("delivery instructions change independently")
            changed = profile_directory(bulk)
            self.assertEqual(changed.schema_fingerprint, profile.schema_fingerprint)
            self.assertNotEqual(changed.input_manifest_digest, profile.input_manifest_digest)

    def test_profile_samples_are_character_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            bulk = Path(temporary)
            (bulk / "wide.csv").write_text("id,text\n1," + "x" * 1_000 + "\n")
            profile = profile_directory(bulk, IngestionPolicy(max_sample_value_characters=10))
            sample = profile.artifacts[0].sample_rows[0]
            self.assertEqual(sample["text"], "xxxxxxxxxx…[truncated]")
            self.assertTrue(sample["_sample_truncated"])

    def test_approved_adapter_produces_evidence_grounded_source_bundle(self):
        result = run_adapter(
            BULK,
            self.adapter,
            self.ontology,
            now=lambda: datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertEqual(len(result.source_bundles), 1)
        bundle = result.source_bundles[0]
        self.assertEqual(bundle["claim"]["diagnoses"], ["A41.9"])
        self.assertEqual(bundle["claim"]["allowed_amount_cents"], 1_200_000)
        extraction = bundle["structured_extraction"]
        self.assertEqual(extraction["assertions"][0]["attributes"]["stage"], 4)
        self.assertEqual(extraction["assertions"][0]["attributes"]["site"], "sacral_region")
        locator = extraction["evidence"][0]["source_locator"]
        self.assertEqual(locator["path"], "wound_assessments.csv")
        self.assertEqual(locator["row_number"], 1)
        self.assertEqual(result.report.output_assertions, 1)

    def test_structured_projection_flows_through_existing_rule_engine(self):
        bundle = run_adapter(BULK, self.adapter, self.ontology).source_bundles[0]
        definition = load_json(ROOT / "src/revenue_integrity/data/wound_care_ontology_v1.json")
        extraction = bundle["structured_extraction"]
        case_payload = {
            "schema_version": "2.0.0",
            **{key: bundle[key] for key in ("case_id", "patient_id", "encounter_id", "admitted_at", "discharged_at", "metadata", "claim")},
            "evidence": extraction["evidence"],
            "ontology": {
                **extraction["ontology"],
                "entities": [*definition["structural_graph"]["entities"], *extraction["ontology"]["entities"]],
                "relations": [*definition["structural_graph"]["relations"], *extraction["ontology"]["relations"]],
            },
            "assertions": extraction["assertions"],
            "provenance": {
                "framework": "mastra",
                "model_id": "test/no-narrative-facts",
                "agent_id": "test-extractor",
                "extracted_at": "2026-07-15T00:00:00Z",
                "schema_version": "2.0.0",
                "extraction_policy": {
                    "max_documents": 100,
                    "max_document_characters": 100000,
                    "max_total_document_characters": 1000000,
                    "max_evidence_items": 100,
                    "max_evidence_characters": 10000,
                    "max_total_evidence_characters": 100000,
                    "max_entities": 100,
                    "max_relations": 100,
                    "max_assertions": 100,
                },
                "ingestion": bundle["ingestion_provenance"],
            },
        }
        case = EncounterCase.from_dict(case_payload)
        findings = RuleEngine(
            load_json(ROOT / "rules/wound_care_v1.json"),
            DeterministicDemoGrouper(),
        ).evaluate(case)

        finding = next(item for item in findings if item.rule_id == "WC-PI-OMITTED-001")
        self.assertEqual(finding.assertion_ids, ("assertion:pressure-injury:ASSESS-ALPHA-001",))
        self.assertEqual(finding.evidence_ids, ("structured-evidence:ASSESS-ALPHA-001",))

    def test_schema_drift_fails_before_transform(self):
        with tempfile.TemporaryDirectory() as temporary:
            bulk = Path(temporary) / "bulk"
            shutil.copytree(BULK, bulk)
            encounters = bulk / "encounters.csv"
            encounters.write_text(encounters.read_text().replace("facility\n", "facility,new_column\n").replace("Alpha Medical Center\n", "Alpha Medical Center,new\n"))
            with self.assertRaisesRegex(ValueError, "schema drift"):
                run_adapter(bulk, self.adapter, self.ontology)

    def test_unlinked_rows_and_unmapped_values_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            bulk = Path(temporary) / "bulk"
            shutil.copytree(BULK, bulk)
            wound_path = bulk / "wound_assessments.csv"
            wound_path.write_text(wound_path.read_text().replace(",IV,Sacrum,Y", ",V,Sacrum,Y"))
            with self.assertRaisesRegex(ValueError, "unmapped value"):
                run_adapter(bulk, self.adapter, self.ontology)

        with tempfile.TemporaryDirectory() as temporary:
            bulk = Path(temporary) / "bulk"
            shutil.copytree(BULK, bulk)
            note_path = bulk / "notes.csv"
            note_path.write_text(note_path.read_text().replace("ENC-ALPHA-001", "ENC-UNKNOWN", 1))
            with self.assertRaisesRegex(ValueError, "unknown encounter"):
                run_adapter(bulk, self.adapter, self.ontology)

    def test_draft_adapter_and_path_traversal_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "must be approved"):
            run_adapter(BULK, replace(self.adapter, status="draft"), self.ontology)
        payload = load_json(ADAPTER_PATH)
        payload["resources"]["encounters"]["path"] = "../outside.csv"
        with self.assertRaisesRegex(ValueError, "safe relative path"):
            AdapterDefinition.from_dict(payload)

    def test_row_filters_and_lineage_fields_are_validated(self):
        payload = load_json(ADAPTER_PATH)
        payload["claim"]["where"] = [{"field": "encounter_id", "op": "eq", "value": "ENC-ALPHA-001"}]
        result = run_adapter(BULK, AdapterDefinition.from_dict(payload), self.ontology)
        self.assertEqual(result.report.output_cases, 1)

        payload["claim"]["where"][0]["value"] = "ENC-OTHER"
        with self.assertRaisesRegex(ValueError, "without exactly one claim"):
            run_adapter(BULK, AdapterDefinition.from_dict(payload), self.ontology)

        payload = load_json(ADAPTER_PATH)
        payload["structured_projections"][0]["evidence"]["field_names"].append("missing_field")
        with self.assertRaisesRegex(ValueError, "field_names references unknown fields"):
            run_adapter(BULK, AdapterDefinition.from_dict(payload), self.ontology)

        payload = load_json(ADAPTER_PATH)
        payload["encounter"]["case_id"] = {"template": "{case_id!r}"}
        with self.assertRaisesRegex(ValueError, "simple field names"):
            AdapterDefinition.from_dict(payload)

    def test_registry_is_extensible_and_real_xlsx_is_discoverable(self):
        workbook = ROOT / "knowledge/sources/wound_care_clinical_rules_raw.xlsx"
        sheets = xlsx_sheet_names(workbook)
        self.assertGreater(len(sheets), 0)
        resource = ResourceDefinition(path=workbook.name, format="xlsx", sheet=sheets[0])
        registry = default_reader_registry()
        rows = registry.iter_rows(workbook.parent, resource, IngestionPolicy())
        first = next(rows)
        self.assertIn("_row_number", first)


if __name__ == "__main__":
    unittest.main()
