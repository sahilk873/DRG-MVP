"""Governed, additive-only ontology extension — the "robust ontology" path.

An agent (or a human) may PROPOSE new ontology content as *data* — new classes, value-sets, and
relations — but never rewrite or remove existing content, and never emit code. A deterministic
preflight then decides whether the proposal may be promoted to a new ontology version:

1. **Additive-only**: every existing class/relation/value-set is still present and byte-identical;
   the proposal may only ADD. (No silent redefinition of a concept rules already depend on.)
2. **Internally valid**: the merged definition passes the full ``OntologyDefinition`` validator
   (known parents, no cycles, relation domain/range resolve, value-sets referenced exist).
3. **Version-bumped**: the ontology_id is unchanged and the version is bumped, so existing artifacts
   keep binding to the old version + digest while new ones adopt the new digest.

The recomputed digest is returned so the same cross-language digest the agent/TS layer computes can
be pinned into rule packages/adapters. Model output never becomes authoritative: only a proposal that
clears this gate is promoted, and promotion is versioned + auditable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .ontology import OntologyDefinition


@dataclass(frozen=True, slots=True)
class OntologyDelta:
    """A purely additive proposal against a base ontology definition."""

    new_version: str
    classes: tuple[Mapping[str, Any], ...] = ()
    relations: tuple[Mapping[str, Any], ...] = ()
    value_sets: Mapping[str, Sequence[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.new_version, str) or not self.new_version.strip():
            raise ValueError("ontology delta requires a non-empty new_version")
        if not (self.classes or self.relations or self.value_sets):
            raise ValueError("ontology delta must add at least one class, relation, or value set")


@dataclass(frozen=True, slots=True)
class PreflightResult:
    ok: bool
    reasons: tuple[str, ...]
    new_version: str | None = None
    new_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reasons": list(self.reasons),
            "new_version": self.new_version,
            "new_digest": self.new_digest,
        }


def apply_delta(base: Mapping[str, Any], delta: OntologyDelta) -> dict[str, Any]:
    """Merge an additive delta onto a base ontology definition payload (does not validate)."""
    proposed = {
        key: (list(value) if isinstance(value, list) else dict(value) if isinstance(value, dict) else value)
        for key, value in base.items()
    }
    proposed["version"] = delta.new_version
    proposed["classes"] = [*base.get("classes", []), *[dict(item) for item in delta.classes]]
    proposed["relations"] = [*base.get("relations", []), *[dict(item) for item in delta.relations]]
    proposed["value_sets"] = {**dict(base.get("value_sets", {})), **{key: list(values) for key, values in delta.value_sets.items()}}
    return proposed


def _by_id(items: Sequence[Mapping[str, Any]], key: str) -> dict[str, Mapping[str, Any]]:
    return {str(item[key]): dict(item) for item in items}


def verify_promotion_preflight(base: Mapping[str, Any], proposed: Mapping[str, Any]) -> PreflightResult:
    """Decide whether ``proposed`` may be promoted over ``base``. Fail-closed with reasons."""
    reasons: list[str] = []

    if base.get("ontology_id") != proposed.get("ontology_id"):
        reasons.append("ontology_id must not change")
    if not proposed.get("version") or proposed.get("version") == base.get("version"):
        reasons.append("proposed ontology must bump the version")

    # Additive-only: every base class/relation/value-set survives unchanged.
    base_classes, proposed_classes = _by_id(base.get("classes", []), "class_id"), _by_id(proposed.get("classes", []), "class_id")
    for class_id, definition in base_classes.items():
        if class_id not in proposed_classes:
            reasons.append(f"class {class_id} was removed (extension must be additive)")
        elif proposed_classes[class_id] != definition:
            reasons.append(f"class {class_id} was modified (extension must be additive)")

    base_relations, proposed_relations = _by_id(base.get("relations", []), "relation_id"), _by_id(proposed.get("relations", []), "relation_id")
    for relation_id, definition in base_relations.items():
        if relation_id not in proposed_relations:
            reasons.append(f"relation {relation_id} was removed (extension must be additive)")
        elif proposed_relations[relation_id] != definition:
            reasons.append(f"relation {relation_id} was modified (extension must be additive)")

    base_value_sets = dict(base.get("value_sets", {}))
    proposed_value_sets = dict(proposed.get("value_sets", {}))
    for name, members in base_value_sets.items():
        if name not in proposed_value_sets:
            reasons.append(f"value set {name} was removed (extension must be additive)")
        elif list(proposed_value_sets[name]) != list(members):
            reasons.append(f"value set {name} was modified (extension must be additive)")

    if not (proposed_classes.keys() - base_classes.keys()) \
            and not (proposed_relations.keys() - base_relations.keys()) \
            and not (proposed_value_sets.keys() - base_value_sets.keys()):
        reasons.append("proposal adds nothing new")

    # Internal validity + digest (only meaningful if it parses).
    new_digest: str | None = None
    new_version: str | None = None
    try:
        definition = OntologyDefinition.from_dict(dict(proposed))
        new_digest = definition.digest
        new_version = definition.version
    except ValueError as exc:
        reasons.append(f"proposed ontology is not internally valid: {exc}")

    if reasons:
        return PreflightResult(ok=False, reasons=tuple(reasons))
    return PreflightResult(ok=True, reasons=(), new_version=new_version, new_digest=new_digest)


def propose_and_verify(base: Mapping[str, Any], delta: OntologyDelta) -> tuple[dict[str, Any], PreflightResult]:
    """Apply an additive delta and run the promotion preflight in one step."""
    proposed = apply_delta(base, delta)
    return proposed, verify_promotion_preflight(base, proposed)
