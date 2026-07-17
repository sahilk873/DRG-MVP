from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from .audit import audit_record
from .engine import RuleEngine
from .grouper import DeterministicDemoGrouper
from .models import EncounterCase
from .ontology import load_ontology_definition
from .review_packet import REVIEW_PACKET_ENVIRONMENTS, build_review_packet


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate a validated encounter case against versioned revenue-integrity rules."
    )
    parser.add_argument("case", type=Path, help="Encounter-case JSON file")
    parser.add_argument("rules", type=Path, help="Rule-package JSON file")
    parser.add_argument(
        "--ontology-definition",
        type=Path,
        help="Custom ontology-definition JSON; packaged definitions are used by default",
    )
    parser.add_argument("--output", type=Path, help="Write the audit record atomically to this path")
    parser.add_argument(
        "--allow-unapproved-rules",
        action="store_true",
        help="Allow a non-approved package for local development only",
    )
    parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="Exit with status 3 when one or more findings are produced",
    )
    parser.add_argument(
        "--format",
        choices=("audit", "review-packet"),
        default="audit",
        help="Output the hash-chained audit record or the reviewer-application handoff contract",
    )
    parser.add_argument(
        "--environment",
        choices=tuple(sorted(REVIEW_PACKET_ENVIRONMENTS)),
        default="development",
        help="Label review-packet data without changing evaluation behavior",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        case_payload = _read_json_object(args.case)
        rules_payload = _read_json_object(args.rules)
        ontology_definition = (
            load_ontology_definition(args.ontology_definition)
            if args.ontology_definition
            else None
        )
        case = EncounterCase.from_dict(case_payload, ontology_definition=ontology_definition)
        findings = RuleEngine(
            rules_payload,
            DeterministicDemoGrouper(),
            allow_unapproved=args.allow_unapproved_rules,
            ontology_definition=ontology_definition,
        ).evaluate(case)
        if args.format == "review-packet":
            result = build_review_packet(
                case=case,
                case_payload=case_payload,
                rule_package=rules_payload,
                findings=findings,
                environment=args.environment,
            )
        else:
            result = audit_record(
                case_payload=case_payload,
                rule_package=rules_payload,
                findings=[finding.to_dict() for finding in findings],
            )
        rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            _atomic_write(args.output, rendered)
        else:
            sys.stdout.write(rendered)
        return 3 if args.fail_on_findings and findings else 0
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _atomic_write(path: Path, content: str) -> None:
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
