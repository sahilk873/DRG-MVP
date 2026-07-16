import hashlib
import json
from pathlib import Path
import unittest
from zipfile import ZipFile


ROOT = Path(__file__).parents[1]


class KnowledgeSourceGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(
            (ROOT / "knowledge/wound_care_source_manifest.json").read_text(encoding="utf-8")
        )

    def test_committed_workbook_matches_manifest_checksum(self):
        workbook = ROOT / self.manifest["repository_path"]
        self.assertTrue(workbook.is_file(), f"missing governed source workbook: {workbook}")
        digest = hashlib.sha256(workbook.read_bytes()).hexdigest()
        self.assertEqual(digest, self.manifest["sha256"])

    def test_workbook_is_a_non_macro_two_sheet_xlsx(self):
        workbook = ROOT / self.manifest["repository_path"]
        with ZipFile(workbook) as archive:
            members = set(archive.namelist())
        self.assertIn("xl/worksheets/sheet1.xml", members)
        self.assertIn("xl/worksheets/sheet2.xml", members)
        self.assertFalse(any("vbaProject" in name or "externalLinks" in name for name in members))

    def test_raw_clinical_source_has_no_execution_or_claim_authority(self):
        self.assertEqual(self.manifest["execution_status"], "not_executable")
        self.assertEqual(self.manifest["review_status"], "clinical-review-required")
        governance = self.manifest["governance"]
        self.assertIs(governance["coding_authority"], False)
        self.assertIs(governance["claim_mutation_authority"], False)
        self.assertIs(governance["clinical_action_authority"], False)

    def test_ontology_sources_resolve_to_governed_manifests(self):
        ontology = json.loads(
            (ROOT / "src/revenue_integrity/data/wound_care_ontology_v1.json").read_text(encoding="utf-8")
        )
        source_ids = {item["source_id"] for item in ontology["sources"]}
        governed_ids = {
            self.manifest["source_id"],
            json.loads(
                (ROOT / "knowledge/disease_treatment_ontology_source.json").read_text(encoding="utf-8")
            )["source_id"],
        }
        self.assertEqual(source_ids, governed_ids)


if __name__ == "__main__":
    unittest.main()
