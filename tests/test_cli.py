import contextlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from revenue_integrity.cli import main


@contextlib.contextmanager
def _tmp_json(payload):
    descriptor, name = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        yield Path(name)
    finally:
        os.unlink(name)

ROOT = Path(__file__).parents[1]
CASE_V1 = ROOT / "examples" / "case_pressure_injury.json"
RULES_V1 = ROOT / "rules" / "wound_care_v1.json"
CASE_V2 = ROOT / "examples" / "case_pressure_injury_v2.json"
RULES_V2 = ROOT / "rules" / "wound_care_v2.json"
CASE_HAC = ROOT / "examples" / "case_pressure_injury_hac_sequencing.json"
GOLD_MANIFEST = ROOT / "examples" / "evaluation" / "gold_manifest.json"


def _run(argv):
    """Run the CLI, capturing stdout/stderr and the exit code."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class VerifySubcommandTest(unittest.TestCase):
    def test_verify_passes_on_shipped_case(self):
        code, out, _ = _run(["verify", str(CASE_V1)])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["artifact_kind"], "encounter-case")
        self.assertTrue(report["passed"])
        self.assertTrue(all(check["ok"] for check in report["checks"]))

    def test_verify_passes_on_review_packet(self):
        code, out, _ = _run([
            str(CASE_V1), str(RULES_V1),
            "--format", "review-packet",
            "--environment", "synthetic",
            "--tenant-id", "t", "--workspace-id", "w",
        ])
        self.assertEqual(code, 0)
        packet = json.loads(out)
        with _tmp_json(packet) as path:
            code, out, _ = _run(["verify", str(path)])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["artifact_kind"], "review-packet")
        self.assertTrue(report["passed"])

    def test_verify_fails_on_tampered_packet(self):
        code, out, _ = _run([
            str(CASE_V1), str(RULES_V1),
            "--format", "review-packet",
            "--environment", "synthetic",
            "--tenant-id", "t", "--workspace-id", "w",
        ])
        self.assertEqual(code, 0)
        packet = json.loads(out)
        packet["findings"] = []  # mutate content without recomputing packet_hash
        with _tmp_json(packet) as path:
            code, out, _ = _run(["verify", str(path)])
        self.assertEqual(code, 3)  # non-zero exit on failure
        report = json.loads(out)
        self.assertFalse(report["passed"])
        hash_check = next(c for c in report["checks"] if c["check"] == "review-packet-hash")
        self.assertFalse(hash_check["ok"])

    def test_verify_fails_on_tampered_audit_record(self):
        code, out, _ = _run([str(CASE_V1), str(RULES_V1)])
        self.assertEqual(code, 0)
        record = json.loads(out)
        record["engine_version"] = "tampered"
        with _tmp_json(record) as path:
            code, out, _ = _run(["verify", str(path)])
        self.assertEqual(code, 3)
        report = json.loads(out)
        self.assertEqual(report["artifact_kind"], "audit-record")
        self.assertFalse(report["passed"])

    def test_verify_missing_file_errors(self):
        code, _, err = _run(["verify", str(ROOT / "does-not-exist.json")])
        self.assertEqual(code, 2)
        self.assertIn("error:", err)


class CoverageSubcommandTest(unittest.TestCase):
    def test_coverage_reports_fired_and_uncovered_for_pair(self):
        code, out, _ = _run(["coverage", f"{CASE_V1}:{RULES_V1}"])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["case_count"], 1)
        self.assertEqual(report["rules"]["available"], ["WC-PI-OMITTED-001", "WC-PI-QUERY-002"])
        self.assertEqual(report["rules"]["fired"], ["WC-PI-OMITTED-001"])
        self.assertEqual(report["rules"]["uncovered"], ["WC-PI-QUERY-002"])
        self.assertEqual(report["cases"][0]["case_id"], "CASE-DEMO-001")
        self.assertIn("PressureInjury", report["cases"][0]["subject_types_exercised"])

    def test_coverage_aggregates_across_manifest(self):
        code, out, _ = _run(["coverage", str(GOLD_MANIFEST)])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["case_count"], 4)
        self.assertEqual(
            report["rules"]["fired"],
            [
                "SEP-SOI-ROM-001",
                "SYSTEM-DRG-REPRODUCTION",
                "SYSTEM-DRG-SEQUENCING",
                "WC-PI-OMITTED-001",
                "WC-PI-POA-002",
                "WC-PI-SEVERITY-001",
            ],
        )
        self.assertEqual(report["rules"]["uncovered"], ["SEP-QUERY-002", "WC-PI-QUERY-002"])

    def test_coverage_is_deterministic(self):
        first = _run(["coverage", str(GOLD_MANIFEST)])[1]
        second = _run(["coverage", str(GOLD_MANIFEST)])[1]
        self.assertEqual(first, second)

    def test_coverage_rejects_malformed_pair(self):
        code, _, err = _run(["coverage", "not-a-pair"])
        self.assertEqual(code, 2)
        self.assertIn("error:", err)


class BackwardCompatibilityTest(unittest.TestCase):
    def test_default_evaluate_path_unchanged(self):
        code, out, _ = _run([str(CASE_V1), str(RULES_V1)])
        self.assertEqual(code, 0)
        record = json.loads(out)
        self.assertIn("record_hash", record)
        self.assertIn("findings", record)


if __name__ == "__main__":
    unittest.main()
