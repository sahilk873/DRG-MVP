import sys
import unittest

from revenue_integrity.runtime import (
    GoldenSample,
    KnowledgeStore,
    SandboxLimits,
    evaluate_and_admit,
    run_sandboxed,
    score_artifact,
)

ADD = "def transform(row):\n    return row['a'] + row['b']\n"


@unittest.skipUnless(sys.platform != "win32", "sandbox requires POSIX resource limits")
class SandboxTests(unittest.TestCase):
    def test_runs_authored_code_over_rows(self):
        result = run_sandboxed(ADD, [{"a": 2, "b": 3}, {"a": 10, "b": 5}])
        self.assertTrue(result.ok)
        self.assertEqual([r.value for r in result.results], [5, 15])

    def test_is_deterministic(self):
        a = run_sandboxed(ADD, [{"a": 1, "b": 1}])
        b = run_sandboxed(ADD, [{"a": 1, "b": 1}])
        self.assertEqual([r.value for r in a.results], [r.value for r in b.results])

    def test_per_row_errors_are_isolated_not_fatal(self):
        result = run_sandboxed(ADD, [{"a": 1, "b": 2}, {"a": 1}])  # second row missing 'b'
        self.assertTrue(result.ok)
        self.assertTrue(result.results[0].ok)
        self.assertFalse(result.results[1].ok)
        self.assertIn("KeyError", result.results[1].error)

    def test_missing_entrypoint_fails_closed(self):
        result = run_sandboxed("x = 1\n", [{"a": 1}])
        self.assertFalse(result.ok)
        self.assertIn("must define a callable transform", result.error)

    def test_non_serializable_output_is_a_row_error(self):
        code = "def transform(row):\n    return object()\n"
        result = run_sandboxed(code, [{"a": 1}])
        self.assertTrue(result.ok)
        self.assertFalse(result.results[0].ok)

    def test_infinite_loop_is_contained(self):
        # Must not hang the host; CPU/wall limits kill the worker and we report failure.
        code = "def transform(row):\n    while True:\n        pass\n"
        result = run_sandboxed(code, [{"a": 1}], limits=SandboxLimits(cpu_seconds=1, wall_seconds=4))
        self.assertFalse(result.ok)

    def test_cannot_read_host_environment(self):
        # Empty env: a secret in the parent must not be visible to authored code.
        import os
        os.environ["RI_SANDBOX_SECRET"] = "top-secret"
        try:
            code = "import os\ndef transform(row):\n    return os.environ.get('RI_SANDBOX_SECRET', 'ABSENT')\n"
            result = run_sandboxed(code, [{}])
            self.assertEqual(result.results[0].value, "ABSENT")
        finally:
            del os.environ["RI_SANDBOX_SECRET"]


@unittest.skipUnless(sys.platform != "win32", "sandbox requires POSIX resource limits")
class SelfEvalTests(unittest.TestCase):
    def test_scores_a_correct_transform_highly(self):
        samples = [GoldenSample({"a": 1, "b": 2}, expected=3), GoldenSample({"a": 4, "b": 4}, expected=8)]
        score, _ = score_artifact(ADD, samples)
        self.assertEqual(score.parse_rate, 1.0)
        self.assertEqual(score.conformance, 1.0)
        self.assertTrue(score.exact_match)
        self.assertTrue(score.meets(require_exact=True))

    def test_scores_a_broken_transform_low(self):
        broken = "def transform(row):\n    return row['missing']\n"
        score, _ = score_artifact(broken, [GoldenSample({"a": 1})])
        self.assertEqual(score.parse_rate, 0.0)
        self.assertFalse(score.meets())

    def test_validator_drives_conformance(self):
        samples = [GoldenSample({"a": 1, "b": 2}), GoldenSample({"a": -5, "b": 1})]
        score, _ = score_artifact(ADD, samples, validator=lambda value: isinstance(value, int) and value >= 0)
        self.assertEqual(score.parse_rate, 1.0)
        self.assertEqual(score.conformance, 0.5)  # the negative-sum row fails the validator

    def test_end_to_end_generate_evaluate_promote(self):
        store = KnowledgeStore()
        samples = [GoldenSample({"a": 1, "b": 1}, expected=2)]
        promoted, score, reason = evaluate_and_admit(
            store, ADD, samples, artifact_id="sum-transform", kind="transform",
            features=["a", "b", "sum"], status="approved-for-demo", require_exact=True,
        )
        self.assertTrue(promoted, reason)
        self.assertTrue(score.meets(require_exact=True))
        self.assertEqual(len(store), 1)
        self.assertEqual(store.exemplars("transform")[0].payload["code"], ADD)

    def test_end_to_end_rejects_a_failing_artifact(self):
        store = KnowledgeStore()
        broken = "def transform(row):\n    return row['missing']\n"
        promoted, _, _ = evaluate_and_admit(
            store, broken, [GoldenSample({"a": 1})], artifact_id="bad", kind="transform",
            features=["a"], status="approved-for-demo",
        )
        self.assertFalse(promoted)
        self.assertEqual(len(store), 0)


if __name__ == "__main__":
    unittest.main()
