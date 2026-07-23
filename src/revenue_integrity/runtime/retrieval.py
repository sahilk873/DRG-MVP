"""Deterministic retrieval-augmented generation (RAG) over the knowledge store.

Instead of prompting an agent with the whole ontology contract or no precedent at all, the
retriever surfaces the few most relevant, already-verified exemplars for a new task. Ranking is
a transparent Jaccard overlap of feature tokens with a content-hash tiebreak — fully reproducible
(no embedding nondeterminism, no floating-point ties). Result: a small, relevant context that
cuts prompt tokens and raises accuracy, and a ``retrieval_digest`` recorded in provenance so any
retrieval-augmented run can be reproduced and audited.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from ..audit import canonical_hash
from .knowledge import Exemplar, KnowledgeStore, _normalize_features


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    exemplars: tuple[Exemplar, ...]
    scores: tuple[float, ...]
    scanned: int
    retrieval_digest: str


class DeterministicRetriever:
    def __init__(self, store: KnowledgeStore) -> None:
        self._store = store

    def retrieve(
        self,
        query_features: list[str],
        *,
        kind: str | None = None,
        k: int = 5,
        min_overlap: int = 1,
        tenant_id: str | None = None,
    ) -> RetrievalResult:
        """Retrieve the top-k precedent exemplars for ``tenant_id`` only. Retrieval is strictly
        tenant-isolated: a query scoped to one tenant can never return another tenant's exemplars,
        since it draws candidates solely from that tenant's chain."""
        if not isinstance(k, int) or k <= 0:
            raise ValueError("k must be a positive integer")
        query = set(_normalize_features(query_features))
        candidates = self._store.exemplars(kind, tenant_id=tenant_id)
        scored: list[tuple[float, str, Exemplar]] = []
        for exemplar in candidates:
            features = set(exemplar.features)
            intersection = len(query & features)
            if intersection < min_overlap:
                continue
            union = len(query | features)
            score = intersection / union if union else 0.0
            scored.append((score, exemplar.content_hash, exemplar))
        # Deterministic order: highest score first, ties broken by content hash.
        scored.sort(key=lambda item: (-item[0], item[1]))
        top = scored[:k]
        digest = canonical_hash({
            "query": sorted(query),
            "kind": kind,
            "k": k,
            "min_overlap": min_overlap,
            "results": [item[1] for item in top],
        })
        return RetrievalResult(
            exemplars=tuple(item[2] for item in top),
            scores=tuple(round(item[0], 6) for item in top),
            scanned=len(candidates),
            retrieval_digest=digest,
        )


def retrieval_pack(result: RetrievalResult) -> list[dict]:
    """The compact context to inject into an agent prompt (payload + label only)."""
    return [
        {"kind": exemplar.kind, "label": exemplar.label, "payload": dict(exemplar.payload)}
        for exemplar in result.exemplars
    ]


def estimate_tokens(value: object) -> int:
    """Rough token estimate (~4 chars/token) for measuring RAG prompt savings."""
    return max(1, len(json.dumps(value, sort_keys=True, default=str)) // 4)
