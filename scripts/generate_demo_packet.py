from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from revenue_integrity.engine import RuleEngine  # noqa: E402
from revenue_integrity.grouper import DeterministicDemoGrouper  # noqa: E402
from revenue_integrity.models import EncounterCase  # noqa: E402
from revenue_integrity.review_packet import build_review_packet  # noqa: E402


OUTPUT = ROOT / "demo/src/fixtures/review-packet.json"


def build_fixture() -> str:
    case_payload = json.loads((ROOT / "examples/case_pressure_injury.json").read_text(encoding="utf-8"))
    rules = json.loads((ROOT / "rules/wound_care_v1.json").read_text(encoding="utf-8"))
    case = EncounterCase.from_dict(case_payload)
    findings = RuleEngine(rules, DeterministicDemoGrouper()).evaluate(case)
    packet = build_review_packet(
        case=case,
        case_payload=case_payload,
        rule_package=rules,
        findings=findings,
        environment="synthetic",
        clock=lambda: datetime(2026, 7, 17, 12, tzinfo=UTC),
    )
    return json.dumps(packet, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or verify the deterministic UI demo packet")
    parser.add_argument("--check", action="store_true", help="Fail when the committed fixture is stale")
    args = parser.parse_args()
    expected = build_fixture()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != expected:
            print(f"stale generated fixture: {OUTPUT.relative_to(ROOT)}", file=sys.stderr)
            return 1
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(expected, encoding="utf-8")
    print(OUTPUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
