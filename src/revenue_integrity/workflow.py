from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .audit import canonical_hash

DECISION_SCHEMA_VERSION = "1.0.0"


class ReviewerRole(StrEnum):
    CODER = "coder"
    CDI_SPECIALIST = "cdi_specialist"
    CHARGE_REVIEWER = "charge_reviewer"
    COMPLIANCE_REVIEWER = "compliance_reviewer"
    ADMIN = "admin"
    READ_ONLY = "read_only"


class ReviewAction(StrEnum):
    ROUTE_TO_CODING = "route_to_coding"
    ROUTE_TO_CDI = "route_to_cdi"
    ROUTE_TO_CHARGE_REVIEW = "route_to_charge_review"
    ROUTE_TO_COMPLIANCE = "route_to_compliance"
    DISMISS_WITH_REASON = "dismiss_with_reason"


ROLE_ACTIONS: Mapping[ReviewerRole, frozenset[ReviewAction]] = {
    ReviewerRole.CODER: frozenset(
        {ReviewAction.ROUTE_TO_CODING, ReviewAction.ROUTE_TO_CDI, ReviewAction.DISMISS_WITH_REASON}
    ),
    ReviewerRole.CDI_SPECIALIST: frozenset(
        {ReviewAction.ROUTE_TO_CDI, ReviewAction.ROUTE_TO_CODING, ReviewAction.DISMISS_WITH_REASON}
    ),
    ReviewerRole.CHARGE_REVIEWER: frozenset(
        {
            ReviewAction.ROUTE_TO_CHARGE_REVIEW,
            ReviewAction.ROUTE_TO_COMPLIANCE,
            ReviewAction.DISMISS_WITH_REASON,
        }
    ),
    ReviewerRole.COMPLIANCE_REVIEWER: frozenset(
        {ReviewAction.ROUTE_TO_COMPLIANCE, ReviewAction.DISMISS_WITH_REASON}
    ),
    ReviewerRole.ADMIN: frozenset(ReviewAction),
    ReviewerRole.READ_ONLY: frozenset(),
}


@dataclass(frozen=True, slots=True)
class ReviewerIdentity:
    actor_id: str
    tenant_id: str
    workspace_id: str
    roles: tuple[ReviewerRole, ...]

    def __post_init__(self) -> None:
        for name in ("actor_id", "tenant_id", "workspace_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise ValueError(f"reviewer {name} must contain 1 to 128 characters")
        if not self.roles:
            raise ValueError("reviewer must have at least one role")
        if len(self.roles) != len(set(self.roles)) or any(not isinstance(role, ReviewerRole) for role in self.roles):
            raise ValueError("reviewer roles must be unique supported roles")


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    decision_id: str
    tenant_id: str
    workspace_id: str
    packet_id: str
    finding_id: str
    action: ReviewAction
    reason: str
    actor_id: str
    actor_roles: tuple[ReviewerRole, ...]
    decided_at: str
    packet_record_hash: str
    previous_decision_hash: str | None
    decision_hash: str

    def __post_init__(self) -> None:
        for name in ("decision_id", "tenant_id", "workspace_id", "packet_id", "finding_id", "actor_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"review decision {name} must not be empty")
        if not isinstance(self.action, ReviewAction):
            raise ValueError("review decision action is unsupported")
        if not self.reason.strip() or len(self.reason) > 1000:
            raise ValueError("review decision reason must contain 1 to 1000 characters")
        if (
            not self.actor_roles
            or len(self.actor_roles) != len(set(self.actor_roles))
            or any(not isinstance(role, ReviewerRole) for role in self.actor_roles)
        ):
            raise ValueError("review decision requires supported actor roles")
        decided_at = datetime.fromisoformat(self.decided_at.replace("Z", "+00:00"))
        if decided_at.tzinfo is None:
            raise ValueError("review decision decided_at must include a timezone")
        for name in ("packet_record_hash", "decision_hash"):
            if not _is_digest(getattr(self, name)):
                raise ValueError(f"review decision {name} must be a SHA-256 digest")
        if self.previous_decision_hash is not None and not _is_digest(self.previous_decision_hash):
            raise ValueError("previous_decision_hash must be a SHA-256 digest or null")

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_schema_version": DECISION_SCHEMA_VERSION,
            "decision_id": self.decision_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "packet_id": self.packet_id,
            "finding_id": self.finding_id,
            "action": self.action.value,
            "reason": self.reason,
            "actor_id": self.actor_id,
            "actor_roles": [role.value for role in self.actor_roles],
            "decided_at": self.decided_at,
            "packet_record_hash": self.packet_record_hash,
            "previous_decision_hash": self.previous_decision_hash,
            "decision_hash": self.decision_hash,
        }


class DecisionRepository(Protocol):
    def append(self, decision: ReviewDecision, *, expected_previous_hash: str | None) -> None: ...

    def list_for_packet(self, tenant_id: str, workspace_id: str, packet_id: str) -> Sequence[ReviewDecision]: ...


class SQLiteDecisionRepository:
    """Durable reference repository; every query is explicitly tenant scoped."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS review_decisions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, decision_id TEXT NOT NULL UNIQUE,
                tenant_id TEXT NOT NULL, workspace_id TEXT NOT NULL, packet_id TEXT NOT NULL,
                payload TEXT NOT NULL, decision_hash TEXT NOT NULL UNIQUE)""")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_decisions_scope ON review_decisions(tenant_id, workspace_id, packet_id, sequence)")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def append(self, decision: ReviewDecision, *, expected_previous_hash: str | None) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT decision_hash FROM review_decisions WHERE tenant_id=? AND workspace_id=? AND packet_id=? ORDER BY sequence DESC LIMIT 1",
                (decision.tenant_id, decision.workspace_id, decision.packet_id),
            ).fetchone()
            current = row[0] if row else None
            if current != expected_previous_hash or decision.previous_decision_hash != current:
                raise ValueError("review decision chain changed; reload before submitting")
            connection.execute(
                "INSERT INTO review_decisions(decision_id,tenant_id,workspace_id,packet_id,payload,decision_hash) VALUES(?,?,?,?,?,?)",
                (decision.decision_id, decision.tenant_id, decision.workspace_id, decision.packet_id, json.dumps(decision.to_dict(), sort_keys=True), decision.decision_hash),
            )

    def list_for_packet(self, tenant_id: str, workspace_id: str, packet_id: str) -> Sequence[ReviewDecision]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM review_decisions WHERE tenant_id=? AND workspace_id=? AND packet_id=? ORDER BY sequence",
                (tenant_id, workspace_id, packet_id),
            ).fetchall()
        return tuple(_decision_from_dict(json.loads(row[0])) for row in rows)


