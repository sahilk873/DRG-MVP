from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import Any, Mapping

from .denial_root_cause import denial_root_cause_findings
from .financial import FinancialSnapshot
from .grouper import Grouper, GroupingResult, derivation_pair
from .models import Assertion, Claim, Disposition, EncounterCase, Finding, ImpactStatus, RuleDomain
from .ontology import OntologyDefinition, OntologyEntity, load_builtin_ontology
from .rules import Condition, ProposedChange, RulePackage, RuleScope
from .subsumption import default_code_subsumption_table


ENGINE_VERSION = "0.7.0"


class RuleEngine:
    """Evaluate a validated declarative rule package without executing generated code."""

    def __init__(
        self,
        rule_package: RulePackage | Mapping[str, Any],
        grouper: Grouper,
        *,
        allow_unapproved: bool = False,
        ontology_definition: OntologyDefinition | None = None,
    ):
        self.rule_package = (
            rule_package
            if isinstance(rule_package, RulePackage)
            else RulePackage.from_dict(rule_package)
        )
        self.grouper = grouper
        if self.rule_package.status not in {"approved", "approved-for-demo"} and not allow_unapproved:
            raise ValueError(f"rule package status {self.rule_package.status!r} is not executable")
        try:
            self.ontology_definition = ontology_definition or load_builtin_ontology(
                self.rule_package.ontology.ontology_id,
                self.rule_package.ontology.version,
            )
        except ValueError as exc:
            raise ValueError("incompatible ontology definition for rule package") from exc
        binding = self.rule_package.ontology
        if (
            self.ontology_definition.ontology_id != binding.ontology_id
            or self.ontology_definition.version != binding.version
            or self.ontology_definition.digest != binding.digest
        ):
            raise ValueError("incompatible ontology definition for rule package")
        for rule in self.rule_package.rules:
            if unknown := set(rule.applies_to.subject_types) - set(self.ontology_definition.classes):
                raise ValueError(f"rule {rule.rule_id} scopes unknown ontology classes: {sorted(unknown)}")

    def evaluate(self, case: EncounterCase) -> list[Finding]:
        if (
            case.ontology.ontology_id != self.rule_package.ontology.ontology_id
            or case.ontology.ontology_version != self.rule_package.ontology.version
            or case.ontology.ontology_digest != self.rule_package.ontology.digest
        ):
            raise ValueError("rule package and encounter case use incompatible ontology definitions")
        baseline = self.grouper.group(case, case.claim)
        findings = self._baseline_findings(case, baseline)
        findings.extend(self._sequencing_findings(case, baseline))
        findings.extend(denial_root_cause_findings(case))
        entities = {entity.entity_id: entity for entity in case.ontology.entities}
        evidence_dates = {ev.evidence_id: ev.recorded_at for ev in case.evidence}
        # Day-0 semantics: the reference "now" for elapsed-day arithmetic is the encounter
        # discharge instant. Each assertion's Day 0 is the EARLIEST cited-evidence recorded_at
        # (grounded, never model-supplied). Missing/unparseable dates never fire (fail-safe).
        reference_date = case.discharged_at
        # Deterministic longitudinal facts derived in Python from the dated assessment series
        # (never model-supplied). Keyed by the WoundAssessment entity a rule can scope to.
        longitudinal_facts = _longitudinal_assessment_facts(case, reference_date)
        is_gap_domain = self.rule_package.rule_domain == RuleDomain.CLINICAL_CARE_GAP.value
        for rule in self.rule_package.rules:
            scoped = [
                assertion
                for assertion in case.assertions
                if self._matches_scope(entities[assertion.subject_id], rule.applies_to)
            ]
            scoped_payloads = [
                self._assertion_payload(
                    a, entities[a.subject_id], evidence_dates, reference_date,
                    longitudinal_facts=longitudinal_facts.get(a.subject_id),
                )
                for a in scoped
            ]
            matches = [
                assertion
                for assertion, payload in zip(scoped, scoped_payloads)
                if evaluate_condition(payload, rule.when, assertions=scoped_payloads)
            ]
            if not matches or not self._case_conditions(case, rule.case_conditions):
                continue

            proposed_claim = self._apply_change(case.claim, rule.action.proposed_change)
            simulated = self.grouper.group(case, proposed_claim)
            if simulated.grouper_version != baseline.grouper_version:
                raise ValueError("baseline and simulated results must use the same grouper version")

            ordered_matches = sorted(matches, key=lambda item: (-item.confidence, item.assertion_id))
            assertion_ids = tuple(item.assertion_id for item in ordered_matches)
            subject_ids = _ordered_unique(item.subject_id for item in ordered_matches)
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
            action = rule.action
            # A clinical_care_gap finding carries no monetary impact: analytics identify the
            # gap, clinicians decide. Its impact is NOT_APPLICABLE with a null estimate, and it
            # carries the walled-off clinical action fields instead of a claim mutation.
            if is_gap_domain:
                impact_status = ImpactStatus.NOT_APPLICABLE
                estimated_impact = None
                gap_kwargs: dict[str, Any] = {
                    "gap_domain": action.gap_domain,
                    "expected_action": action.expected_action,
                    "timing_window_days": action.timing_window_days,
                    "alert_urgency": action.alert_urgency,
                    "recommended_action": action.recommended_action,
                    "clinical_impact": action.clinical_impact,
                }
            else:
                impact_status = ImpactStatus.ESTIMATED
                estimated_impact = simulated.estimated_payment_cents - baseline.estimated_payment_cents
                gap_kwargs = {}
            findings.append(Finding(
                finding_id=f"finding-{digest}",
                rule_id=rule.rule_id,
                rule_package_id=self.rule_package.package_id,
                rule_package_version=self.rule_package.version,
                title=rule.title,
                disposition=action.disposition,
                confidence=ordered_matches[0].confidence,
                proposed_change=change,
                subject_ids=subject_ids,
                assertion_ids=assertion_ids,
                evidence_ids=evidence_ids,
                contradicting_evidence_ids=contradicting_ids,
                rationale=action.rationale,
                requires_human_review=action.requires_human_review,
                submitted_drg=case.claim.drg,
                current_drg=baseline.drg,
                simulated_drg=simulated.drg,
                estimated_impact_cents=estimated_impact,
                impact_status=impact_status,
                grouper_version=baseline.grouper_version,
                derivation=derivation_pair(baseline, simulated),
                **gap_kwargs,
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
        impact = None if submitted_payment is None else baseline.estimated_payment_cents - submitted_payment
        return [Finding(
            finding_id=f"finding-{digest}",
            rule_id="SYSTEM-DRG-REPRODUCTION",
            rule_package_id="deterministic-system-checks",
            rule_package_version=ENGINE_VERSION,
            title="Submitted DRG does not reproduce from the current coded claim",
            disposition=Disposition.CODING_REVIEW,
            confidence=1.0,
            proposed_change={"replace_drg": [baseline.drg]},
            subject_ids=(),
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
            impact_status=(
                ImpactStatus.UNAVAILABLE if submitted_payment is None else ImpactStatus.ESTIMATED
            ),
            grouper_version=baseline.grouper_version,
            derivation=derivation_pair(baseline, baseline),
        )]

    def _sequencing_findings(self, case: EncounterCase, baseline: GroupingResult) -> list[Finding]:
        """Deterministic DRG-sequencing counterfactual.

        Only fires when the claim carries per-diagnosis ``diagnosis_details``. It re-groups
        the encounter under the *documented* principal diagnosis (``sequence == 1``, then by
        ascending sequence) with the same POA/HAC-aware grouper, and compares the resulting
        DRG against the submitted DRG. When they differ, the documented sequencing and
        present-on-admission facts imply a different DRG than the one on the claim, so a SYSTEM
        finding is emitted for coding review. No language-model output is involved; this is a
        pure, order-canonicalized re-run of the deterministic grouper.
        """
        if not case.claim.diagnosis_details or case.claim.drg is None:
            return []
        counterfactual_claim = self._principal_first_claim(case.claim)
        counterfactual = self.grouper.group(case, counterfactual_claim)
        if counterfactual.grouper_version != baseline.grouper_version:
            raise ValueError("baseline and counterfactual results must use the same grouper version")
        if counterfactual.drg == case.claim.drg:
            return []
        principal = case.claim.principal_diagnosis()
        material = {
            "case_id": case.case_id,
            "check": "drg-sequencing",
            "submitted_drg": case.claim.drg,
            "resequenced_drg": counterfactual.drg,
            "principal_diagnosis": principal,
            "grouper_version": counterfactual.grouper_version,
        }
        digest = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]
        submitted_payment = case.claim.allowed_amount_cents
        impact = (
            None if submitted_payment is None
            else counterfactual.estimated_payment_cents - submitted_payment
        )
        return [Finding(
            finding_id=f"finding-{digest}",
            rule_id="SYSTEM-DRG-SEQUENCING",
            rule_package_id="deterministic-system-checks",
            rule_package_version=ENGINE_VERSION,
            title="Documented principal diagnosis and POA/HAC facts imply a different DRG",
            disposition=Disposition.CODING_REVIEW,
            confidence=1.0,
            proposed_change={"replace_drg": [counterfactual.drg]},
            subject_ids=(),
            assertion_ids=(),
            evidence_ids=(),
            contradicting_evidence_ids=(),
            rationale=(
                "Re-grouping the encounter under the documented principal diagnosis "
                f"({principal or 'unknown'}) and present-on-admission / HAC logic produced "
                f"DRG {counterfactual.drg}, which differs from the submitted DRG "
                f"{case.claim.drg}. Verify diagnosis sequencing and POA indicators before "
                "correcting the claim."
            ),
            requires_human_review=True,
            submitted_drg=case.claim.drg,
            current_drg=baseline.drg,
            simulated_drg=counterfactual.drg,
            estimated_impact_cents=impact,
            impact_status=(
                ImpactStatus.UNAVAILABLE if submitted_payment is None else ImpactStatus.ESTIMATED
            ),
            grouper_version=counterfactual.grouper_version,
            derivation=derivation_pair(baseline, counterfactual),
        )]

    @staticmethod
    def _principal_first_claim(claim: Claim) -> Claim:
        """Reorder the claim's diagnoses so the documented sequencing leads.

        Diagnoses referenced by ``diagnosis_details`` are ordered by ascending sequence
        (sequence 1 = principal first); any diagnoses without a detail keep their original
        relative order and follow. Deterministic and pure; ``diagnosis_details`` is preserved
        so the grouper still applies POA/HAC.
        """
        by_sequence = sorted(claim.diagnosis_details, key=lambda detail: detail.sequence)
        ordered: list[str] = []
        for detail in by_sequence:
            if detail.code not in ordered:
                ordered.append(detail.code)
        for code in claim.diagnoses:
            if code not in ordered:
                ordered.append(code)
        return replace(claim, diagnoses=tuple(ordered))

    @staticmethod
    def _assertion_payload(
        assertion: Assertion,
        subject: OntologyEntity,
        evidence_dates: Mapping[str, str] | None = None,
        reference_date: str | None = None,
        *,
        longitudinal_facts: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        evidence_dates = evidence_dates or {}
        # Day 0 for temporal operators: the EARLIEST cited-evidence recorded_at (grounded).
        cited = [
            evidence_dates[evidence_id]
            for evidence_id in assertion.evidence_ids
            if evidence_id in evidence_dates
        ]
        observed_at = min(cited, key=_iso_sort_key) if cited else None
        # Merge deterministic Python-derived longitudinal facts into the assertion's attributes
        # namespace so declarative rules read them as attributes.<key> without a new schema.
        # These are computed from the dated assessment series (size trend, reassessment overdue)
        # — never model-supplied. When the assessment carries a grounded observed_at date it
        # overrides the evidence-derived Day-0 anchor so temporal operators read the true date.
        attributes: Mapping[str, Any] = assertion.attributes
        if longitudinal_facts:
            merged = dict(assertion.attributes)
            merged.update(longitudinal_facts.get("attributes", {}))
            attributes = merged
            if longitudinal_facts.get("observed_at"):
                observed_at = longitudinal_facts["observed_at"]
        return {
            "assertion_id": assertion.assertion_id,
            "subject_id": assertion.subject_id,
            "concept": assertion.concept,
            "status": assertion.status.value,
            "documentation_status": assertion.documentation_status.value,
            "confidence": assertion.confidence,
            "attributes": attributes,
            # Deterministic, Python-derived read-only fields (never model-supplied) so rules
            # can reason about evidence strength and contradiction without a new schema.
            "evidence_ids": list(assertion.evidence_ids),
            "contradicting_evidence_ids": list(assertion.contradicting_evidence_ids),
            "evidence_count": len(assertion.evidence_ids),
            "contradicting_evidence_count": len(assertion.contradicting_evidence_ids),
            "has_contradicting_evidence": bool(assertion.contradicting_evidence_ids),
            # Grounded dates for the native temporal operators. ``observed_at`` is Day 0;
            # ``reference_date`` is the encounter-discharge "now". Both are ISO-8601 strings or
            # None; temporal operators never fire when either is missing (fail-safe).
            "observed_at": observed_at,
            "reference_date": reference_date,
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

    @classmethod
    def _matches_assertion(
        cls,
        assertion: Assertion,
        subject: OntologyEntity,
        condition: Condition,
    ) -> bool:
        return evaluate_condition(cls._assertion_payload(assertion, subject), condition)

    def _matches_scope(self, subject: OntologyEntity, scope: RuleScope) -> bool:
        if scope.include_subtypes:
            return any(
                self.ontology_definition.is_a(subject.entity_type, expected)
                for expected in scope.subject_types
            )
        return subject.entity_type in scope.subject_types

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
            # Deterministic, Python-derived read-only denial facts (never model-supplied) so
            # declarative rules can reason about payer denials without a new schema. All
            # zero/false when the case carries no financial snapshot; existing rules never
            # reference this branch, so grouper output and findings are unchanged.
            "financial": _financial_facts(case.financial),
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


def evaluate_condition(
    payload: Mapping[str, Any],
    condition: Condition,
    *,
    assertions: Sequence[Mapping[str, Any]] | None = None,
) -> bool:
    if condition.all_of:
        return all(evaluate_condition(payload, item, assertions=assertions) for item in condition.all_of)
    if condition.any_of:
        return any(evaluate_condition(payload, item, assertions=assertions) for item in condition.any_of)
    if condition.negate is not None:
        return not evaluate_condition(payload, condition.negate, assertions=assertions)
    if condition.co_occurs:
        return _evaluate_co_occurs(condition, assertions)

    actual = _resolve(payload, condition.field or "")
    expected = condition.value
    operator = condition.operator
    if operator == "exists":
        return (actual is not None) is (expected is not False)
    if operator in {"elapsed_days_gte", "elapsed_days_lte"}:
        return _evaluate_elapsed_days(operator, actual, payload.get("reference_date"), expected)
    if operator == "absent_within_days":
        # Assertion-set-aware absence operator: TRUE when NO assertion in the scoped set carries
        # a grounded date (at condition.field) within ``expected`` calendar days of the reference
        # date. Mirrors _evaluate_co_occurs' consumption of the assertion set. Fails safe (False)
        # when there is no set context or the reference date is missing/unparseable, so a bare
        # leaf never silently fires and ``not absent_within_days`` (present-within-window) stays
        # coherent.
        return _evaluate_absent_within_days(
            condition.field or "", payload.get("reference_date"), expected, assertions
        )
    if operator in {"pct_change_gte", "pct_change_lte"}:
        return _evaluate_pct_change(operator, actual, expected)
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
    if operator == "between":
        if isinstance(actual, bool) or not isinstance(actual, (int, float)):
            return False
        low, high = expected
        return low <= actual <= high
    if operator == "starts_with":
        return isinstance(actual, str) and actual.startswith(expected)
    if operator == "subsumed_by":
        # Pure declarative comparison against the governed subsumption reference: does the
        # coded value roll up to the referenced ancestor code? The table is verified
        # (version + self-describing digest) at load time and fails closed if tampered.
        if not isinstance(actual, str) or not isinstance(expected, str):
            return False
        return default_code_subsumption_table().subsumed_by(actual, expected)
    if operator in {"count_gte", "count_lte"}:
        # Count only real collections; never count string characters or mapping keys.
        if isinstance(actual, (str, bytes, Mapping)) or not isinstance(actual, (list, tuple)):
            return False
        return len(actual) >= expected if operator == "count_gte" else len(actual) <= expected
    raise ValueError(f"unsupported operator: {operator}")


def _parse_iso(value: Any) -> datetime | None:
    """Parse a grounded ISO-8601 date/datetime, or None if missing/unparseable (fail-safe)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed


def _iso_sort_key(value: str) -> datetime:
    parsed = _parse_iso(value)
    # Unparseable dates sort last so they never win min(); temporal ops fail safe on them. The
    # fallback is tz-AWARE (UTC) so min()/sorted() over a mix of tz-aware evidence dates and this
    # sentinel never raises "can't compare offset-naive and offset-aware datetimes".
    return parsed if parsed is not None else datetime.max.replace(tzinfo=timezone.utc)


def _calendar_days_between(start: datetime, end: datetime) -> int:
    """Elapsed CALENDAR days from ``start`` (Day 0) to ``end``.

    Compares calendar dates, so any same-day pair is 0 days regardless of clock time. When the
    two datetimes are timezone-aware they are compared in their own local date; both grounded
    dates in this engine carry a timezone (validated at case load), so this is deterministic.
    """
    return (end.date() - start.date()).days


def _evaluate_elapsed_days(operator: str, actual: Any, reference: Any, threshold: Any) -> bool:
    """Fire only when both the anchor date (Day 0) and the reference date are grounded.

    ``elapsed_days_gte`` fires when at least ``threshold`` calendar days have elapsed from the
    anchor to the reference; ``elapsed_days_lte`` fires when at most ``threshold`` have. Missing
    or unparseable dates never fire (fail-safe), and are noted by returning False.
    """
    anchor = _parse_iso(actual)
    ref = _parse_iso(reference)
    if anchor is None or ref is None:
        return False
    elapsed = _calendar_days_between(anchor, ref)
    if operator == "elapsed_days_gte":
        return elapsed >= threshold
    return elapsed <= threshold


def _evaluate_absent_within_days(
    field: str,
    reference: Any,
    threshold: Any,
    assertions: Sequence[Mapping[str, Any]] | None,
) -> bool:
    """Set-aware absence: fire when NO assertion has a dated ``field`` within the window.

    TRUE when no assertion in the scoped set carries a grounded date (resolved at ``field``)
    within ``threshold`` calendar days of the ``reference`` date. Fails safe (False) when there
    is no assertion-set context or the reference date is missing/unparseable — so a bare leaf
    never silently fires, and negating it (``not`` → present-within-window) stays coherent.
    Assertions whose ``field`` date is missing/unparseable do not count as "present" (they
    cannot prove the action occurred), mirroring the fail-safe posture of the other temporal ops.
    """
    if not assertions:
        return False
    ref = _parse_iso(reference)
    if ref is None:
        return False
    for candidate in assertions:
        observed = _parse_iso(_resolve(candidate, field))
        if observed is None:
            continue
        if abs(_calendar_days_between(observed, ref)) <= threshold:
            # An assertion IS present within the window: the action is not absent.
            return False
    return True


def _evaluate_pct_change(operator: str, actual: Any, threshold: Any) -> bool:
    """Percentage change from a grounded ``[baseline, current]`` numeric pair.

    pct = (current - baseline) / |baseline| * 100. Fails safe (False) when the value is not a
    numeric pair or the baseline is zero (undefined change). All arithmetic is deterministic
    Python; no model output participates.
    """
    if (
        not isinstance(actual, (list, tuple))
        or len(actual) != 2
        or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in actual)
    ):
        return False
    baseline, current = actual
    if baseline == 0:
        return False
    pct = (current - baseline) / abs(baseline) * 100
    if operator == "pct_change_gte":
        return pct >= threshold
    return pct <= threshold


def _evaluate_co_occurs(condition: Condition, assertions: Sequence[Mapping[str, Any]] | None) -> bool:
    """Every co_occurs sub-condition must be satisfied by SOME assertion in the scoped set.

    Different sub-conditions may be satisfied by different assertions. When ``window_days`` is
    set, the earliest and latest ``observed_at`` among the assertions that satisfied the
    sub-conditions must fall within that many calendar days; assertions with a missing
    ``observed_at`` never satisfy a windowed co_occurs (fail-safe). Bounded by the parser to
    MAX_CO_OCCURS_CONDITIONS sub-conditions, so this is linear in (sub-conditions x assertions).
    """
    if not assertions:
        return False
    window = condition.co_occurs_window_days
    matched_dates: list[datetime] = []
    for sub in condition.co_occurs:
        satisfier: datetime | None = None
        found = False
        for candidate in assertions:
            if not evaluate_condition(candidate, sub, assertions=assertions):
                continue
            found = True
            observed = _parse_iso(candidate.get("observed_at"))
            if window is None:
                break
            # For a windowed co_occurs each satisfier must contribute a grounded date.
            if observed is not None and (satisfier is None or observed < satisfier):
                satisfier = observed
        if not found:
            return False
        if window is not None:
            if satisfier is None:
                return False
            matched_dates.append(satisfier)
    if window is not None and matched_dates:
        span = _calendar_days_between(min(matched_dates), max(matched_dates))
        if span > window:
            return False
    return True


def _longitudinal_assessment_facts(
    case: EncounterCase, reference_date: str | None
) -> dict[str, Any]:
    """Deterministically derive per-assessment longitudinal facts from the dated series.

    For each ``WoundAssessment`` bound to an ontology entity (``subject_entity_id``) this returns
    a fact bundle keyed by that entity id. The bundle carries the assessment's grounded
    ``observed_at`` plus an ``attributes`` mapping a declarative rule can read via
    ``attributes.<key>``:

    - ``size_area_cm2``            planar area at this assessment (length x width),
    - ``size_trend_pct``          a ``[baseline_area, current_area]`` pair — baseline is the
                                  comparedWith prior's area when linked, else the first dated
                                  assessment's area — for the ``pct_change_*`` operators,
    - ``days_since_baseline``     calendar days from the baseline assessment to this one,
    - ``days_since_prior``        calendar days from the comparedWith prior to this one,
    - ``standard_care_documented`` / ``provider_reassessment`` echoed booleans,
    - ``reassessment_overdue``    True when standard care was documented, this assessment shows
                                  no area reduction vs baseline, and no provider reassessment is
                                  recorded on it (a deterministic follow-through gap signal).

    All arithmetic is Python; no language-model output participates. Returns an empty mapping
    for a legacy case with no ``assessments``.
    """
    facts: dict[str, Any] = {}
    if not case.assessments:
        return facts
    by_id = {a.assessment_id: a for a in case.assessments}
    baseline = next((a for a in case.assessments if a.size is not None), None)
    baseline_area = baseline.size.area_cm2 if baseline and baseline.size else None
    baseline_date = _parse_iso(baseline.observed_at) if baseline else None
    for assessment in case.assessments:
        if assessment.subject_entity_id is None:
            continue
        attributes: dict[str, Any] = {
            "standard_care_documented": assessment.standard_care_documented,
            "provider_reassessment": assessment.provider_reassessment,
        }
        observed = _parse_iso(assessment.observed_at)
        current_area = assessment.size.area_cm2 if assessment.size is not None else None
        if current_area is not None:
            attributes["size_area_cm2"] = current_area
        # Baseline for the trend: the comparedWith prior when linked, else the series baseline.
        prior = by_id.get(assessment.compared_with_id) if assessment.compared_with_id else None
        prior_area = prior.size.area_cm2 if prior and prior.size else baseline_area
        prior_date = _parse_iso(prior.observed_at) if prior else baseline_date
        if prior_area is not None and current_area is not None:
            attributes["size_trend_pct"] = [prior_area, current_area]
        if baseline_date is not None and observed is not None:
            attributes["days_since_baseline"] = _calendar_days_between(baseline_date, observed)
        if prior_date is not None and observed is not None:
            attributes["days_since_prior"] = _calendar_days_between(prior_date, observed)
        # Deterministic follow-through gap: standard care given, no area reduction vs baseline,
        # and no provider reassessment recorded on this dated assessment.
        no_reduction = (
            baseline_area is not None
            and current_area is not None
            and current_area >= baseline_area
        )
        attributes["reassessment_overdue"] = bool(
            assessment.standard_care_documented
            and no_reduction
            and not assessment.provider_reassessment
        )
        facts[assessment.subject_entity_id] = {
            "observed_at": assessment.observed_at,
            "attributes": attributes,
        }
    return facts


def _financial_facts(financial: FinancialSnapshot | None) -> dict[str, Any]:
    """Deterministic read-only denial facts for the case-condition payload.

    Derived purely from the immutable ``FinancialSnapshot`` (never from language-model
    output). Returns all-zero/false facts when the case has no financial snapshot, so the
    ``financial`` condition branch is always well-typed and additive.
    """
    if financial is None:
        return {"has_denials": False, "denied_amount_cents": 0, "denial_count": 0}
    denial_count = len(financial.denials)
    return {
        "has_denials": denial_count > 0,
        "denied_amount_cents": financial.denied_amount_cents,
        "denial_count": denial_count,
    }


def _resolve(payload: Mapping[str, Any], path: str) -> Any:
    value: Any = payload
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
