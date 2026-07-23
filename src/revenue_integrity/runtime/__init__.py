"""Deterministic agentic runtime: retrieval-augmented, self-improving authoring support.

This package gives the agents a governed way to *learn from verified experience* without
sacrificing determinism. The system improves over time by accumulating an append-only,
hash-chained store of approved artifacts and labeled reviewer outcomes (``knowledge``), and
by retrieving the most relevant precedent for a new task deterministically (``retrieval``) so
agents are prompted with a small, relevant context (RAG) instead of everything — fewer tokens,
higher accuracy. Promotion of any authored artifact stays gated and reproducible (``promotion``).

Nothing here lets model output become authoritative: retrieval only surfaces already-verified
records, and promotion requires a passing deterministic score plus a fingerprint/hash freeze.
"""
from .knowledge import Exemplar, KnowledgeStore, EXEMPLAR_KINDS, DEFAULT_TENANT_ID
from .retrieval import DeterministicRetriever, RetrievalResult, estimate_tokens
from .promotion import (
    ArtifactScore,
    admit_artifact,
    admit_rule_package,
    learn_from_decision,
    learn_from_review_log,
)
from .sandbox import SandboxLimits, SandboxResult, RowResult, run_sandboxed
from .self_eval import GoldenSample, score_artifact, evaluate_and_admit
from .authored_reader import (
    AuthoredReaderDefinition,
    score_reader,
    run_authored_reader,
    promote_reader,
)
from .authored_transform import (
    AuthoredTransformDefinition,
    score_transform,
    run_authored_transform,
    promote_transform,
)

__all__ = [
    "Exemplar",
    "KnowledgeStore",
    "EXEMPLAR_KINDS",
    "DEFAULT_TENANT_ID",
    "DeterministicRetriever",
    "RetrievalResult",
    "estimate_tokens",
    "ArtifactScore",
    "admit_artifact",
    "admit_rule_package",
    "learn_from_decision",
    "learn_from_review_log",
    "SandboxLimits",
    "SandboxResult",
    "RowResult",
    "run_sandboxed",
    "GoldenSample",
    "score_artifact",
    "evaluate_and_admit",
    "AuthoredReaderDefinition",
    "score_reader",
    "run_authored_reader",
    "promote_reader",
    "AuthoredTransformDefinition",
    "score_transform",
    "run_authored_transform",
    "promote_transform",
]