class ReviewWorkflowService:
    def __init__(self, repository: DecisionRepository, clock: Callable[[], datetime] | None = None) -> None:
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(UTC))

    def submit(self, *, packet: Mapping[str, Any], actor: ReviewerIdentity, finding_id: str, action: ReviewAction, reason: str) -> ReviewDecision:
        tenant = packet.get("tenant")
        if not isinstance(tenant, Mapping) or tenant.get("tenant_id") != actor.tenant_id or tenant.get("workspace_id") != actor.workspace_id:
            raise PermissionError("reviewer and packet tenant scope do not match")
        controls = packet.get("controls")
        if not isinstance(controls, Mapping) or controls.get("claim_mutation_allowed") is not False:
            raise ValueError("review packet does not enforce the no-mutation control")
        if action.value not in controls.get("permitted_actions", []):
            raise PermissionError("action is not permitted by the review packet")
        allowed = set().union(*(ROLE_ACTIONS.get(role, frozenset()) for role in actor.roles))
        if action not in allowed:
            raise PermissionError("reviewer roles do not permit this action")
        findings = packet.get("findings")
        if not isinstance(findings, list) or finding_id not in {item.get("finding_id") for item in findings if isinstance(item, Mapping)}:
            raise ValueError("finding does not belong to review packet")
        reason = reason.strip()
        if not reason or len(reason) > 1000:
            raise ValueError("decision reason must contain 1 to 1000 characters")
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("workflow clock must return a timezone-aware datetime")
        prior = self.repository.list_for_packet(actor.tenant_id, actor.workspace_id, str(packet["packet_id"]))
        if prior and not verify_decision_chain(prior):
            raise ValueError("existing review decision chain failed integrity verification")
        previous_hash = prior[-1].decision_hash if prior else None
        body = {
            "decision_schema_version": DECISION_SCHEMA_VERSION,
            "tenant_id": actor.tenant_id, "workspace_id": actor.workspace_id,
            "packet_id": packet["packet_id"], "finding_id": finding_id,
            "action": action.value, "reason": reason, "actor_id": actor.actor_id,
            "actor_roles": sorted(role.value for role in actor.roles),
            "decided_at": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "packet_record_hash": packet["provenance"]["record_hash"],
            "previous_decision_hash": previous_hash,
        }
        digest = canonical_hash(body)
        decision = ReviewDecision(
            decision_id=f"decision-{digest[:20]}", tenant_id=actor.tenant_id,
            workspace_id=actor.workspace_id, packet_id=str(packet["packet_id"]),
            finding_id=finding_id, action=action, reason=reason, actor_id=actor.actor_id,
            actor_roles=tuple(sorted(actor.roles, key=str)), decided_at=body["decided_at"],
            packet_record_hash=str(packet["provenance"]["record_hash"]),
            previous_decision_hash=previous_hash, decision_hash=digest,
        )
        self.repository.append(decision, expected_previous_hash=previous_hash)
        return decision


def verify_decision_chain(decisions: Sequence[ReviewDecision]) -> bool:
    previous: str | None = None
    for decision in decisions:
        payload = decision.to_dict()
        claimed = payload.pop("decision_hash")
        payload.pop("decision_id")
        if (decision.previous_decision_hash != previous or canonical_hash(payload) != claimed
                or decision.decision_id != f"decision-{claimed[:20]}"):
            return False
        previous = claimed
    return True


def _decision_from_dict(data: Mapping[str, Any]) -> ReviewDecision:
    if data.get("decision_schema_version") != DECISION_SCHEMA_VERSION:
        raise ValueError("unsupported review decision schema version")
    return ReviewDecision(
        decision_id=data["decision_id"], tenant_id=data["tenant_id"], workspace_id=data["workspace_id"],
        packet_id=data["packet_id"], finding_id=data["finding_id"], action=ReviewAction(data["action"]),
        reason=data["reason"], actor_id=data["actor_id"], actor_roles=tuple(ReviewerRole(role) for role in data["actor_roles"]),
        decided_at=data["decided_at"], packet_record_hash=data["packet_record_hash"],
        previous_decision_hash=data["previous_decision_hash"], decision_hash=data["decision_hash"],
    )


def _is_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
