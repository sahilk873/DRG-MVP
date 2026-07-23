from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import re
from typing import Any, Mapping

from .models import ClinicalUrgency, Disposition, GapDomain, RuleDomain


# Native temporal / percentage-change operators. Their arithmetic (calendar-day elapsed,
# absence-within-window, percentage change) is computed DETERMINISTICALLY in the engine from
# grounded dates/numbers — never by any language model. Declared here so malformed rules
# fail closed at parse time.
TEMPORAL_OPERATORS = frozenset({"elapsed_days_gte", "elapsed_days_lte", "absent_within_days"})
PCT_CHANGE_OPERATORS = frozenset({"pct_change_gte", "pct_change_lte"})
SUPPORTED_OPERATORS = frozenset({
    "eq", "ne", "gte", "lte", "in", "contains", "not_contains", "exists",
    "between", "starts_with", "count_gte", "count_lte", "subsumed_by",
}) | TEMPORAL_OPERATORS | PCT_CHANGE_OPERATORS
SUPPORTED_CHANGE_KEYS = frozenset({"add_diagnoses", "remove_diagnoses", "add_procedures", "remove_procedures", "add_charges", "remove_charges"})
# Domains this rule-package contract accepts. Each is walled off from the other in
# RuleAction.from_dict (see the domain wall below).
PERMITTED_RULE_DOMAINS = frozenset(domain.value for domain in RuleDomain)
# Optional clinical action fields a clinical_care_gap rule may carry. A revenue_integrity
# rule carrying ANY of these is rejected; a clinical_care_gap rule carrying a proposed_change
# payload is rejected. This is the structural wall between the two peer domains.
CLINICAL_ACTION_FIELDS = frozenset({
    "gap_domain", "expected_action", "timing_window_days",
    "recommended_action", "alert_urgency", "clinical_impact",
})
# Bound the co-occurrence combinator so it can never cause combinatorial blowup.
MAX_CO_OCCURS_CONDITIONS = 5
FIELD_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


@dataclass(frozen=True, slots=True)
class Condition:
    field: str | None = None
    operator: str | None = None
    value: Any = None
    all_of: tuple["Condition", ...] = ()
    any_of: tuple["Condition", ...] = ()
    negate: "Condition | None" = None
    # Co-occurrence combinator: every sub-condition must be satisfied by SOME assertion in the
    # matched assertion set (not necessarily the same one), optionally within a bounded window
    # of calendar days between the earliest and latest matching assertion. Bounded to
    # MAX_CO_OCCURS_CONDITIONS sub-conditions so it can never blow up combinatorially.
    co_occurs: tuple["Condition", ...] = ()
    co_occurs_window_days: int | float | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Condition":
        if not isinstance(data, Mapping):
            raise ValueError("condition must be an object")
        combinators = [key for key in ("all", "any", "not", "co_occurs") if key in data]
        if combinators:
            key = combinators[0]
            if key == "co_occurs":
                allowed_keys = {"co_occurs", "window_days"}
                if len(combinators) != 1 or set(data) - allowed_keys:
                    raise ValueError("condition.co_occurs may carry only an optional window_days")
                children = data["co_occurs"]
                if not isinstance(children, list) or len(children) < 2:
                    raise ValueError("condition.co_occurs requires at least two sub-conditions")
                if len(children) > MAX_CO_OCCURS_CONDITIONS:
                    raise ValueError(
                        f"condition.co_occurs allows at most {MAX_CO_OCCURS_CONDITIONS} sub-conditions"
                    )
                window = data.get("window_days")
                if window is not None and (
                    isinstance(window, bool) or not isinstance(window, (int, float)) or window < 0
                ):
                    raise ValueError("condition.co_occurs window_days must be a non-negative number")
                return cls(
                    co_occurs=tuple(cls.from_dict(item) for item in children),
                    co_occurs_window_days=window,
                )
            if len(combinators) != 1 or len(data) != 1:
                raise ValueError("condition combinators must be the only key at their level")
            if key == "not":
                if not isinstance(data[key], Mapping):
                    raise ValueError("condition.not must be an object")
                return cls(negate=cls.from_dict(data[key]))
            children = data[key]
            if not isinstance(children, list) or not children:
                raise ValueError(f"condition.{key} must be a non-empty array")
            parsed = tuple(cls.from_dict(item) for item in children)
            return cls(all_of=parsed) if key == "all" else cls(any_of=parsed)

        allowed = {"field", "op", "value"}
        if set(data) - allowed or "field" not in data or "op" not in data:
            raise ValueError("leaf condition requires field and op and contains no unknown keys")
        field_name = data["field"]
        operator = data["op"]
        if not isinstance(field_name, str) or not field_name:
            raise ValueError("condition.field must be a non-empty string")
        if not FIELD_PATH.fullmatch(field_name):
            raise ValueError("condition.field must be a dotted identifier path")
        if operator not in SUPPORTED_OPERATORS:
            raise ValueError(f"unsupported operator: {operator}")
        if operator != "exists" and "value" not in data:
            raise ValueError(f"operator {operator} requires a value")
        _validate_operator_value(operator, data.get("value"))
        return cls(field=field_name, operator=operator, value=data.get("value"))


