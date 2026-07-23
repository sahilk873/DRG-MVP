"""Verify-then-promote gate + self-learning write-back.

An agent-authored artifact only enters the deterministic execution plane after it (a) passes a
deterministic score on golden samples and (b) carries an executable status. Promotion records the
frozen artifact into the knowledge store, so what the system "learned" is exactly what was verified.
Reviewer decisions are also written back as labeled outcomes, so future findings can retrieve how
similar ones were resolved — deterministic self-improvement, not model drift.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..promotion import PatternProposal
from ..promotion_backtest import (
    BacktestGateError,
    PromotedProposal,
    promote_with_backtest,
)
from .knowledge import Exemplar, KnowledgeStore


def learn_from_review_log(
    store: KnowledgeStore,
    packet: Mapping[str, Any],
    decisions: Sequence[Mapping[str, Any]],
    *,
    tenant_id: str | None = None,
) -> list[Exemplar]:
    """Project an audited review packet + its decision log into retrievable outcome exemplars.

    This is the self-learning write-back for the reviewer loop, built deterministically FROM the
    audited artifacts (never as a side-effect inside the submit path): each governed decision becomes
    a labeled precedent for how a finding of that shape was resolved. Decisions whose finding is not
    in the packet are skipped. Re-running over the same log is idempotent (content-addressed store).
    """
    findings = {item.get("finding_id"): item for item in packet.get("findings", [])}
    recorded: list[Exemplar] = []
    for decision in decisions:
        finding = findings.get(decision.get("finding_id"))
        if finding is None:
            continue
        recorded.append(
            learn_from_decision(
                store,
                finding,
                action=str(decision.get("action", "")),
                reason=str(decision.get("reason_code", decision.get("reason", ""))),
                provenance={
                    "packet_id": packet.get("packet_id"),
                    "actor_id": decision.get("actor_id"),
                    "decided_at": decision.get("decided_at"),
                },
                tenant_id=tenant_id,
            )
        )
    return recorded

EXECUTABLE_STATUSES = frozenset({"approved", "approved-for-demo"})


@dataclass(frozen=True, slots=True)
class ArtifactScore:
    """Deterministic score of an authored artifact against golden samples."""

    parse_rate: float          # fraction of sample inputs the artifact handled without error
    conformance: float         # fraction of outputs that passed schema/lineage validation
    exact_match: bool = True   # canonical-hash match against a golden output, when one exists

    def __post_init__(self) -> None:
        for name in ("parse_rate", "conformance"):
            value = getattr(self, name)
            if isinstance(value, bool) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")

    def meets(self, *, min_parse_rate: float = 0.95, min_conformance: float = 1.0, require_exact: bool = False) -> bool:
        return (
            self.parse_rate >= min_parse_rate
            and self.conformance >= min_conformance
            and (self.exact_match or not require_exact)
        )

    def to_dict(self) -> dict[str, Any]:
        return {"parse_rate": self.parse_rate, "conformance": self.conformance, "exact_match": self.exact_match}


def admit_artifact(
    store: KnowledgeStore,
    *,
    artifact_id: str,
    kind: str,
    features: Sequence[str],
    payload: Mapping[str, Any],
    score: ArtifactScore,
    status: str,
    min_parse_rate: float = 0.95,
    min_conformance: float = 1.0,
    require_exact: bool = False,
    provenance: Mapping[str, Any] | None = None,
    tenant_id: str | None = None,
) -> tuple[bool, Exemplar | None, str]:
    """Gate an authored artifact into the knowledge store. Returns (promoted, exemplar, reason).

    ``tenant_id`` scopes the write to a single tenant's chain (defaulting to the shared default)."""
    if status not in EXECUTABLE_STATUSES:
        return False, None, f"status {status!r} is not executable"
    if not score.meets(min_parse_rate=min_parse_rate, min_conformance=min_conformance, require_exact=require_exact):
        return False, None, "artifact score did not meet promotion thresholds"
    exemplar = Exemplar(
        exemplar_id=artifact_id,
        kind=kind,
        features=tuple(features),
        payload=payload,
        label="approved",
        provenance={**(provenance or {}), "score": score.to_dict(), "status": status},
    )
    store.record(exemplar, tenant_id=tenant_id)
    return True, exemplar, "promoted"


