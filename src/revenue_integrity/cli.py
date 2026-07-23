from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from .audit import audit_record, verify_audit_chain, verify_audit_record
from .engine import RuleEngine
from .grouper import DeterministicDemoGrouper, default_demo_registry
from .models import EncounterCase
from .ontology import load_ontology_definition
from .review_packet import (
    REVIEW_PACKET_ENVIRONMENTS,
    REVIEW_PACKET_SCHEMA_VERSION,
    build_review_packet,
    verify_review_packet_hash,
)

_SUBCOMMANDS = ("verify", "coverage")


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
    parser.add_argument("--tenant-id", help="Required tenant scope for review-packet output")
    parser.add_argument("--workspace-id", help="Required workspace scope for review-packet output")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Additive subcommands. The historical form ``revenue-integrity <case> <rules>``
    # has no subcommand and is preserved verbatim below.
    if argv and argv[0] in _SUBCOMMANDS:
        if argv[0] == "verify":
            return _verify_main(argv[1:])
        return _coverage_main(argv[1:])
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
            DeterministicDemoGrouper(registry=default_demo_registry()),
            allow_unapproved=args.allow_unapproved_rules,
            ontology_definition=ontology_definition,
        ).evaluate(case)
        if args.format == "review-packet":
            if not args.tenant_id or not args.workspace_id:
                raise ValueError("--tenant-id and --workspace-id are required for review-packet output")
            if args.environment == "production":
                raise ValueError("the CLI demo grouper is not approved for production")
            result = build_review_packet(
                case=case,
                case_payload=case_payload,
                rule_package=rules_payload,
                findings=findings,
                environment=args.environment,
                tenant_id=args.tenant_id,
                workspace_id=args.workspace_id,
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


def _build_verify_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="revenue-integrity verify",
        description=(
            "Re-run the deterministic verifications over an encounter-case or a "
            "review-packet artifact and print a pass/fail report. Non-zero exit on failure."
        ),
    )
    parser.add_argument("artifact", type=Path, help="Encounter-case, review-packet, or audit-record JSON file")
    parser.add_argument(
        "--ontology-definition",
        type=Path,
        help="Custom ontology-definition JSON; packaged definitions are used by default",
    )
    parser.add_argument("--output", type=Path, help="Write the verification report atomically to this path")
    return parser


def _verify_main(argv: list[str]) -> int:
    args = _build_verify_parser().parse_args(argv)
    try:
        payload = _read_json_object(args.artifact)
        ontology_definition = (
            load_ontology_definition(args.ontology_definition)
            if args.ontology_definition
            else None
        )
        report = _verify_artifact(payload, ontology_definition=ontology_definition)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.output:
        _atomic_write(args.output, rendered)
    else:
        sys.stdout.write(rendered)
    return 0 if report["passed"] else 3


def _verify_artifact(
    payload: dict[str, Any],
    *,
    ontology_definition: Any | None = None,
) -> dict[str, Any]:
    """Re-run the shipped deterministic verifications; add no new trust surface.

    Detects the artifact kind (review packet / audit record / raw encounter case) and
    runs only the checks that apply. Every check reuses an existing validator. The
    result is a deterministic pass/fail report; it never mutates a claim, DRG, or payment.
    """
    checks: list[dict[str, Any]] = []

    if "review_packet_schema_version" in payload:
        artifact_kind = "review-packet"
        _verify_review_packet(payload, checks, ontology_definition=ontology_definition)
    elif "record_hash" in payload and "case_hash" in payload:
        artifact_kind = "audit-record"
        _verify_audit_artifact(payload, checks)
    else:
        artifact_kind = "encounter-case"
        _verify_case_payload(payload, checks, ontology_definition=ontology_definition)

    passed = all(check["ok"] for check in checks)
    return {"artifact_kind": artifact_kind, "passed": passed, "checks": checks}


def _record_check(checks: list[dict[str, Any]], name: str, ok: bool, detail: str) -> None:
    checks.append({"check": name, "ok": ok, "detail": detail})