def _is_real_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _optional_nonempty(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string when present")
    return value


def _validate_operator_value(operator: str, value: Any) -> None:
    """Fail-closed value-shape validation so malformed rules never load."""
    if operator == "between":
        if (
            not isinstance(value, list)
            or len(value) != 2
            or not all(_is_real_number(item) for item in value)
            or value[0] > value[1]
        ):
            raise ValueError("operator between requires a [low, high] pair of numbers with low <= high")
    elif operator in {"count_gte", "count_lte"}:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"operator {operator} requires a non-negative integer count")
    elif operator == "starts_with":
        if not isinstance(value, str) or not value:
            raise ValueError("operator starts_with requires a non-empty string value")
    elif operator == "subsumed_by":
        # The value is the more-general ancestor code the field's coded value must roll
        # up to per the governed subsumption reference. Purely declarative; the actual
        # hierarchy lookup happens deterministically in the engine, never here.
        if not isinstance(value, str) or not value:
            raise ValueError("operator subsumed_by requires a non-empty string code reference")
    elif operator in TEMPORAL_OPERATORS:
        # Value is a threshold in whole/fractional calendar days. The engine reads grounded
        # dates and computes elapsed days deterministically; here we only shape-check.
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"operator {operator} requires a non-negative number of days")
    elif operator in PCT_CHANGE_OPERATORS:
        # Value is a percentage-change threshold (may be negative for a decrease). The engine
        # computes the actual percentage change from a grounded [baseline, current] numeric
        # pair; here we only require a real number.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"operator {operator} requires a numeric percentage threshold")


@dataclass(frozen=True, slots=True)
class ProposedChange:
    values: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProposedChange":
        unknown = set(data) - SUPPORTED_CHANGE_KEYS
        if unknown:
            raise ValueError(f"unsupported proposed-change keys: {sorted(unknown)}")
        parsed: dict[str, tuple[str, ...]] = {}
        for key, raw in data.items():
            if not isinstance(raw, list) or any(not isinstance(item, str) or not item for item in raw):
                raise ValueError(f"proposed_change.{key} must be an array of non-empty strings")
            if len(raw) != len(set(raw)):
                raise ValueError(f"proposed_change.{key} must not contain duplicates")
            parsed[key] = tuple(raw)
        return cls(parsed)

    def to_dict(self) -> dict[str, list[str]]:
        return {key: list(values) for key, values in self.values.items()}


