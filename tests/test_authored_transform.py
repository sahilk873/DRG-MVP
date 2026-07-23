import sys
import unittest

from revenue_integrity.runtime import (
    AuthoredTransformDefinition,
    GoldenSample,
    KnowledgeStore,
    promote_transform,
    run_authored_transform,
    score_transform,
)

# A currency-string -> integer-cents transform: real, and impossible with the fixed DSL op set.
CURRENCY = (
    "def transform(value):\n"
    "    cleaned = value.replace('$', '').replace(',', '').strip()\n"
    "    dollars, _, cents = cleaned.partition('.')\n"
    "    cents = (cents + '00')[:2]\n"
    "    return int(dollars) * 100 + int(cents)\n"
)
CURRENCY_SAMPLES = [
    GoldenSample("$1,234.56", expected=123456),
    GoldenSample("$49,000.00", expected=4900000),
    GoldenSample("$8.20", expected=820),
]

# A regex extractor pulling a stage number out of free text.
REGEX = (
    "import re\n"
    "def transform(value):\n"
    "    match = re.search(r'stage\\s*(\\d+)', value, re.IGNORECASE)\n"
    "    return int(match.group(1)) if match else None\n"
)


@unittest.skipUnless(sys.platform != "win32", "authored transforms use the POSIX sandbox")
class AuthoredTransformTests(unittest.TestCase):
    def test_scores_and_runs_a_currency_transform(self):
        score = score_transform(CURRENCY, CURRENCY_SAMPLES)
        self.assertEqual(score.parse_rate, 1.0)
        self.assertTrue(score.exact_match)
        definition = AuthoredTransformDefinition("currency-cents", "1.0.0", "approved-for-demo", CURRENCY)
        out = run_authored_transform(definition, ["$1,234.56", "$8.20"], expected_hash=definition.code_hash)
        self.assertEqual(out, [123456, 820])

    def test_regex_transform_extracts_from_free_text(self):
        score = score_transform(
            REGEX,
            [GoldenSample("Documented STAGE 4 sacral injury", expected=4), GoldenSample("no stage here", expected=None)],
        )
        self.assertTrue(score.exact_match)

    def test_promote_records_a_governed_transform(self):
        store = KnowledgeStore()
        promoted, definition, score, reason = promote_transform(
            store, CURRENCY, transform_id="currency-cents", version="1.0.0",
            samples=CURRENCY_SAMPLES, status="approved-for-demo", require_exact=True,
        )
        self.assertTrue(promoted, reason)
        self.assertEqual(len(store), 1)
        self.assertEqual(store.exemplars("transform")[0].payload["code_hash"], definition.code_hash)

    def test_hash_freeze_and_status_gate(self):
        definition = AuthoredTransformDefinition("t", "1.0.0", "approved-for-demo", CURRENCY)
        with self.assertRaisesRegex(ValueError, "code hash does not match"):
            run_authored_transform(definition, ["$1.00"], expected_hash="0" * 64)
        draft = AuthoredTransformDefinition("t", "1.0.0", "draft", CURRENCY)
        with self.assertRaisesRegex(ValueError, "not executable"):
            run_authored_transform(draft, ["$1.00"])

    def test_broken_transform_is_rejected(self):
        store = KnowledgeStore()
        broken = "def transform(value):\n    return value['nope']\n"
        promoted, _, score, _ = promote_transform(
            store, broken, transform_id="bad", version="1.0.0",
            samples=[GoldenSample("$1.00")], status="approved-for-demo",
        )
        self.assertFalse(promoted)
        self.assertEqual(score.parse_rate, 0.0)
        self.assertEqual(len(store), 0)


if __name__ == "__main__":
    unittest.main()
