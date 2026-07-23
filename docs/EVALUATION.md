# Accuracy evaluation and backtest

Deterministic discovery is only credible if it is measured. The evaluation harness scores the
`RuleEngine` against a labeled gold set and reports precision / recall / F1. It is the honest,
reproducible source for any accuracy figure the product quotes — including the demo's precision
target. It never mutates a claim, assigns a DRG, or computes reimbursement; it only compares
engine output to human labels.

## Run it

```bash
make eval        # runs the shipped gold manifest with --enforce
# or directly:
revenue-integrity-eval examples/evaluation/gold_manifest.json --output output/eval-report.json
```

The command runs the deterministic engine over every case in the manifest, maps each `Finding`
to an `(encounter_id, category, key)` opportunity key, compares the predicted set to the gold
labels, and prints a canonical, hash-signed report.

## Manifest shape

```json
{
  "eval_schema_version": "1.0.0",
  "cases": [
    {
      "name": "pressure-injury-omitted-diagnosis",
      "case": "../case_pressure_injury.json",
      "rules": "../../rules/wound_care_v1.json",
      "expected": [{"category": "missed_diagnosis", "key": "L89.154", "valid": true}]
    }
  ],
  "thresholds": {"min_precision": 0.95, "min_recall": 0.95}
}
```

Paths are resolved relative to the manifest. Each label may omit `encounter_id`; it defaults to
the case's encounter. Labels accept either the canonical `{encounter_id, category, key, valid}`
shape or the legacy `{case_id, category, key, label}` shape — `load_labeled_opportunities`
normalizes both.

## Report

The report combines `EvaluationMetrics.to_dict()` (precision/recall/F1 + TP/FP/FN) with the engine
version, case/label counts, thresholds, a `passed` flag, and a `report_hash` covering every field.
It is explicitly marked `synthetic-gold-set-not-for-billing`. Running it twice yields byte-identical
output and a stable report hash.

## CI and gating

`--enforce` exits non-zero when the manifest thresholds are not met, so a regression in discovery
accuracy fails the build. On a small corpus, keep the threshold step advisory until the gold set is
large enough not to flap; grow the gold set (new labeled cases, contradictions, clean controls) as
the honest way to raise the quoted precision figure.

## Finding → opportunity mapping

`finding_to_opportunity_key` is a pure table lookup: an additive proposed change maps to
`missed_diagnosis` / `missed_procedure` / `missed_charge` with the first added code as the key;
otherwise the finding's disposition selects the category and the rule ID is the key. No floats and
no model output participate.