@dataclass(frozen=True, slots=True)
class RuleAction:
    disposition: Disposition
    requires_human_review: bool
    proposed_change: ProposedChange
    rationale: str
    # ---- clinical_care_gap action fields (OPTIONAL; only on clinical_care_gap rules) ----
    gap_domain: GapDomain | None = None
    expected_action: str | None = None
    timing_window_days: int | float | None = None
    recommended_action: str | None = None
    alert_urgency: ClinicalUrgency | None = None
    clinical_impact: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, rule_domain: str) -> "RuleAction":
        if not isinstance(data, Mapping):
            raise ValueError("rule action must be an object")
        base_required = {"disposition", "requires_human_review", "proposed_change", "rationale"}
        allowed = base_required | CLINICAL_ACTION_FIELDS
        if missing := base_required - set(data):
            raise ValueError(f"rule action missing fields: {sorted(missing)}")
        if unknown := set(data) - allowed:
            raise ValueError(f"rule action contains unknown fields: {sorted(unknown)}")
        if not isinstance(data["requires_human_review"], bool):
            raise ValueError("requires_human_review must be a boolean")
        if not isinstance(data["rationale"], str) or not data["rationale"].strip():
            raise ValueError("rule rationale must be a non-empty string")
        if not isinstance(data["proposed_change"], Mapping):
            raise ValueError("proposed_change must be an object")

        present_clinical = CLINICAL_ACTION_FIELDS & set(data)
        proposed_change = ProposedChange.from_dict(data["proposed_change"])

        # ---- THE DOMAIN WALL (structural, enforced at parse time) ----
        if rule_domain == RuleDomain.CLINICAL_CARE_GAP.value:
            # A clinical_care_gap rule must NOT mutate a claim.
            if proposed_change.values:
                raise ValueError(
                    "clinical_care_gap rules must not carry a proposed_change payload "
                    "(no add/remove diagnoses/procedures/charges)"
                )
        elif rule_domain == RuleDomain.REVENUE_INTEGRITY.value:
            # A revenue_integrity rule must NOT carry clinical action fields.
            if present_clinical:
                raise ValueError(
                    "revenue_integrity rules must not carry clinical action fields: "
                    f"{sorted(present_clinical)}"
                )
        else:  # pragma: no cover - RulePackage gates the domain before we get here.
            raise ValueError(f"unsupported rule_domain: {rule_domain}")

        gap_domain = GapDomain(data["gap_domain"]) if "gap_domain" in data else None
        alert_urgency = ClinicalUrgency(data["alert_urgency"]) if "alert_urgency" in data else None
        expected_action = _optional_nonempty(data.get("expected_action"), "expected_action")
        recommended_action = _optional_nonempty(data.get("recommended_action"), "recommended_action")
        clinical_impact = _optional_nonempty(data.get("clinical_impact"), "clinical_impact")
        timing_window_days = data.get("timing_window_days")
        if timing_window_days is not None and (
            isinstance(timing_window_days, bool)
            or not isinstance(timing_window_days, (int, float))
            or timing_window_days < 0
        ):
            raise ValueError("timing_window_days must be a non-negative number")

        action = cls(
            disposition=Disposition(data["disposition"]),
            requires_human_review=data["requires_human_review"],
            proposed_change=proposed_change,
            rationale=data["rationale"],
            gap_domain=gap_domain,
            expected_action=expected_action,
            timing_window_days=timing_window_days,
            recommended_action=recommended_action,
            alert_urgency=alert_urgency,
            clinical_impact=clinical_impact,
        )
        if action.proposed_change.values and not action.requires_human_review:
            raise ValueError("claim-affecting proposed changes require human review")
        # clinical_care_gap findings never bypass review.
        if rule_domain == RuleDomain.CLINICAL_CARE_GAP.value and not action.requires_human_review:
            raise ValueError("clinical_care_gap rules must require human review")
        return action


@dataclass(frozen=True, slots=True)
class RuleScope:
    subject_types: tuple[str, ...]
    include_subtypes: bool

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RuleScope":
        if not isinstance(data, Mapping) or set(data) != {"subject_types", "include_subtypes"}:
            raise ValueError("rule applies_to requires exactly subject_types and include_subtypes")
        subject_types = data["subject_types"]
        if (
            not isinstance(subject_types, list)
            or not subject_types
            or any(not isinstance(item, str) or not item for item in subject_types)
        ):
            raise ValueError("rule applies_to.subject_types must be a non-empty string array")
        if len(subject_types) != len(set(subject_types)):
            raise ValueError("rule applies_to.subject_types must not contain duplicates")
        if not isinstance(data["include_subtypes"], bool):
            raise ValueError("rule applies_to.include_subtypes must be a boolean")
        return cls(tuple(subject_types), data["include_subtypes"])


