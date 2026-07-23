import sys
import unittest

from revenue_integrity.runtime import (
    AuthoredReaderDefinition,
    DeterministicRetriever,
    GoldenSample,
    KnowledgeStore,
    learn_from_review_log,
    promote_reader,
    run_authored_reader,
    score_reader,
)

# A pipe-delimited reader — a real format the built-in CSV/JSON/JSONL/XLSX readers don't cover.
PIPE_READER = (
    "def read(raw):\n"
    "    lines = [line for line in raw.splitlines() if line.strip()]\n"
    "    header = lines[0].split('|')\n"
    "    return [dict(zip(header, line.split('|'))) for line in lines[1:]]\n"
)
PIPE_DOC = "mrn|encounter_id|drg\nMRN-1|ENC-1|872\nMRN-2|ENC-2|641"
PIPE_ROWS = [
    {"mrn": "MRN-1", "encounter_id": "ENC-1", "drg": "872"},
    {"mrn": "MRN-2", "encounter_id": "ENC-2", "drg": "641"},
]


@unittest.skipUnless(sys.platform != "win32", "authored readers use the POSIX sandbox")
class AuthoredReaderTests(unittest.TestCase):
    def test_scores_a_correct_reader(self):
        score = score_reader(PIPE_READER, [GoldenSample(PIPE_DOC, expected=PIPE_ROWS)])
        self.assertEqual(score.parse_rate, 1.0)
        self.assertEqual(score.conformance, 1.0)
        self.assertTrue(score.exact_match)

    def test_promote_then_run_produces_rows(self):
        store = KnowledgeStore()
        promoted, definition, score, reason = promote_reader(
            store, PIPE_READER, reader_id="pipe-delimited", version="1.0.0",
            format_name="pipe-delimited", samples=[GoldenSample(PIPE_DOC, expected=PIPE_ROWS)],
            status="approved-for-demo", require_exact=True,
        )
        self.assertTrue(promoted, reason)
        self.assertEqual(len(store), 1)
        rows = run_authored_reader(definition, [PIPE_DOC], expected_hash=definition.code_hash)
        self.assertEqual(rows, PIPE_ROWS)

    def test_hash_mismatch_fails_closed(self):
        definition = AuthoredReaderDefinition("pipe", "1.0.0", "approved-for-demo", "pipe-delimited", PIPE_READER)
        with self.assertRaisesRegex(ValueError, "code hash does not match"):
            run_authored_reader(definition, [PIPE_DOC], expected_hash="0" * 64)

    def test_non_executable_status_cannot_run(self):
        definition = AuthoredReaderDefinition("pipe", "1.0.0", "draft", "pipe-delimited", PIPE_READER)
        with self.assertRaisesRegex(ValueError, "not executable"):
            run_authored_reader(definition, [PIPE_DOC])

    def test_reader_that_returns_wrong_shape_is_rejected(self):
        store = KnowledgeStore()
        bad = "def read(raw):\n    return raw.upper()\n"  # a string, not a list of rows
        promoted, definition, score, _ = promote_reader(
            store, bad, reader_id="bad", version="1.0.0", format_name="bad",
            samples=[GoldenSample(PIPE_DOC)], status="approved-for-demo",
        )
        self.assertFalse(promoted)
        self.assertEqual(score.conformance, 0.0)
        self.assertEqual(len(store), 0)

    def test_promoted_reader_is_retrievable_by_format(self):
        store = KnowledgeStore()
        promote_reader(
            store, PIPE_READER, reader_id="pipe", version="1.0.0", format_name="pipe-delimited",
            samples=[GoldenSample(PIPE_DOC, expected=PIPE_ROWS)], status="approved-for-demo",
        )
        result = DeterministicRetriever(store).retrieve(["format:pipe-delimited"], kind="reader", k=1)
        self.assertEqual(result.exemplars[0].payload["format"], "pipe-delimited")


class ReviewWriteBackTests(unittest.TestCase):
    def test_review_log_becomes_retrievable_precedent(self):
        store = KnowledgeStore()
        packet = {
            "packet_id": "packet-abc",
            "findings": [
                {"finding_id": "finding-1", "rule_id": "WC-PI-OMITTED-001", "disposition": "coding_review",
                 "proposed_change": {"add_diagnoses": ["L89.154"]}, "subject_ids": ["wound:1"]},
            ],
        }
        decisions = [
            {"finding_id": "finding-1", "action": "dismiss_with_reason", "reason_code": "documentation_not_supported",
             "actor_id": "coder-1", "decided_at": "2026-07-19T12:00:00Z"},
            {"finding_id": "missing", "action": "route_to_coding", "reason_code": "evidence_confirmed"},  # skipped
        ]
        recorded = learn_from_review_log(store, packet, decisions)
        self.assertEqual(len(recorded), 1)
        result = DeterministicRetriever(store).retrieve(
            ["rule:WC-PI-OMITTED-001", "code:L89.154"], kind="review_outcome", k=1,
        )
        self.assertEqual(result.exemplars[0].label, "dismiss_with_reason:documentation_not_supported")

    def test_projection_is_idempotent(self):
        store = KnowledgeStore()
        packet = {"packet_id": "p", "findings": [{"finding_id": "f", "rule_id": "R", "disposition": "coding_review",
                  "proposed_change": {}, "subject_ids": []}]}
        decisions = [{"finding_id": "f", "action": "route_to_coding", "reason_code": "evidence_confirmed"}]
        learn_from_review_log(store, packet, decisions)
        learn_from_review_log(store, packet, decisions)  # replay
        self.assertEqual(len(store), 1)


if __name__ == "__main__":
    unittest.main()
