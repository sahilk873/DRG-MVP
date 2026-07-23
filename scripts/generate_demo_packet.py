from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Callable


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from revenue_integrity.engine import RuleEngine  # noqa: E402
from revenue_integrity.automation import build_automation_plan  # noqa: E402
from revenue_integrity.eval_cli import evaluate_manifest  # noqa: E402
from revenue_integrity.grouper import DeterministicDemoGrouper, default_demo_registry  # noqa: E402
from revenue_integrity.models import EncounterCase, ExceptionType, Finding, GapStatus  # noqa: E402
from revenue_integrity.review_packet import build_review_packet  # noqa: E402


FIXTURES_DIR = ROOT / "demo/src/fixtures"

# Each packet-backed demo case: (example case, rule package, packet file, plan file, fixed clock,
# workspace id, finding transform). The transform is an identity for the revenue-integrity cases so
# their generated packets stay byte-for-byte identical apart from already-bumped version/hash strings.
CASES = (
    (
        "examples/case_pressure_injury.json", "rules/wound_care_v1.json",
        "review-packet.json", "automation-plan.json",
        datetime(2026, 7, 17, 12, tzinfo=UTC),
        "workspace-revenue-integrity", None,
    ),
    (
        "examples/case_pressure_injury_v2.json", "rules/wound_care_v2.json",
        "review-packet-2.json", "automation-plan-2.json",
        datetime(2026, 7, 18, 12, tzinfo=UTC),
        "workspace-revenue-integrity", None,
    ),
    # BOTH-LENS showcase: the shipped clinical_care_gap package run against the synthetic
    # diabetic-foot-ulcer episode. Exercises every gap tier deterministically —
    #   CG-INF-002  urgent  -> escalated       (open urgent gap, care_gap lane)
    #   CG-DFU-001  routine -> auto_routed      (routine gap safely routed to the care team)
    #   CG-DFU-002  same_day-> suppressed       (a confirmed, undisputed exception downgrades it)
    # so the demo-packet gate covers the clinical-care-gap lens as well as revenue integrity.
    (
        "examples/case_diabetic_foot_ulcer_episode.json", "rules/wound_care_gaps_v1.json",
        "review-packet-gap.json", "automation-plan-gap.json",
        datetime(2026, 7, 19, 12, tzinfo=UTC),
        "workspace-clinical-care-gap", "_confirm_dfu_exception",
    ),
)


def _confirm_dfu_exception(findings: list[Finding]) -> list[Finding]:
    """Fold a recorded, evidence-grounded clinician exception into the CG-DFU-002 gap finding.

    This mirrors the C3 gap-closure lifecycle: a clinician confirmed the osteomyelitis workup is
    already being managed in outside care, so the surfaced gap is a legitimate non-gap. The
    deterministic automation policy honors the confirmed, undisputed exception by suppressing the
    finding (see ``automation._classify_care_gap``). No claim is mutated and the finding still
    carries its full evidence lineage; only the gap lifecycle fields change.
    """
    adjusted: list[Finding] = []
    for finding in findings:
        if finding.rule_id == "CG-DFU-002":
            finding = replace(
                finding,
                exception_checks=(
                    {
                        "exception_type": ExceptionType.OUTSIDE_CARE,
                        "evidence_id": finding.evidence_ids[0],
                        "status": "confirmed",
                    },
                ),
                gap_status=GapStatus.EXCEPTION,
                closed_at="2026-06-30T00:00:00Z",
                barrier_code="BARRIER-OUTSIDE-CARE",
            )
        adjusted.append(finding)
    return adjusted


def _build_pair(
    case_rel: str,
    rules_rel: str,
    clock: datetime,
    workspace_id: str = "workspace-revenue-integrity",
    transform: Callable[[list[Finding]], list[Finding]] | None = None,
) -> tuple[str, str]:
    case_payload = json.loads((ROOT / case_rel).read_text(encoding="utf-8"))
    rules = json.loads((ROOT / rules_rel).read_text(encoding="utf-8"))
    case = EncounterCase.from_dict(case_payload)
    findings = RuleEngine(rules, DeterministicDemoGrouper(registry=default_demo_registry())).evaluate(case)
    if transform is not None:
        findings = transform(findings)
    packet = build_review_packet(
        case=case,
        case_payload=case_payload,
        rule_package=rules,
        findings=findings,
        environment="synthetic",
        tenant_id="tenant-demo-alpha",
        workspace_id=workspace_id,
        clock=lambda: clock,
    )
    plan = build_automation_plan(
        findings,
        tenant_id=packet["tenant"]["tenant_id"],
        workspace_id=packet["tenant"]["workspace_id"],
        case_id=packet["case"]["case_id"],
        encounter_id=packet["case"]["encounter_id"],
        packet_id=packet["packet_id"],
        packet_hash=packet["provenance"]["packet_hash"],
        case=case,
    )
    return (
        json.dumps(packet, indent=2, ensure_ascii=False) + "\n",
        json.dumps(plan, indent=2, ensure_ascii=False) + "\n",
    )


#: Transforms referenced by name in ``CASES`` so the table stays declarative/serializable.
_TRANSFORMS: dict[str, Callable[[list[Finding]], list[Finding]]] = {
    "_confirm_dfu_exception": _confirm_dfu_exception,
}


def build_fixtures() -> list[tuple[Path, str]]:
    outputs: list[tuple[Path, str]] = []
    for case_rel, rules_rel, packet_name, plan_name, clock, workspace_id, transform_name in CASES:
        transform = _TRANSFORMS[transform_name] if transform_name else None
        packet_str, plan_str = _build_pair(case_rel, rules_rel, clock, workspace_id, transform)
        outputs.append((FIXTURES_DIR / packet_name, packet_str))
        outputs.append((FIXTURES_DIR / plan_name, plan_str))
    # Deterministic accuracy report for the demo's validation-gate panel, produced by the
    # exact same measurement path as `make eval`.
    report = evaluate_manifest(ROOT / "examples/evaluation/gold_manifest.json")
    outputs.append((FIXTURES_DIR / "evaluation-metrics.json", json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"))
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or verify the deterministic UI demo packets")
    parser.add_argument("--check", action="store_true", help="Fail when a committed fixture is stale")
    args = parser.parse_args()
    outputs = build_fixtures()
    if args.check:
        stale = [
            path for path, content in outputs
            if not path.is_file() or path.read_text(encoding="utf-8") != content
        ]
        if stale:
            print(
                "stale generated fixtures: " + ", ".join(str(path.relative_to(ROOT)) for path in stale),
                file=sys.stderr,
            )
            return 1
        return 0
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    for path, content in outputs:
        path.write_text(content, encoding="utf-8")
    print(" ".join(str(path.relative_to(ROOT)) for path, _ in outputs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
