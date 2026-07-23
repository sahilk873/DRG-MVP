"""Deterministic accuracy / backtest harness for opportunity discovery.

Runs the deterministic ``RuleEngine`` over a manifest of labeled encounter cases and
reports precision / recall / F1 against the gold labels. This is the honest, reproducible
source for any accuracy figure the product quotes. It never mutates a claim, assigns a
DRG, or computes reimbursement — it only compares engine output to human labels.

Usage:
    revenue-integrity-eval <manifest.json> [--output report.json] [--enforce]

Manifest shape:
    {
      "eval_schema_version": "1.0.0",
      "cases": [
        {"name": "...", "case": "examples/case_pressure_injury.json",
         "rules": "rules/wound_care_v1.json",
         "expected": [{"category": "missed_diagnosis", "key": "L89.154", "valid": true}]}
      ],
      "thresholds": {"min_precision": 0.9, "min_recall": 0.9}   // optional
    }
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .engine import ENGINE_VERSION, RuleEngine
from .evaluation import (
    build_evaluation_report,
    evaluate_predictions,
    load_labeled_opportunities,
    predicted_keys_from_findings,
)
from .grouper import DeterministicDemoGrouper, default_demo_registry
from .models import EncounterCase


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score deterministic opportunity discovery against a labeled gold set."
    )
    parser.add_argument("manifest", type=Path, help="Evaluation manifest JSON file")
    parser.add_argument("--output", type=Path, help="Write the signed report atomically to this path")
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="Exit non-zero when manifest thresholds are not met",
    )
    parser.add_argument(
        "--allow-unapproved-rules",
        action="store_true",
        help="Allow non-approved rule packages for local development only",
    )
    return parser


def evaluate_manifest(manifest_path: Path, *, allow_unapproved: bool = False) -> dict[str, Any]:
    """Run the deterministic engine over a gold manifest and return the signed report.

    Reusable entry point for the CLI and the demo-fixture generator, so the demo's
    accuracy panel is produced by the exact same measurement path as ``make eval``.
    """
    manifest = _read_json_object(manifest_path)
    base = manifest_path.resolve().parent
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("manifest must contain a non-empty 'cases' array")

    labels: list[Any] = []
    predicted: set[Any] = set()
    for entry in cases:
        case = EncounterCase.from_dict(_read_json_object(_resolve(base, entry["case"])))
        findings = RuleEngine(
            _read_json_object(_resolve(base, entry["rules"])),
            DeterministicDemoGrouper(registry=default_demo_registry()),
            allow_unapproved=allow_unapproved,
        ).evaluate(case)
        predicted |= predicted_keys_from_findings(case.encounter_id, findings)
        # Default each label's encounter to this case unless it names its own.
        stamped = [{"encounter_id": case.encounter_id, **item} for item in entry.get("expected", [])]
        labels.extend(load_labeled_opportunities(stamped))

    thresholds = manifest.get("thresholds")
    return build_evaluation_report(
        evaluate_predictions(labels, predicted),
        engine_version=ENGINE_VERSION,
        case_count=len(cases),
        label_count=sum(1 for item in labels if item.valid),
        thresholds=thresholds if isinstance(thresholds, dict) else None,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = evaluate_manifest(args.manifest, allow_unapproved=args.allow_unapproved_rules)
        rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        if args.output:
            _atomic_write(args.output, rendered)
        else:
            sys.stdout.write(rendered)
        if args.enforce and report.get("passed") is False:
            print("error: evaluation did not meet manifest thresholds", file=sys.stderr)
            return 3
        return 0
    except (OSError, KeyError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _resolve(base: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else (base / path)


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _atomic_write(path: Path, content: str) -> None:
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())
