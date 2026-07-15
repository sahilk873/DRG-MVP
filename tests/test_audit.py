import unittest
from datetime import UTC, datetime

from revenue_integrity.audit import audit_record, canonical_hash


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
