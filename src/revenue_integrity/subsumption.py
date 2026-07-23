"""Governed code-subsumption reference for the declarative ``subsumed_by`` rule operator.

A purely deterministic, data-driven lookup: a small child->parent hierarchy of
diagnosis/procedure codes lets a declarative rule test whether a specific coded value
(e.g. ``L89.153``) rolls up to a more general parent (e.g. ``L89``). No language-model
output is involved and there is no code-execution path — the operator is a pure
declarative comparison against this governed table.

The packaged table (``data/code_subsumption_v1.json``) carries an explicit
``version`` and a self-describing SHA-256 ``digest`` over its canonical content. The
loader recomputes and verifies that digest, so a tampered or malformed table fails
closed (raises) rather than silently changing rule behaviour. This mirrors the
governance posture of the ontology and grouping reference tables.

Everything here is SYNTHETIC and NOT for real coding, billing, or terminology use.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
import json
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class CodeSubsumptionTable:
    """A governed, versioned child->parent code hierarchy.

    ``parents`` maps each specific code to its immediate more-general parent code.
    ``subsumed_by`` walks that chain to answer whether one code rolls up to another.
    """

    table_id: str
    version: str
    status: str
    digest: str
    parents: Mapping[str, str]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CodeSubsumptionTable":
        if not isinstance(data, Mapping):
            raise ValueError("code-subsumption table must be an object")
        required = {"table_id", "version", "status", "digest", "parents"}
        if missing := required - set(data):
            raise ValueError(f"code-subsumption table missing fields: {sorted(missing)}")
        for key in ("table_id", "version", "status"):
            if not isinstance(data[key], str) or not data[key]:
                raise ValueError(f"code-subsumption table {key} must be a non-empty string")
        if data["status"] not in {"approved", "approved-for-demo"}:
            raise ValueError("code-subsumption table status is invalid")
        digest = data["digest"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("code-subsumption table digest must be a lowercase SHA-256 hex digest")
        raw_parents = data["parents"]
        if not isinstance(raw_parents, Mapping) or not raw_parents:
            raise ValueError("code-subsumption table parents must be a non-empty object")
        parents: dict[str, str] = {}
        for child, parent in raw_parents.items():
            if not isinstance(child, str) or not child or not isinstance(parent, str) or not parent:
                raise ValueError("code-subsumption parents must map non-empty strings to non-empty strings")
            if child == parent:
                raise ValueError("code-subsumption parents must not map a code to itself")
            parents[child] = parent
        # Verify the self-describing digest over canonical content (digest field excluded)
        # so a tampered table fails closed rather than silently altering rule behaviour.
        # Imported lazily to avoid an engine<->audit<->subsumption import cycle.
        from .audit import canonical_hash

        content = {key: value for key, value in data.items() if key != "digest"}
        recomputed = canonical_hash(content)
        if recomputed != digest:
            raise ValueError("code-subsumption table digest does not match its content")
        # Reject cycles up front; the subsumption walk then needs no cycle guard.
        cls._reject_cycles(parents)
        return cls(
            table_id=data["table_id"],
            version=data["version"],
            status=data["status"],
            digest=digest,
            parents=parents,
        )

    @staticmethod
    def _reject_cycles(parents: Mapping[str, str]) -> None:
        for start in parents:
            seen = {start}
            current = parents[start]
            while current in parents:
                if current in seen:
                    raise ValueError("code-subsumption table contains a cycle")
                seen.add(current)
                current = parents[current]

    def subsumed_by(self, code: str, ancestor: str) -> bool:
        """Return True when ``code`` rolls up to ``ancestor`` via the parent chain.

        A code is considered subsumed by itself. Unknown codes (not present as any child)
        are only subsumed by themselves; they never spuriously roll up.
        """
        if not isinstance(code, str) or not isinstance(ancestor, str) or not code or not ancestor:
            return False
        if code == ancestor:
            return True
        current = code
        while current in self.parents:
            current = self.parents[current]
            if current == ancestor:
                return True
        return False


def load_code_subsumption_table(
    resource_name: str = "data/code_subsumption_v1.json",
) -> CodeSubsumptionTable:
    """Load the packaged, governed code-subsumption reference table."""
    resource = files("revenue_integrity").joinpath(resource_name)
    return CodeSubsumptionTable.from_dict(json.loads(resource.read_text(encoding="utf-8")))


@lru_cache(maxsize=None)
def default_code_subsumption_table() -> CodeSubsumptionTable:
    """Return the packaged default table (cached; verified once at first use)."""
    return load_code_subsumption_table()