def _verify_case_payload(
    payload: dict[str, Any],
    checks: list[dict[str, Any]],
    *,
    ontology_definition: Any | None = None,
) -> EncounterCase | None:
    """Load a raw encounter case; ``from_dict`` re-runs schema, lineage, financial-lineage,
    and ontology-graph (evidence grounding) validation and fails closed on any drift."""
    try:
        case = EncounterCase.from_dict(payload, ontology_definition=ontology_definition)
    except (TypeError, ValueError) as exc:
        _record_check(checks, "encounter-case-validation", False, str(exc))
        return None
    _record_check(
        checks,
        "encounter-case-validation",
        True,
        "schema, evidence/assertion lineage, financial lineage, and ontology graph validated",
    )
    _record_check(
        checks,
        "financial-lineage",
        True,
        "present" if case.financial is not None else "no financial snapshot (not applicable)",
    )
    return case


def _verify_review_packet(
    payload: dict[str, Any],
    checks: list[dict[str, Any]],
    *,
    ontology_definition: Any | None = None,
) -> None:
    version = payload.get("review_packet_schema_version")
    _record_check(
        checks,
        "review-packet-schema-version",
        version == REVIEW_PACKET_SCHEMA_VERSION,
        f"expected {REVIEW_PACKET_SCHEMA_VERSION!r}, found {version!r}",
    )
    _record_check(
        checks,
        "review-packet-hash",
        verify_review_packet_hash(payload),
        "recomputed packet_hash matches provenance",
    )
    controls = payload.get("controls")
    mutation_forbidden = isinstance(controls, dict) and controls.get("claim_mutation_allowed") is False
    _record_check(
        checks,
        "claim-mutation-forbidden",
        mutation_forbidden,
        "controls.claim_mutation_allowed must be false",
    )
    # Reconstruct the embedded encounter case and re-run the case-level verifications.
    case_section = payload.get("case")
    if isinstance(case_section, dict) and isinstance(payload.get("evidence"), list):
        # The packet surfaces a read-only deep-link ``source_locator`` on each evidence item
        # (kind == "clinical_note_excerpt" / "structured_source_record"). That locator is a
        # packet-only projection; strip it before reconstructing the embedded case so
        # ``Evidence.from_dict`` validates the original grounding shape and never mistakes a
        # deep-link for an adapter locator.
        evidence_for_case = []
        for item in payload.get("evidence"):
            if isinstance(item, dict) and isinstance(item.get("source_locator"), dict) and "kind" in item["source_locator"]:
                item = {key: value for key, value in item.items() if key != "source_locator"}
            evidence_for_case.append(item)
        embedded = {
            "schema_version": case_section.get("schema_version"),
            "case_id": case_section.get("case_id"),
            "patient_id": case_section.get("patient_id"),
            "encounter_id": case_section.get("encounter_id"),
            "admitted_at": case_section.get("admitted_at"),
            "discharged_at": case_section.get("discharged_at"),
            "metadata": case_section.get("metadata", {}),
            "claim": case_section.get("claim"),
            "evidence": evidence_for_case,
            "ontology": payload.get("ontology"),
            "assertions": payload.get("assertions"),
        }
        provenance = payload.get("provenance")
        # The packet omits the extraction provenance block; skip case reconstruction when
        # it is not recoverable rather than fabricating one (fail closed, not open).
        if isinstance(provenance, dict) and "extraction_provenance" in provenance:
            embedded["provenance"] = provenance["extraction_provenance"]
            _verify_case_payload(embedded, checks, ontology_definition=ontology_definition)


def _verify_audit_artifact(payload: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    _record_check(
        checks,
        "audit-record-hash",
        verify_audit_record(payload),
        "recomputed record_hash matches",
    )
    chain = verify_audit_chain([payload])
    _record_check(checks, "audit-chain-linkage", bool(chain["ok"]), str(chain["reason"]))


def _build_coverage_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="revenue-integrity coverage",
        description=(
            "Report which governed rules and ontology subject types were exercised versus "
            "available across the given case/rule pairs. Deterministic; runs no model."
        ),
    )
    parser.add_argument(
        "pair",
        nargs="+",
        metavar="CASE:RULES",
        help="One or more 'case.json:rules.json' pairs, or a coverage-manifest JSON file",
    )
    parser.add_argument(
        "--ontology-definition",
        type=Path,
        help="Custom ontology-definition JSON; packaged definitions are used by default",
    )
    parser.add_argument("--output", type=Path, help="Write the coverage report atomically to this path")
    parser.add_argument(
        "--allow-unapproved-rules",
        action="store_true",
        help="Allow non-approved rule packages for local development only",
    )
    return parser


