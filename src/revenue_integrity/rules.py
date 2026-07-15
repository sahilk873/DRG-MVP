from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import re
from typing import Any, Mapping

from .models import Disposition


SUPPORTED_OPERATORS = frozenset({"eq", "ne", "gte", "lte", "in", "contains", "not_contains", "exists"})
SUPPORTED_CHANGE_KEYS = frozenset({"add_diagnoses", "remove_diagnoses", "add_procedures", "remove_procedures", "add_charges", "remove_charges"})
FIELD_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


@dataclass(frozen=True, slots=True)
class Condition:
    field: str | None = None
    operator: str | None = None
    value: Any = None
    all_of: tuple["Condition", ...] = ()
    any_of: tuple["Condition", ...] = ()
    negate: "Condition | None" = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Condition":
        if not isinstance(data, Mapping):
            raise ValueError("condition must be an object")
        combinators = [key for key in ("all", "any", "not") if key in data]
        if combinators:
            if len(combinators) != 1 or len(data) != 1:
                raise ValueError("condition combinators must be the only key at their level")
            key = combinators[0]
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
        return cls(field=field_name, operator=operator, value=data.get("value"))


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

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RuleAction":
        if not isinstance(data, Mapping):
            raise ValueError("rule action must be an object")
        required = {"disposition", "requires_human_review", "proposed_change", "rationale"}
        if set(data) != required:
            raise ValueError(f"rule action requires exactly: {sorted(required)}")
        if not isinstance(data["requires_human_review"], bool):
            raise ValueError("requires_human_review must be a boolean")
        if not isinstance(data["rationale"], str) or not data["rationale"].strip():
            raise ValueError("rule rationale must be a non-empty string")
        if not isinstance(data["proposed_change"], Mapping):
            raise ValueError("proposed_change must be an object")
        action = cls(
            disposition=Disposition(data["disposition"]),
            requires_human_review=data["requires_human_review"],
            proposed_change=ProposedChange.from_dict(data["proposed_change"]),
            rationale=data["rationale"],
        )
        if action.proposed_change.values and not action.requires_human_review:
            raise ValueError("claim-affecting proposed changes require human review")
        return action


@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: str
    title: str
    when: Condition
    case_conditions: tuple[Condition, ...]
    action: RuleAction

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Rule":
        if not isinstance(data, Mapping):
            raise ValueError("rule must be an object")
        required = {"rule_id", "title", "when", "then"}
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
            when=Condition.from_dict(data["when"]),
            case_conditions=tuple(Condition.from_dict(item) for item in case_conditions),
            action=RuleAction.from_dict(data["then"]),
        )


@dataclass(frozen=True, slots=True)
class RulePackage:
    package_id: str
    version: str
    status: str
    effective_from: str
    rules: tuple[Rule, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RulePackage":
        if not isinstance(data, Mapping):
            raise ValueError("rule package must be an object")
        required = {"package_id", "version", "status", "effective_from", "rules"}
        if set(data) != required:
            raise ValueError(f"rule package requires exactly: {sorted(required)}")
        for key in ("package_id", "version", "status", "effective_from"):
            if not isinstance(data[key], str) or not data[key]:
                raise ValueError(f"rule package {key} must be a non-empty string")
        if data["status"] not in {"approved", "approved-for-demo", "clinical-review-required"}:
            raise ValueError("rule package status is invalid")
        if not isinstance(data["rules"], list):
            raise ValueError("rule package rules must be an array")
        rules = tuple(Rule.from_dict(item) for item in data["rules"])
        ids = [rule.rule_id for rule in rules]
        if len(ids) != len(set(ids)):
            raise ValueError("rule IDs must be unique within a package")
        try:
            date.fromisoformat(data["effective_from"])
        except ValueError as exc:
            raise ValueError("rule package effective_from must be an ISO date") from exc
        return cls(data["package_id"], data["version"], data["status"], data["effective_from"], rules)
