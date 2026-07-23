import unittest
from datetime import UTC, datetime

from revenue_integrity.audit import (
    RunUsage,
    audit_record,
    canonical_hash,
    verify_audit_chain,
    verify_audit_record,
)


class AuditTests(unittest.TestCase):
    def test_hash_is_order_independent_for_objects(self):
        self.assertEqual(canonical_hash({"a": 1, "b": 2}), canonical_hash({"b": 2, "a": 1}))

    def test_record_is_reproducible_with_an_injected_clock(self):
        clock = lambda: datetime(2026, 1, 1, tzinfo=UTC)
        arguments = {
            "case_payload": {"case_id": "case-1", "value": 1},
            "rule_package": {"package_id": "rules", "version": "1"},
            "findings": [],
            "clock": clock,
        }
        first = audit_record(**arguments)
        second = audit_record(**arguments)
        self.assertEqual(first, second)
        expected = canonical_hash({key: value for key, value in first.items() if key != "record_hash"})
        self.assertEqual(first["record_hash"], expected)

    def test_record_can_chain_to_a_previous_hash(self):
        record = audit_record(
            case_payload={"case_id": "case-1"},
            rule_package={"package_id": "rules", "version": "1"},
            findings=[],
            previous_record_hash="abc123",
        )
        self.assertEqual(record["previous_record_hash"], "abc123")

    def _chain(self):
        clock = lambda: datetime(2026, 1, 1, tzinfo=UTC)
        first = audit_record(
            case_payload={"case_id": "case-1", "value": 1},
            rule_package={"package_id": "rules", "version": "1"}, findings=[], clock=clock,
        )
        second = audit_record(
            case_payload={"case_id": "case-2", "value": 2},
            rule_package={"package_id": "rules", "version": "1"}, findings=[], clock=clock,
            previous_record_hash=first["record_hash"],
        )
        return [first, second]

    def test_verify_audit_record_detects_tampering(self):
        record = self._chain()[0]
        self.assertTrue(verify_audit_record(record))
        tampered = {**record, "case_id": "case-mutated"}
        self.assertFalse(verify_audit_record(tampered))

    def test_verify_audit_chain_accepts_a_valid_genesis_chain(self):
        result = verify_audit_chain(self._chain())
        self.assertTrue(result["ok"])
        self.assertIsNone(result["first_bad_index"])

    def test_verify_audit_chain_reports_broken_linkage_and_mutation(self):
        chain = self._chain()
        broken = [chain[0], {**chain[1], "previous_record_hash": "0" * 64}]
        result = verify_audit_chain(broken)
        self.assertFalse(result["ok"])
        self.assertEqual(result["first_bad_index"], 1)

        mutated = self._chain()
        mutated[0] = {**mutated[0], "case_id": "case-mutated"}
        mutated_result = verify_audit_chain(mutated)
        self.assertFalse(mutated_result["ok"])
        self.assertEqual(mutated_result["first_bad_index"], 0)

    def test_run_usage_cost_is_deterministic_and_integer_cents(self):
        usage = RunUsage(model_id="anthropic/claude-opus", input_tokens=1000, output_tokens=2000)
        # 1000 * 1500 + 2000 * 7500 = 16_500_000 micro-cents-per-1k -> /1_000_000 = 16.5 -> 17
        self.assertEqual(usage.estimated_cost_cents, 17)
        self.assertIsInstance(usage.estimated_cost_cents, int)
        # Same inputs => same cost.
        again = RunUsage(model_id="anthropic/claude-opus", input_tokens=1000, output_tokens=2000)
        self.assertEqual(usage.estimated_cost_cents, again.estimated_cost_cents)

    def test_run_usage_unknown_model_uses_default_rates(self):
        usage = RunUsage(model_id="mystery/model", input_tokens=1000, output_tokens=1000)
        # default: 1000*300 + 1000*1500 = 1_800_000 -> /1_000_000 = 1.8 -> 2
        self.assertEqual(usage.estimated_cost_cents, 2)

    def test_run_usage_zero_tokens_is_zero_cost(self):
        self.assertEqual(RunUsage(model_id="x", input_tokens=0, output_tokens=0).estimated_cost_cents, 0)

    def test_run_usage_rejects_negative_and_bool_tokens(self):
        with self.assertRaises(ValueError):
            RunUsage(model_id="x", input_tokens=-1, output_tokens=0)
        with self.assertRaises(ValueError):
            RunUsage(model_id="x", input_tokens=True, output_tokens=0)
        with self.assertRaises(ValueError):
            RunUsage(model_id="", input_tokens=0, output_tokens=0)

    def test_run_usage_round_trip_from_dict(self):
        usage = RunUsage.from_dict({"model_id": "anthropic/claude-haiku", "input_tokens": 5000, "output_tokens": 1000})
        rendered = usage.to_dict()
        self.assertEqual(rendered["estimated_cost_cents"], usage.estimated_cost_cents)
        self.assertEqual(rendered["model_id"], "anthropic/claude-haiku")
        self.assertEqual(rendered["run_usage_schema_version"], "1.0.0")
        with self.assertRaises(ValueError):
            RunUsage.from_dict({"model_id": "x", "input_tokens": 1, "output_tokens": 1, "extra": 1})
        with self.assertRaises(ValueError):
            RunUsage.from_dict({"model_id": "x", "input_tokens": 1})

    def test_audit_record_omits_run_usage_when_absent_byte_identical(self):
        clock = lambda: datetime(2026, 1, 1, tzinfo=UTC)
        arguments = {
            "case_payload": {"case_id": "case-1", "value": 1},
            "rule_package": {"package_id": "rules", "version": "1"},
            "findings": [],
            "clock": clock,
        }
        without = audit_record(**arguments)
        also_without = audit_record(**arguments, run_usage=None)
        self.assertNotIn("run_usage", without)
        self.assertEqual(without, also_without)

    def test_audit_record_includes_run_usage_and_is_hash_covered(self):
        clock = lambda: datetime(2026, 1, 1, tzinfo=UTC)
        arguments = {
            "case_payload": {"case_id": "case-1", "value": 1},
            "rule_package": {"package_id": "rules", "version": "1"},
            "findings": [],
            "clock": clock,
        }
        usage = RunUsage(model_id="anthropic/claude-opus", input_tokens=1000, output_tokens=2000)
        record = audit_record(**arguments, run_usage=usage)
        self.assertEqual(record["run_usage"], usage.to_dict())
        # Hash covers the usage: record verifies, tampering with usage breaks it.
        self.assertTrue(verify_audit_record(record))
        tampered = {**record, "run_usage": {**record["run_usage"], "estimated_cost_cents": 0}}
        self.assertFalse(verify_audit_record(tampered))
        # Different usage => different record_hash (usage participates in the digest).
        without = audit_record(**arguments)
        self.assertNotEqual(record["record_hash"], without["record_hash"])

    def test_verify_audit_chain_requires_null_genesis_predecessor(self):
        chain = self._chain()
        # Re-point the genesis record at a non-null predecessor and re-sign it.
        forged = {key: value for key, value in chain[0].items() if key != "record_hash"}
        forged["previous_record_hash"] = "f" * 64
        forged["record_hash"] = canonical_hash(forged)
        result = verify_audit_chain([forged])
        self.assertFalse(result["ok"])
        self.assertEqual(result["first_bad_index"], 0)
