from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from collections.abc import Iterable
from typing import Any, Mapping

from .grouper import Grouper, GroupingResult
from .models import Assertion, Claim, Disposition, EncounterCase, Finding
from .ontology import OntologyEntity
from .rules import Condition, ProposedChange, RulePackage


ENGINE_VERSION = "0.3.0"


class RuleEngine:
    """Evaluate a validated declarative rule package without executing generated code."""

    def __init__(
        self,
        rule_package: RulePackage | Mapping[str, Any],
        grouper: Grouper,
        *,
        allow_unapproved: bool = False,
    ):
        self.rule_package = (
            rule_package
            if isinstance(rule_package, RulePackage)
            else RulePackage.from_dict(rule_package)
        )
        self.grouper = grouper
        if self.rule_package.status not in {"approved", "approved-for-demo"} and not allow_unapproved:
            raise ValueError(f"rule package status {self.rule_package.status!r} is not executable")

    def evaluate(self, case: EncounterCase) -> list[Finding]:
        if (
            case.ontology.ontology_id != self.rule_package.ontology.ontology_id
            or case.ontology.ontology_version != self.rule_package.ontology.version
        ):
            raise ValueError("rule package and encounter case use incompatible ontology definitions")
        baseline = self.grouper.group(case, case.claim)
        findings = self._baseline_findings(case, baseline)
        entities = {entity.entity_id: entity for entity in case.ontology.entities}
        for rule in self.rule_package.rules:
            matches = [
                assertion
                for assertion in case.assertions
                if self._matches_assertion(assertion, entities[assertion.subject_id], rule.when)
            ]
            if not matches or not self._case_conditions(case, rule.case_conditions):
                continue

            proposed_claim = self._apply_change(case.claim, rule.action.proposed_change)
            simulated = self.grouper.group(case, proposed_claim)
            if simulated.grouper_version != baseline.grouper_version:
                raise ValueError("baseline and simulated results must use the same grouper version")

            ordered_matches = sorted(matches, key=lambda item: (-item.confidence, item.assertion_id))
            assertion_ids = tuple(item.assertion_id for item in ordered_matches)
            evidence_ids = _ordered_unique(evidence for item in ordered_matches for evidence in item.evidence_ids)
            contradicting_ids = _ordered_unique(
                evidence for item in ordered_matches for evidence in item.contradicting_evidence_ids
            )
            change = rule.action.proposed_change.to_dict()
            finding_material = {
                "case_id": case.case_id,
                "package_id": self.rule_package.package_id,
                "package_version": self.rule_package.version,
                "rule_id": rule.rule_id,
                "change": change,
            }
            digest = hashlib.sha256(
                json.dumps(finding_material, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()[:16]
            findings.append(Finding(
                finding_id=f"finding-{digest}",
                rule_id=rule.rule_id,
                rule_package_id=self.rule_package.package_id,
                rule_package_version=self.rule_package.version,
                title=rule.title,
                disposition=rule.action.disposition,
                confidence=ordered_matches[0].confidence,
                proposed_change=change,
                assertion_ids=assertion_ids,
                evidence_ids=evidence_ids,
                contradicting_evidence_ids=contradicting_ids,
                rationale=rule.action.rationale,
                requires_human_review=rule.action.requires_human_review,
                submitted_drg=case.claim.drg,
                current_drg=baseline.drg,
                simulated_drg=simulated.drg,
                estimated_impact_cents=simulated.estimated_payment_cents - baseline.estimated_payment_cents,
                grouper_version=baseline.grouper_version,
            ))
        return findings

    @staticmethod
    def _baseline_findings(case: EncounterCase, baseline: GroupingResult) -> list[Finding]:
        if case.claim.drg is None or case.claim.drg == baseline.drg:
            return []
        material = {
            "case_id": case.case_id,
            "check": "drg-reproduction",
            "submitted_drg": case.claim.drg,
            "reproduced_drg": baseline.drg,
            "grouper_version": baseline.grouper_version,
        }
        digest = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]
        submitted_payment = case.claim.allowed_amount_cents
        impact = 0 if submitted_payment is None else baseline.estimated_payment_cents - submitted_payment
        return [Finding(
            finding_id=f"finding-{digest}",
            rule_id="SYSTEM-DRG-REPRODUCTION",
            rule_package_id="deterministic-system-checks",
            rule_package_version=ENGINE_VERSION,
            title="Submitted DRG does not reproduce from the current coded claim",
            disposition=Disposition.CODING_REVIEW,
            confidence=1.0,
            proposed_change={"replace_drg": [baseline.drg]},
            assertion_ids=(),
            evidence_ids=(),
            contradicting_evidence_ids=(),
            rationale=(
                "The configured grouper did not reproduce the submitted DRG from the current "
                "claim codes. Verify grouper version, claim inputs, sequencing and submitted "
                "DRG before correction."
            ),
            requires_human_review=True,
            submitted_drg=case.claim.drg,
            current_drg=baseline.drg,
            simulated_drg=baseline.drg,
            estimated_impact_cents=impact,
            grouper_version=baseline.grouper_version,
        )]

    @staticmethod
    def _matches_assertion(
        assertion: Assertion,
        subject: OntologyEntity,
        condition: Condition,
    ) -> bool:
        payload = {
            "assertion_id": assertion.assertion_id,
            "subject_id": assertion.subject_id,
            "concept": assertion.concept,
            "status": assertion.status.value,
            "documentation_status": assertion.documentation_status.value,
            "confidence": assertion.confidence,
            "attributes": assertion.attributes,
            "subject": {
                "entity_id": subject.entity_id,
                "entity_type": subject.entity_type,
                "label": subject.label,
                "concept": None if subject.concept is None else {
                    "system": subject.concept.system,
                    "code": subject.concept.code,
                    "display": subject.concept.display,
                },
                "properties": subject.properties,
            },
        }
        return evaluate_condition(payload, condition)

    @staticmethod
    def _case_conditions(case: EncounterCase, conditions: tuple[Condition, ...]) -> bool:
        payload = {
            "claim": {
                "diagnoses": case.claim.diagnoses,
                "procedures": case.claim.procedures,
                "charges": case.claim.charges,
                "drg": case.claim.drg,
            },
            "metadata": case.metadata,
        }
        return all(evaluate_condition(payload, condition) for condition in conditions)

    @staticmethod
    def _apply_change(claim: Claim, change: ProposedChange) -> Claim:
        collections = {
            "diagnoses": list(claim.diagnoses),
            "procedures": list(claim.procedures),
            "charges": list(claim.charges),
        }
        for collection_name in collections:
            for code in change.values.get(f"remove_{collection_name}", ()):
                collections[collection_name] = [
                    existing for existing in collections[collection_name] if existing != code
                ]
            for code in change.values.get(f"add_{collection_name}", ()):
                if code not in collections[collection_name]:
                    collections[collection_name].append(code)
        return replace(
            claim,
            diagnoses=tuple(collections["diagnoses"]),
            procedures=tuple(collections["procedures"]),
            charges=tuple(collections["charges"]),
        )


def evaluate_condition(payload: Mapping[str, Any], condition: Condition) -> bool:
    if condition.all_of:
        return all(evaluate_condition(payload, item) for item in condition.all_of)
    if condition.any_of:
        return any(evaluate_condition(payload, item) for item in condition.any_of)
    if condition.negate is not None:
        return not evaluate_condition(payload, condition.negate)

    actual = _resolve(payload, condition.field or "")
    expected = condition.value
    operator = condition.operator
    if operator == "exists":
        return (actual is not None) is (expected is not False)
    if actual is None:
        return False
    if operator == "eq":
        return actual == expected
    if operator == "ne":
        return actual != expected
    if operator == "in":
        return isinstance(expected, (list, tuple, set, frozenset)) and actual in expected
    if operator in {"contains", "not_contains"}:
        try:
            contains = actual is not None and expected in actual
        except TypeError:
            contains = False
        return contains if operator == "contains" else not contains
    if operator in {"gte", "lte"}:
        if isinstance(actual, bool) or isinstance(expected, bool):
            return False
        try:
            return actual >= expected if operator == "gte" else actual <= expected
        except TypeError:
            return False
    raise ValueError(f"unsupported operator: {operator}")


def _resolve(payload: Mapping[str, Any], path: str) -> Any:
    value: Any = payload
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
