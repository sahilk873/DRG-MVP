from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Mapping, Sequence

from .engine import ENGINE_VERSION

#: Governance version for the run-usage cost accounting record.
RUN_USAGE_SCHEMA_VERSION = "1.0.0"

#: Deterministic per-model token rate table (integer micro-cents per 1,000 tokens).
#: Usage is recorded INPUT DATA — no model computes this. Rates are synthetic and
#: not for billing. Keyed by ``model_id``; ``__default__`` covers unknown models.
#: Micro-cents (cents * 1000) keep the rate table integer-only while allowing
#: sub-cent per-1k-token rates; the final estimate is rounded to whole cents.
RUN_USAGE_RATE_TABLE_MICROCENTS_PER_1K: dict[str, dict[str, int]] = {
    "__default__": {"input": 300, "output": 1500},
    "anthropic/claude-opus": {"input": 1500, "output": 7500},
    "anthropic/claude-sonnet": {"input": 300, "output": 1500},
    "anthropic/claude-haiku": {"input": 25, "output": 125},
}


def _usage_tokens(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer token count")
    return value


@dataclass(frozen=True, slots=True)
class RunUsage:
    """Immutable per-run token/cost accounting recorded into provenance.

    Token counts are recorded INPUT DATA supplied by the provider-agnostic agent
    runtime — no language model computes anything here. ``estimated_cost_cents`` is a
    deterministic integer-cent figure derived from :data:`RUN_USAGE_RATE_TABLE_MICROCENTS_PER_1K`,
    reproducible for identical inputs. Synthetic rates; not for billing.
    """

    model_id: str
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ValueError("run_usage model_id must be a non-empty string")
        _usage_tokens(self.input_tokens, "run_usage input_tokens")
        _usage_tokens(self.output_tokens, "run_usage output_tokens")

    @property
    def estimated_cost_cents(self) -> int:
        """Deterministic integer-cent cost estimate from the synthetic rate table."""
        rates = RUN_USAGE_RATE_TABLE_MICROCENTS_PER_1K.get(
            self.model_id, RUN_USAGE_RATE_TABLE_MICROCENTS_PER_1K["__default__"]
        )
        # Integer micro-cent arithmetic; round half-up to whole cents at the end.
        micro = self.input_tokens * rates["input"] + self.output_tokens * rates["output"]
        # micro is (cents * 1000 * 1000)? No: rate is micro-cents per 1,000 tokens,
        # so tokens * rate = micro-cents * (tokens / 1000). Divide by 1000 (per-1k) and
        # by 1000 (micro-cents -> cents) => divide by 1_000_000, rounding half-up.
        return (micro + 500_000) // 1_000_000

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RunUsage":
        if not isinstance(data, Mapping):
            raise ValueError("run_usage must be an object")
        required = {"model_id", "input_tokens", "output_tokens"}
        missing = sorted(required - set(data))
        unknown = sorted(set(data) - required)
        if missing:
            raise ValueError(f"run_usage missing required fields: {missing}")
        if unknown:
            raise ValueError(f"run_usage contains unknown fields: {unknown}")
        return cls(
            model_id=data["model_id"],
            input_tokens=data["input_tokens"],
            output_tokens=data["output_tokens"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_usage_schema_version": RUN_USAGE_SCHEMA_VERSION,
            "model_id": self.model_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_cents": self.estimated_cost_cents,
        }


def canonical_json(payload: Any) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def audit_record(
    *,
    case_payload: Mapping[str, Any],
    rule_package: Mapping[str, Any],
    findings: Sequence[Mapping[str, Any]],
    clock: Callable[[], datetime] | None = None,
    previous_record_hash: str | None = None,
    run_usage: RunUsage | None = None,
) -> dict[str, Any]:
    now = (clock or (lambda: datetime.now(UTC)))()
    if now.tzinfo is None:
        raise ValueError("audit clock must return a timezone-aware datetime")
    body: dict[str, Any] = {
        "audit_schema_version": "1.0.0",
        "evaluated_at": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "engine_version": ENGINE_VERSION,
        "case_id": case_payload["case_id"],
        "case_hash": canonical_hash(case_payload),
        "rule_package_id": rule_package["package_id"],
        "rule_package_version": rule_package["version"],
        "rule_package_hash": canonical_hash(rule_package),
        "previous_record_hash": previous_record_hash,
        "findings": list(findings),
    }
    # Additive, immutable cost accounting. When no usage is supplied the key is
    # omitted entirely so the record and its hash stay byte-identical to the legacy
    # no-usage path. Hash-covered because it is placed into ``body`` before signing.
    if run_usage is not None:
        body["run_usage"] = run_usage.to_dict()
    body["record_hash"] = canonical_hash(body)
    return body


def verify_audit_record(record: Mapping[str, Any]) -> bool:
    """Recompute a single record's ``record_hash`` and confirm it matches.

    Mirrors how :func:`audit_record` sets ``record_hash`` last: the hash is recomputed
    over the record with ``record_hash`` removed.
    """
    claimed = record.get("record_hash")
    if not isinstance(claimed, str):
        return False
    body = {key: value for key, value in record.items() if key != "record_hash"}
    return canonical_hash(body) == claimed


def verify_audit_chain(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Verify per-record integrity and contiguous hash-chain linkage.

    Returns ``{"ok", "first_bad_index", "reason"}``. The genesis record must carry a
    ``previous_record_hash`` of ``None``; every later record must link to the exact
    ``record_hash`` of its predecessor. A read-only tamper-evidence guarantee — it never
    mutates a claim, DRG, or payment.
    """
    previous_hash: str | None = None
    for index, record in enumerate(records):
        if not verify_audit_record(record):
            return {"ok": False, "first_bad_index": index, "reason": "record_hash mismatch"}
        if record.get("previous_record_hash") != previous_hash:
            return {"ok": False, "first_bad_index": index, "reason": "broken previous_record_hash linkage"}
        previous_hash = record["record_hash"]
    return {"ok": True, "first_bad_index": None, "reason": "verified"}
