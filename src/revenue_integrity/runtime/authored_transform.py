"""Authored transforms: the agent can write field transforms the fixed DSL can't express.

The declarative adapter op set (`trim/lower/upper/integer/number/boolean/datetime/map/split`) is
deliberately narrow. When a source field needs regex extraction, arithmetic, currency parsing, or
conditional logic, the agent authors a `transform(value)` function instead. It is sandbox-scored
against golden samples and promoted as a frozen, hash-pinned `AuthoredTransformDefinition`; at
execution it runs only in the sandbox and only if its code hash matches what was scored. This lifts
the transform-expressiveness ceiling without letting unverified model code touch the data plane.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from ..audit import canonical_hash
from .knowledge import Exemplar, KnowledgeStore
from .promotion import EXECUTABLE_STATUSES, ArtifactScore
from .sandbox import SandboxLimits, run_sandboxed
from .self_eval import GoldenSample, score_artifact


@dataclass(frozen=True, slots=True)
class AuthoredTransformDefinition:
    transform_id: str
    version: str
    status: str
    code: str
    entrypoint: str = "transform"

    def __post_init__(self) -> None:
        for name in ("transform_id", "version", "code", "entrypoint"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"authored transform {name} must be a non-empty string")

    @property
    def code_hash(self) -> str:
        return canonical_hash({"code": self.code, "entrypoint": self.entrypoint})

    def to_dict(self) -> dict[str, Any]:
        return {
            "transform_id": self.transform_id, "version": self.version, "status": self.status,
            "code": self.code, "entrypoint": self.entrypoint, "code_hash": self.code_hash,
        }


def score_transform(
    code: str,
    samples: Sequence[GoldenSample],
    *,
    entrypoint: str = "transform",
    validator: Callable[[Any], bool] | None = None,
    limits: SandboxLimits | None = None,
) -> ArtifactScore:
    score, _ = score_artifact(code, samples, entrypoint=entrypoint, validator=validator, limits=limits)
    return score


def run_authored_transform(
    definition: AuthoredTransformDefinition,
    values: Sequence[Any],
    *,
    expected_hash: str | None = None,
    limits: SandboxLimits | None = None,
) -> list[Any]:
    """Apply a promoted transform to a list of field values in the sandbox. Fail-closed."""
    if definition.status not in EXECUTABLE_STATUSES:
        raise ValueError(f"authored transform status {definition.status!r} is not executable")
    if expected_hash is not None and definition.code_hash != expected_hash:
        raise ValueError("authored transform code hash does not match the promoted artifact")
    result = run_sandboxed(definition.code, list(values), entrypoint=definition.entrypoint, limits=limits)
    if not result.ok:
        raise ValueError(f"authored transform failed in sandbox: {result.error}")
    outputs: list[Any] = []
    for index, row_result in enumerate(result.results):
        if not row_result.ok:
            raise ValueError(f"authored transform errored on value {index}: {row_result.error}")
        outputs.append(row_result.value)
    return outputs


def promote_transform(
    store: KnowledgeStore,
    code: str,
    *,
    transform_id: str,
    version: str,
    samples: Sequence[GoldenSample],
    status: str,
    entrypoint: str = "transform",
    feature_tokens: Sequence[str] = (),
    validator: Callable[[Any], bool] | None = None,
    min_parse_rate: float = 1.0,
    min_conformance: float = 1.0,
    require_exact: bool = True,
    limits: SandboxLimits | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> tuple[bool, AuthoredTransformDefinition | None, ArtifactScore, str]:
    """Sandbox-score an authored transform and, if it passes, promote it as a governed definition."""
    score = score_transform(code, samples, entrypoint=entrypoint, validator=validator, limits=limits)
    if status not in EXECUTABLE_STATUSES:
        return False, None, score, f"status {status!r} is not executable"
    if not score.meets(min_parse_rate=min_parse_rate, min_conformance=min_conformance, require_exact=require_exact):
        return False, None, score, "transform score did not meet promotion thresholds"
    definition = AuthoredTransformDefinition(transform_id, version, status, code, entrypoint)
    store.record(Exemplar(
        exemplar_id=f"transform:{transform_id}@{version}",
        kind="transform",
        features=[f"transform:{transform_id}", *feature_tokens] if feature_tokens else [f"transform:{transform_id}"],
        payload={"code": code, "entrypoint": entrypoint, "code_hash": definition.code_hash},
        label="approved",
        provenance={**(provenance or {}), "score": score.to_dict(), "status": status},
    ))
    return True, definition, score, "promoted"
