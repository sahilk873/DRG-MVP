"""Self-evaluation harness: score agent-authored code by running it through the sandbox.

This is the "evaluate" step of the generate → run → evaluate → self-correct → promote loop. An
authored artifact is scored on golden samples for parse rate (did it run), conformance (did the
output pass a deterministic validator), and exact match (did it reproduce known-good outputs). The
resulting :class:`ArtifactScore` is exactly what the promotion gate checks, so what gets promoted is
what was measured — in the same sandbox that authoring used.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .knowledge import KnowledgeStore
from .promotion import ArtifactScore, admit_artifact
from .sandbox import SandboxLimits, SandboxResult, run_sandboxed


@dataclass(frozen=True, slots=True)
class GoldenSample:
    input: Any
    expected: Any = None  # known-good output for exact-match scoring (optional)


def score_artifact(
    code: str,
    samples: Sequence[GoldenSample],
    *,
    entrypoint: str = "transform",
    validator: Callable[[Any], bool] | None = None,
    limits: SandboxLimits | None = None,
) -> tuple[ArtifactScore, SandboxResult]:
    """Run ``code`` over the golden samples in the sandbox and score it deterministically."""
    if not samples:
        raise ValueError("scoring requires at least one golden sample")
    result = run_sandboxed(code, [sample.input for sample in samples], entrypoint=entrypoint, limits=limits)
    if not result.ok:
        return ArtifactScore(parse_rate=0.0, conformance=0.0, exact_match=False), result

    total = len(samples)
    parsed = sum(1 for row in result.results if row.ok)
    conformant = sum(
        1 for row in result.results if row.ok and (validator(row.value) if validator else True)
    )
    expected_pairs = [
        (sample, row) for sample, row in zip(samples, result.results) if sample.expected is not None
    ]
    exact_match = all(row.ok and row.value == sample.expected for sample, row in expected_pairs)
    return (
        ArtifactScore(parse_rate=parsed / total, conformance=conformant / total, exact_match=exact_match),
        result,
    )


def evaluate_and_admit(
    store: KnowledgeStore,
    code: str,
    samples: Sequence[GoldenSample],
    *,
    artifact_id: str,
    kind: str,
    features: Sequence[str],
    status: str,
    entrypoint: str = "transform",
    validator: Callable[[Any], bool] | None = None,
    limits: SandboxLimits | None = None,
    min_parse_rate: float = 0.95,
    min_conformance: float = 1.0,
    require_exact: bool = False,
    provenance: Mapping[str, Any] | None = None,
) -> tuple[bool, ArtifactScore, str]:
    """End-to-end: sandbox-score authored ``code`` and, if it passes, promote it to the store.

    The promoted exemplar's payload includes the exact code + entrypoint, so the execution plane can
    replay precisely what was scored. Returns (promoted, score, reason).
    """
    score, _ = score_artifact(code, samples, entrypoint=entrypoint, validator=validator, limits=limits)
    promoted, _, reason = admit_artifact(
        store,
        artifact_id=artifact_id,
        kind=kind,
        features=features,
        payload={"code": code, "entrypoint": entrypoint},
        score=score,
        status=status,
        min_parse_rate=min_parse_rate,
        min_conformance=min_conformance,
        require_exact=require_exact,
        provenance=provenance,
    )
    return promoted, score, reason
