from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Callable, Mapping, Sequence

from .engine import ENGINE_VERSION


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
    body["record_hash"] = canonical_hash(body)
    return body
