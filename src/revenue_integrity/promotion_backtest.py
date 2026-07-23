"""Backtest-gated rule promotion — the governed path from proposal to approved asset.

``promotion.PatternProposal.approve`` accepts a bare precision float, which is fine for
local experimentation but trusts an unverified metric. This module hardens that path: it
promotes a proposal **only** when a signed evaluation backtest (``eval_cli.evaluate_manifest``
over a caller-supplied manifest) meets the manifest thresholds *and* shows no regression
against an optional baseline report. The backtest ``report_hash`` and metrics are recorded
into the promoted proposal's provenance so a later reviewer can reproduce the exact figure.

Everything here is deterministic: it runs the deterministic ``RuleEngine`` through the eval
harness and compares hash-signed reports. No language-model output is consulted, no claim is
mutated, no DRG is assigned, and no reimbursement is computed.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .eval_cli import evaluate_manifest
from .promotion import PatternProposal

#: Metric fields (higher-is-better) checked for regression against a baseline report.
_REGRESSION_METRICS = ("precision", "recall", "f1")


class BacktestGateError(ValueError):
    """Raised when a proposal fails the governed backtest gate."""


@dataclass(frozen=True, slots=True)
class BacktestProvenance:
    """Immutable record of the signed backtest that justified a promotion."""

    report_hash: str
    engine_version: str
    eval_schema_version: str
    manifest_path: str
    precision: float
    recall: float
    f1: float
    baseline_report_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_hash": self.report_hash,
            "engine_version": self.engine_version,
            "eval_schema_version": self.eval_schema_version,
            "manifest_path": self.manifest_path,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "baseline_report_hash": self.baseline_report_hash,
        }


@dataclass(frozen=True, slots=True)
class PromotedProposal:
    """An approved proposal paired with the backtest provenance that governed it."""

    proposal: PatternProposal
    backtest: BacktestProvenance

    def to_dict(self) -> dict[str, Any]:
        return {"proposal": self.proposal.to_dict(), "backtest": self.backtest.to_dict()}


def _extract_metric(report: Mapping[str, Any], name: str) -> float:
    metrics = report.get("metrics")
    if not isinstance(metrics, Mapping) or name not in metrics:
        raise BacktestGateError(f"backtest report is missing metric '{name}'")
    return float(metrics[name])


def _check_no_regression(
    report: Mapping[str, Any], baseline: Mapping[str, Any] | None
) -> None:
    if baseline is None:
        return
    for name in _REGRESSION_METRICS:
        current = _extract_metric(report, name)
        prior = _extract_metric(baseline, name)
        if current < prior:
            raise BacktestGateError(
                f"backtest regression: {name} dropped from {prior} to {current}"
            )


def promote_with_backtest(
    proposal: PatternProposal,
    reviewer_id: str,
    manifest_path: str | Path,
    *,
    baseline_report: Mapping[str, Any] | None = None,
    allow_unapproved_rules: bool = False,
    minimum_precision: float = 0.95,
    evaluate: Callable[..., Mapping[str, Any]] = evaluate_manifest,
) -> PromotedProposal:
    """Promote ``proposal`` only if a signed backtest passes thresholds and shows no regression.

    The manifest must declare ``thresholds`` and the resulting signed report must report
    ``passed == True``; otherwise the promotion is refused with :class:`BacktestGateError`.
    When ``baseline_report`` is supplied, every higher-is-better metric must be greater than
    or equal to the baseline's. On success the proposal is approved through the existing
    :meth:`PatternProposal.approve` path (which re-checks the precision floor) and the
    backtest ``report_hash`` + metrics are recorded into the returned provenance.

    ``evaluate`` is injectable purely for deterministic testing; it defaults to the real
    :func:`eval_cli.evaluate_manifest`.
    """
    manifest = Path(manifest_path)
    report = evaluate(manifest, allow_unapproved=allow_unapproved_rules)
    if not isinstance(report, Mapping):
        raise BacktestGateError("backtest evaluation did not return a report object")

    report_hash = report.get("report_hash")
    if not isinstance(report_hash, str) or not report_hash.strip():
        raise BacktestGateError("backtest report is unsigned (missing report_hash)")

    if "passed" not in report:
        raise BacktestGateError(
            "backtest manifest declares no thresholds; cannot gate promotion"
        )
    if report.get("passed") is not True:
        raise BacktestGateError("backtest did not meet manifest thresholds")

    _check_no_regression(report, baseline_report)

    approved = proposal.approve(reviewer_id, minimum_precision=minimum_precision)

    provenance = BacktestProvenance(
        report_hash=report_hash,
        engine_version=str(report.get("engine_version", "")),
        eval_schema_version=str(report.get("eval_schema_version", "")),
        manifest_path=str(manifest),
        precision=_extract_metric(report, "precision"),
        recall=_extract_metric(report, "recall"),
        f1=_extract_metric(report, "f1"),
        baseline_report_hash=(
            str(baseline_report.get("report_hash"))
            if baseline_report is not None and baseline_report.get("report_hash") is not None
            else None
        ),
    )
    return PromotedProposal(proposal=approved, backtest=provenance)