def _coverage_main(argv: list[str]) -> int:
    args = _build_coverage_parser().parse_args(argv)
    try:
        pairs = _resolve_coverage_pairs(args.pair)
        ontology_definition = (
            load_ontology_definition(args.ontology_definition)
            if args.ontology_definition
            else None
        )
        report = _coverage_report(
            pairs,
            ontology_definition=ontology_definition,
            allow_unapproved=args.allow_unapproved_rules,
        )
    except (OSError, KeyError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.output:
        _atomic_write(args.output, rendered)
    else:
        sys.stdout.write(rendered)
    return 0


def _resolve_coverage_pairs(values: list[str]) -> list[tuple[Path, Path]]:
    """Accept either N ``case:rules`` pairs or a single coverage-manifest JSON file."""
    if len(values) == 1 and ":" not in values[0] and values[0].endswith(".json"):
        manifest_path = Path(values[0])
        manifest = _read_json_object(manifest_path)
        base = manifest_path.resolve().parent
        cases = manifest.get("cases")
        if not isinstance(cases, list) or not cases:
            raise ValueError("coverage manifest must contain a non-empty 'cases' array")
        pairs: list[tuple[Path, Path]] = []
        for entry in cases:
            if not isinstance(entry, dict) or "case" not in entry or "rules" not in entry:
                raise ValueError("each coverage-manifest case requires 'case' and 'rules'")
            pairs.append((_resolve_relative(base, entry["case"]), _resolve_relative(base, entry["rules"])))
        return pairs
    pairs = []
    for value in values:
        case_str, sep, rules_str = value.partition(":")
        if not sep or not case_str or not rules_str:
            raise ValueError(f"expected 'case.json:rules.json', got {value!r}")
        pairs.append((Path(case_str), Path(rules_str)))
    return pairs


def _resolve_relative(base: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else (base / path)


def _coverage_report(
    pairs: list[tuple[Path, Path]],
    *,
    ontology_definition: Any | None = None,
    allow_unapproved: bool = False,
) -> dict[str, Any]:
    """Deterministic rule/concept coverage across the given case/rule pairs.

    Reuses the exact ``RuleEngine`` evaluation path, so a rule is reported "fired" only if
    it would fire in a real evaluation. Reports fired rule ids, uncovered rules (available
    but never fired), and the ontology subject types exercised versus scoped. Never mutates
    a claim, DRG, or payment.
    """
    from .rules import RulePackage

    fired_rules: set[str] = set()
    available_rules: set[str] = set()
    exercised_subject_types: set[str] = set()
    scoped_subject_types: set[str] = set()
    per_case: list[dict[str, Any]] = []

    for case_path, rules_path in pairs:
        case = EncounterCase.from_dict(
            _read_json_object(case_path), ontology_definition=ontology_definition
        )
        rules_payload = _read_json_object(rules_path)
        package = RulePackage.from_dict(rules_payload)
        for rule in package.rules:
            available_rules.add(rule.rule_id)
            scoped_subject_types.update(rule.applies_to.subject_types)
        engine = RuleEngine(
            package,
            DeterministicDemoGrouper(registry=default_demo_registry()),
            allow_unapproved=allow_unapproved,
            ontology_definition=ontology_definition,
        )
        findings = engine.evaluate(case)
        entities = {entity.entity_id: entity for entity in case.ontology.entities}
        case_fired = sorted({finding.rule_id for finding in findings})
        case_subject_types: set[str] = set()
        for finding in findings:
            for subject_id in finding.subject_ids:
                entity = entities.get(subject_id)
                if entity is not None:
                    case_subject_types.add(entity.entity_type)
        fired_rules.update(case_fired)
        exercised_subject_types.update(case_subject_types)
        per_case.append(
            {
                "case_id": case.case_id,
                "encounter_id": case.encounter_id,
                "rule_package_id": package.package_id,
                "rule_package_version": package.version,
                "fired_rule_ids": case_fired,
                "subject_types_exercised": sorted(case_subject_types),
            }
        )

    return {
        "case_count": len(pairs),
        "rules": {
            "available": sorted(available_rules),
            "fired": sorted(fired_rules),
            "uncovered": sorted(available_rules - fired_rules),
        },
        "subject_types": {
            "scoped": sorted(scoped_subject_types),
            "exercised": sorted(exercised_subject_types),
            "unexercised": sorted(scoped_subject_types - exercised_subject_types),
        },
        "cases": per_case,
    }


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