@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: str
    title: str
    applies_to: RuleScope
    when: Condition
    case_conditions: tuple[Condition, ...]
    action: RuleAction

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, rule_domain: str = RuleDomain.REVENUE_INTEGRITY.value) -> "Rule":
        if not isinstance(data, Mapping):
            raise ValueError("rule must be an object")
        required = {"rule_id", "title", "applies_to", "when", "then"}
        allowed = required | {"case_conditions"}
        if missing := required - set(data):
            raise ValueError(f"rule missing fields: {sorted(missing)}")
        if unknown := set(data) - allowed:
            raise ValueError(f"rule contains unknown fields: {sorted(unknown)}")
        rule_id, title = data["rule_id"], data["title"]
        if not isinstance(rule_id, str) or not rule_id or not isinstance(title, str) or not title:
            raise ValueError("rule_id and title must be non-empty strings")
        case_conditions = data.get("case_conditions", [])
        if not isinstance(case_conditions, list):
            raise ValueError("case_conditions must be an array")
        return cls(
            rule_id=rule_id,
            title=title,
            applies_to=RuleScope.from_dict(data["applies_to"]),
            when=Condition.from_dict(data["when"]),
            case_conditions=tuple(Condition.from_dict(item) for item in case_conditions),
            action=RuleAction.from_dict(data["then"], rule_domain=rule_domain),
        )


@dataclass(frozen=True, slots=True)
class OntologyBinding:
    ontology_id: str
    version: str
    digest: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OntologyBinding":
        if not isinstance(data, Mapping) or set(data) != {"ontology_id", "version", "digest"}:
            raise ValueError("rule package ontology requires exactly ontology_id, version and digest")
        ontology_id = data["ontology_id"]
        version = data["version"]
        digest = data["digest"]
        if not isinstance(ontology_id, str) or not ontology_id or not isinstance(version, str) or not version:
            raise ValueError("rule package ontology values must be non-empty strings")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("rule package ontology digest must be a lowercase SHA-256 hex digest")
        return cls(ontology_id, version, digest)


@dataclass(frozen=True, slots=True)
class RulePackage:
    package_id: str
    version: str
    rule_domain: str
    ontology: OntologyBinding
    status: str
    effective_from: str
    rules: tuple[Rule, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RulePackage":
        if not isinstance(data, Mapping):
            raise ValueError("rule package must be an object")
        required = {
            "package_id", "version", "rule_domain", "ontology", "status", "effective_from", "rules"
        }
        if set(data) != required:
            raise ValueError(f"rule package requires exactly: {sorted(required)}")
        for key in ("package_id", "version", "rule_domain", "status", "effective_from"):
            if not isinstance(data[key], str) or not data[key]:
                raise ValueError(f"rule package {key} must be a non-empty string")
        rule_domain = data["rule_domain"]
        if rule_domain not in PERMITTED_RULE_DOMAINS:
            raise ValueError(
                "this rule-package contract only permits the domains "
                f"{sorted(PERMITTED_RULE_DOMAINS)}"
            )
        if data["status"] not in {"approved", "approved-for-demo", "clinical-review-required"}:
            raise ValueError("rule package status is invalid")
        if not isinstance(data["rules"], list):
            raise ValueError("rule package rules must be an array")
        rules = tuple(Rule.from_dict(item, rule_domain=rule_domain) for item in data["rules"])
        ids = [rule.rule_id for rule in rules]
        if len(ids) != len(set(ids)):
            raise ValueError("rule IDs must be unique within a package")
        try:
            date.fromisoformat(data["effective_from"])
        except ValueError as exc:
            raise ValueError("rule package effective_from must be an ISO date") from exc
        return cls(
            package_id=data["package_id"],
            version=data["version"],
            rule_domain=data["rule_domain"],
            ontology=OntologyBinding.from_dict(data["ontology"]),
            status=data["status"],
            effective_from=data["effective_from"],
            rules=rules,
        )