def admit_rule_package(
    store: KnowledgeStore,
    proposal: PatternProposal,
    reviewer_id: str,
    manifest_path: Any,
    *,
    features: Sequence[str] | None = None,
    baseline_report: Mapping[str, Any] | None = None,
    allow_unapproved_rules: bool = False,
    minimum_precision: float = 0.95,
    provenance: Mapping[str, Any] | None = None,
    tenant_id: str | None = None,
    promote: Callable[..., PromotedProposal] = promote_with_backtest,
) -> tuple[Exemplar, PromotedProposal]:
    """Admit a proposed rule package as a learned exemplar — but only behind the backtest gate.

    A rule package is declarative JSON (:class:`~..promotion.PatternProposal`), never executable
    model output. It is promoted through :func:`~..promotion_backtest.promote_with_backtest`, which
    runs the deterministic eval harness over a signed manifest and refuses promotion (raising
    :class:`~..promotion_backtest.BacktestGateError`) unless the report meets the manifest
    thresholds and shows no regression against ``baseline_report``. Only on success is the frozen
    proposal recorded as a ``rule_package`` exemplar, with the signed ``report_hash`` captured in
    provenance so the promotion is reproducible.

    Returns the recorded ``(exemplar, promoted_proposal)``. Any gate failure propagates the
    :class:`~..promotion_backtest.BacktestGateError` untouched — nothing is written to the store.

    ``promote`` is injectable purely for deterministic testing; it defaults to the real backtest gate.
    """
    promoted = promote(
        proposal,
        reviewer_id,
        manifest_path,
        baseline_report=baseline_report,
        allow_unapproved_rules=allow_unapproved_rules,
        minimum_precision=minimum_precision,
    )
    backtest = promoted.backtest
    resolved_features = (
        list(features)
        if features
        else [f"pattern:{proposal.pattern_key}", f"ontology:{proposal.ontology_version}"]
    )
    exemplar = Exemplar(
        exemplar_id=f"rule-package:{proposal.proposal_id}",
        kind="rule_package",
        features=resolved_features,
        payload={
            "proposal": promoted.proposal.to_dict(),
            "report_hash": backtest.report_hash,
        },
        label="approved",
        provenance={
            **(provenance or {}),
            "reviewer_id": reviewer_id,
            "report_hash": backtest.report_hash,
            "backtest": backtest.to_dict(),
        },
    )
    store.record(exemplar, tenant_id=tenant_id)
    return exemplar, promoted


def learn_from_decision(
    store: KnowledgeStore,
    finding: Mapping[str, Any],
    *,
    action: str,
    reason: str,
    provenance: Mapping[str, Any] | None = None,
    tenant_id: str | None = None,
) -> Exemplar:
    """Record a reviewer outcome as retrievable precedent (deterministic self-learning).

    ``finding`` is a ``Finding.to_dict()``-shaped mapping. The exemplar's features capture the
    finding's identity (rule, disposition, proposed codes) so a future, similar finding can
    retrieve how this one was resolved.
    """
    change = finding.get("proposed_change") or {}
    codes = [code for values in change.values() if isinstance(values, list) for code in values]
    features = [
        f"rule:{finding.get('rule_id', '')}",
        f"disposition:{finding.get('disposition', '')}",
        *[f"code:{code}" for code in codes],
        *[f"subject:{subject}" for subject in finding.get("subject_ids", [])],
    ]
    exemplar = Exemplar(
        exemplar_id=f"outcome:{finding.get('finding_id', 'unknown')}",
        kind="review_outcome",
        features=[token for token in features if token.split(":", 1)[1]],
        payload={"finding_id": finding.get("finding_id"), "action": action, "reason": reason},
        label=f"{action}:{reason}",
        provenance=dict(provenance or {}),
    )
    store.record(exemplar, tenant_id=tenant_id)
    return exemplar
