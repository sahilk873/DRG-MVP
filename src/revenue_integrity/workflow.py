from __future__ import annotations

import contextlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .audit import canonical_hash
from .automation import AutomationTier, verify_automation_plan_hash
from .models import GapStatus
from .review_packet import verify_review_packet_hash

DECISION_SCHEMA_VERSION = "2.0.0"


def _open_sqlite(path: Path) -> contextlib.closing[sqlite3.Connection]:
    """Yield a short-lived WAL-mode connection that is always closed on exit.

    ``sqlite3.Connection`` used as a context manager only commits/rolls back the
    transaction — it never closes the handle. Wrapping every connection in
    ``contextlib.closing`` gives each operation a clean lifecycle and stops the process
    from leaking connections (previously surfaced as ResourceWarning). Pair with
    ``with ..., connection:`` when transactional commit/rollback is also wanted. Shared by
    every tenant-scoped store here and by ``routing.SQLiteRoutingOutbox``.
    """
    connection = sqlite3.connect(path, timeout=10)
    connection.execute("PRAGMA journal_mode=WAL")
    return contextlib.closing(connection)


def _verify_packet_and_plan_scope(
    packet: Mapping[str, Any], automation_plan: Mapping[str, Any], actor: ReviewerIdentity
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Shared integrity + tenant-scope + plan-binding + no-mutation preamble.

    Both terminal-decision paths (revenue :class:`ReviewWorkflowService` and clinical
    :class:`GapClosureService`) must first prove: the review packet and automation plan each
    verify their own hash, the packet's tenant/workspace matches the actor, the plan is bound
    to the same tenant and to this exact packet, and the packet enforces the no-mutation
    control. Returns the validated ``(tenant, controls)`` mappings. Behavior — including every
    error type and message — is identical to the previously inlined checks, so the two paths
    cannot drift apart.
    """
    if not verify_review_packet_hash(packet):
        raise ValueError("review packet failed full-packet integrity verification")
    if not verify_automation_plan_hash(automation_plan):
        raise ValueError("automation plan failed integrity verification")
    tenant = packet.get("tenant")
    if not isinstance(tenant, Mapping) or tenant.get("tenant_id") != actor.tenant_id or tenant.get("workspace_id") != actor.workspace_id:
        raise PermissionError("reviewer and packet tenant scope do not match")
    if automation_plan.get("tenant") != tenant:
        raise PermissionError("automation plan and packet tenant scope do not match")
    plan_packet = automation_plan.get("packet")
    if (
        not isinstance(plan_packet, Mapping)
        or plan_packet.get("packet_id") != packet.get("packet_id")
        or plan_packet.get("packet_hash") != packet["provenance"]["packet_hash"]
    ):
        raise ValueError("automation plan does not reference this exact review packet")
    controls = packet.get("controls")
    if not isinstance(controls, Mapping) or controls.get("claim_mutation_allowed") is not False:
        raise ValueError("review packet does not enforce the no-mutation control")
    return tenant, controls


def _canonical_packet_finding(packet_finding: Mapping[str, Any]) -> dict[str, Any]:
    """Strip the presentational-only 'narrative' the packet injects onto each finding.

    The packet injects a derived ``narrative`` (review-packet 3.3.0) that is covered by the
    packet hash separately; the automation plan hashes the canonical engine finding
    (``Finding.to_dict()``), so the narrative must be excluded before comparing a packet
    finding against ``automation.finding_hash``. Shared by both submit paths so the
    canonical-finding derivation cannot drift.
    """
    return {key: value for key, value in packet_finding.items() if key != "narrative"}


class ReviewerRole(StrEnum):
    CODER = "coder"
    CDI_SPECIALIST = "cdi_specialist"
    CHARGE_REVIEWER = "charge_reviewer"
    COMPLIANCE_REVIEWER = "compliance_reviewer"
    ADMIN = "admin"
    READ_ONLY = "read_only"
    #: Authorized clinical role that may close, except, or withdraw a clinical care gap.
    #: A gap is analytics-identified; only this role (or ADMIN) records the human decision.
    #: It carries NO revenue ReviewAction authority, so revenue decision recording is
    #: unchanged and a coordinator can never route/dismiss a revenue_integrity finding.
    CARE_GAP_COORDINATOR = "care_gap_coordinator"


class ReviewAction(StrEnum):
    ROUTE_TO_CODING = "route_to_coding"
    ROUTE_TO_CDI = "route_to_cdi"
    ROUTE_TO_CHARGE_REVIEW = "route_to_charge_review"
    ROUTE_TO_COMPLIANCE = "route_to_compliance"
    DISMISS_WITH_REASON = "dismiss_with_reason"


class DecisionReasonCode(StrEnum):
    EVIDENCE_CONFIRMED = "evidence_confirmed"
    DOCUMENTATION_NOT_SUPPORTED = "documentation_not_supported"
    DUPLICATE = "duplicate"
    ALREADY_CORRECTED = "already_corrected"
    OTHER_GOVERNED = "other_governed"


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
    reason_code: DecisionReasonCode
    reason: str
    actor_id: str
    actor_roles: tuple[ReviewerRole, ...]
    decided_at: str
    packet_record_hash: str
    packet_hash: str
    automation_plan_hash: str
    automation_policy_hash: str
    idempotency_key: str
    previous_decision_hash: str | None
    decision_hash: str

    def __post_init__(self) -> None:
        for name in (
            "decision_id", "tenant_id", "workspace_id", "packet_id", "finding_id", "actor_id",
            "idempotency_key",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"review decision {name} must not be empty")
        if not isinstance(self.action, ReviewAction):
            raise ValueError("review decision action is unsupported")
        if not isinstance(self.reason_code, DecisionReasonCode):
            raise ValueError("review decision reason_code is unsupported")
        if self.action is ReviewAction.DISMISS_WITH_REASON and self.reason_code is DecisionReasonCode.EVIDENCE_CONFIRMED:
            raise ValueError("dismissal cannot use the evidence_confirmed reason code")
        if self.action is not ReviewAction.DISMISS_WITH_REASON and self.reason_code is not DecisionReasonCode.EVIDENCE_CONFIRMED:
            raise ValueError("routing decisions must use the evidence_confirmed reason code")
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
        for name in (
            "packet_record_hash", "packet_hash", "automation_plan_hash",
            "automation_policy_hash", "decision_hash",
        ):
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
            "reason_code": self.reason_code.value,
            "reason": self.reason,
            "actor_id": self.actor_id,
            "actor_roles": [role.value for role in self.actor_roles],
            "decided_at": self.decided_at,
            "packet_record_hash": self.packet_record_hash,
            "packet_hash": self.packet_hash,
            "automation_plan_hash": self.automation_plan_hash,
            "automation_policy_hash": self.automation_policy_hash,
            "idempotency_key": self.idempotency_key,
            "previous_decision_hash": self.previous_decision_hash,
            "decision_hash": self.decision_hash,
        }


class DecisionRepository(Protocol):
    def append(self, decision: ReviewDecision, *, expected_previous_hash: str | None) -> None: ...

    def list_for_packet(self, tenant_id: str, workspace_id: str, packet_id: str) -> Sequence[ReviewDecision]: ...

    def find_by_idempotency(
        self, tenant_id: str, workspace_id: str, idempotency_key: str
    ) -> ReviewDecision | None: ...


class SQLiteDecisionRepository:
    """Durable reference repository; every query is explicitly tenant scoped."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._open() as connection, connection:
            existing = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_decisions'"
            ).fetchone()
            if existing:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(review_decisions)").fetchall()
                }
                if "idempotency_key" not in columns:
                    raise RuntimeError(
                        "legacy review decision database uses schema v1; archive/export it and "
                        "initialize a v2 database because packet and automation provenance cannot be backfilled safely"
                    )
            connection.execute("""CREATE TABLE IF NOT EXISTS review_decisions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, decision_id TEXT NOT NULL UNIQUE,
                tenant_id TEXT NOT NULL, workspace_id TEXT NOT NULL, packet_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, payload TEXT NOT NULL,
                decision_hash TEXT NOT NULL UNIQUE,
                UNIQUE(tenant_id, workspace_id, idempotency_key))""")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_decisions_scope ON review_decisions(tenant_id, workspace_id, packet_id, sequence)")

    def _open(self) -> contextlib.closing[sqlite3.Connection]:
        return _open_sqlite(self.path)

    def append(self, decision: ReviewDecision, *, expected_previous_hash: str | None) -> None:
        with self._open() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT decision_hash FROM review_decisions WHERE tenant_id=? AND workspace_id=? AND packet_id=? ORDER BY sequence DESC LIMIT 1",
                (decision.tenant_id, decision.workspace_id, decision.packet_id),
            ).fetchone()
            current = row[0] if row else None
            if current != expected_previous_hash or decision.previous_decision_hash != current:
                raise ValueError("review decision chain changed; reload before submitting")
            connection.execute(
                "INSERT INTO review_decisions(decision_id,tenant_id,workspace_id,packet_id,idempotency_key,payload,decision_hash) VALUES(?,?,?,?,?,?,?)",
                (
                    decision.decision_id, decision.tenant_id, decision.workspace_id,
                    decision.packet_id, decision.idempotency_key,
                    json.dumps(decision.to_dict(), sort_keys=True), decision.decision_hash,
                ),
            )

    def list_for_packet(self, tenant_id: str, workspace_id: str, packet_id: str) -> Sequence[ReviewDecision]:
        with self._open() as connection:
            rows = connection.execute(
                "SELECT payload FROM review_decisions WHERE tenant_id=? AND workspace_id=? AND packet_id=? ORDER BY sequence",
                (tenant_id, workspace_id, packet_id),
            ).fetchall()
        return tuple(_decision_from_dict(json.loads(row[0])) for row in rows)

    def find_by_idempotency(
        self, tenant_id: str, workspace_id: str, idempotency_key: str
    ) -> ReviewDecision | None:
        with self._open() as connection:
            row = connection.execute(
                "SELECT payload FROM review_decisions WHERE tenant_id=? AND workspace_id=? AND idempotency_key=?",
                (tenant_id, workspace_id, idempotency_key),
            ).fetchone()
        return None if row is None else _decision_from_dict(json.loads(row[0]))


class ReviewWorkflowService:
    def __init__(self, repository: DecisionRepository, clock: Callable[[], datetime] | None = None) -> None:
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(UTC))

    def submit(
        self, *, packet: Mapping[str, Any], automation_plan: Mapping[str, Any],
        actor: ReviewerIdentity, finding_id: str, action: ReviewAction, reason: str,
        reason_code: DecisionReasonCode, idempotency_key: str,
    ) -> ReviewDecision:
        _tenant, controls = _verify_packet_and_plan_scope(packet, automation_plan, actor)
        if action.value not in controls.get("permitted_actions", []):
            raise PermissionError("action is not permitted by the review packet")
        automation_items = automation_plan.get("findings")
        automation = next(
            (
                item for item in automation_items or []
                if isinstance(item, Mapping) and item.get("finding_id") == finding_id
            ),
            None,
        )
        if not isinstance(automation, Mapping):
            raise ValueError("finding does not belong to automation plan")
        packet_finding = next(
            (
                item for item in packet.get("findings", [])
                if isinstance(item, Mapping) and item.get("finding_id") == finding_id
            ),
            None,
        )
        # GOVERNANCE SEGREGATION: a clinical_care_gap finding is decided ONLY through the
        # GapClosureService (CARE_GAP_COORDINATOR role, hash-chained GapClosureRecord
        # lifecycle). An escalated/focused gap carries "dismiss_with_reason" in its
        # automation allowed_actions, so without this guard a reviewer holding revenue
        # dismiss authority could dispose of a clinical gap via the revenue decision path,
        # bypassing the coordinator role and the gap closure ledger. Reject before any
        # persistence — the inverse guard lives in GapClosureService.submit.
        if isinstance(packet_finding, Mapping) and packet_finding.get("gap_domain") is not None:
            raise PermissionError(
                "clinical_care_gap findings are decided via the gap closure service, "
                "not the revenue decision path"
            )
        canonical_finding = (
            _canonical_packet_finding(packet_finding)
            if isinstance(packet_finding, Mapping)
            else None
        )
        if (
            canonical_finding is None
            or automation.get("finding_hash") != canonical_hash(canonical_finding)
        ):
            raise ValueError("automation finding does not match the exact packet finding")
        if automation.get("tier") not in {
            AutomationTier.QUICK_CONFIRM.value,
            AutomationTier.FOCUSED_REVIEW.value,
            AutomationTier.ESCALATED.value,
        }:
            raise PermissionError("automation tier is not eligible for a human terminal decision")
        if finding_id not in automation_plan.get("review_now_finding_ids", []):
            raise PermissionError("finding is deferred or not selected for review")
        if action.value not in automation.get("allowed_actions", []):
            raise PermissionError("action is not permitted for this finding")
        allowed = set().union(*(ROLE_ACTIONS.get(role, frozenset()) for role in actor.roles))
        if action not in allowed:
            raise PermissionError("reviewer roles do not permit this action")
        if not isinstance(reason_code, DecisionReasonCode):
            raise ValueError("decision reason_code is unsupported")
        if action is ReviewAction.DISMISS_WITH_REASON and reason_code is DecisionReasonCode.EVIDENCE_CONFIRMED:
            raise ValueError("dismissal cannot use the evidence_confirmed reason code")
        if action is not ReviewAction.DISMISS_WITH_REASON and reason_code is not DecisionReasonCode.EVIDENCE_CONFIRMED:
            raise ValueError("routing decisions must use the evidence_confirmed reason code")
        findings = packet.get("findings")
        if not isinstance(findings, list) or finding_id not in {item.get("finding_id") for item in findings if isinstance(item, Mapping)}:
            raise ValueError("finding does not belong to review packet")
        reason = reason.strip()
        if not reason or len(reason) > 1000:
            raise ValueError("decision reason must contain 1 to 1000 characters")
        idempotency_key = idempotency_key.strip()
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("idempotency_key must contain 1 to 128 characters")
        existing = self.repository.find_by_idempotency(
            actor.tenant_id, actor.workspace_id, idempotency_key
        )
        if existing is not None:
            if not _matches_idempotent_request(
                existing, packet=packet, plan=automation_plan, actor=actor,
                finding_id=finding_id, action=action, reason_code=reason_code, reason=reason,
            ):
                raise ValueError("idempotency key was already used for a different decision")
            return existing
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("workflow clock must return a timezone-aware datetime")
        prior = self.repository.list_for_packet(actor.tenant_id, actor.workspace_id, str(packet["packet_id"]))
        if prior and not verify_decision_chain(prior):
            raise ValueError("existing review decision chain failed integrity verification")
        previous_hash = prior[-1].decision_hash if prior else None
        if any(item.finding_id == finding_id for item in prior):
            raise ValueError("finding already has a terminal decision; use a governed reversal")
        policy = automation_plan.get("policy")
        if not isinstance(policy, Mapping) or not isinstance(policy.get("digest"), str):
            raise ValueError("automation plan is missing policy provenance")
        body = {
            "decision_schema_version": DECISION_SCHEMA_VERSION,
            "tenant_id": actor.tenant_id, "workspace_id": actor.workspace_id,
            "packet_id": packet["packet_id"], "finding_id": finding_id,
            "action": action.value, "reason_code": reason_code.value,
            "reason": reason, "actor_id": actor.actor_id,
            "actor_roles": sorted(role.value for role in actor.roles),
            "decided_at": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "packet_record_hash": packet["provenance"]["record_hash"],
            "packet_hash": packet["provenance"]["packet_hash"],
            "automation_plan_hash": automation_plan["plan_hash"],
            "automation_policy_hash": policy["digest"],
            "idempotency_key": idempotency_key,
            "previous_decision_hash": previous_hash,
        }
        digest = canonical_hash(body)
        decision = ReviewDecision(
            decision_id=f"decision-{digest[:20]}", tenant_id=actor.tenant_id,
            workspace_id=actor.workspace_id, packet_id=str(packet["packet_id"]),
            finding_id=finding_id, action=action, reason_code=reason_code,
            reason=reason, actor_id=actor.actor_id,
            actor_roles=tuple(sorted(actor.roles, key=str)), decided_at=body["decided_at"],
            packet_record_hash=str(packet["provenance"]["record_hash"]),
            packet_hash=str(packet["provenance"]["packet_hash"]),
            automation_plan_hash=str(automation_plan["plan_hash"]),
            automation_policy_hash=str(policy["digest"]),
            idempotency_key=idempotency_key,
            previous_decision_hash=previous_hash, decision_hash=digest,
        )
        try:
            self.repository.append(decision, expected_previous_hash=previous_hash)
            return decision
        except (sqlite3.IntegrityError, ValueError):
            # A concurrent retry may win after the preflight lookup. Return it
            # only when it is the exact same request; otherwise preserve the
            # optimistic-concurrency failure.
            winner = self.repository.find_by_idempotency(
                actor.tenant_id, actor.workspace_id, idempotency_key
            )
            if winner is not None and _matches_idempotent_request(
                winner, packet=packet, plan=automation_plan, actor=actor,
                finding_id=finding_id, action=action, reason_code=reason_code, reason=reason,
            ):
                return winner
            raise


GAP_CLOSURE_SCHEMA_VERSION = "1.0.0"


class GapClosureAction(StrEnum):
    """Terminal human decisions on a surfaced clinical care gap.

    None of these touch a claim, a DRG, or a payment. They fold a coordinator's decision
    into a :class:`~revenue_integrity.models.GapStatus` change on the gap finding.
    """

    CLOSE = "close"
    EXCEPTION = "exception"
    WITHDRAW = "withdraw"


#: Deterministic map from a coordinator action to the resulting gap lifecycle status.
_GAP_ACTION_TO_STATUS: Mapping[GapClosureAction, GapStatus] = {
    GapClosureAction.CLOSE: GapStatus.CLOSED,
    GapClosureAction.EXCEPTION: GapStatus.EXCEPTION,
    GapClosureAction.WITHDRAW: GapStatus.WITHDRAWN,
}

#: Roles authorized to record a gap closure/exception/withdrawal. Analytics identify the
#: gap; only an authorized clinical role decides. ADMIN retains break-glass authority.
_GAP_CLOSURE_ROLES: frozenset[ReviewerRole] = frozenset(
    {ReviewerRole.CARE_GAP_COORDINATOR, ReviewerRole.ADMIN}
)


@dataclass(frozen=True, slots=True)
class GapClosureRecord:
    """Immutable, hash-chained record of a human gap-lifecycle decision.

    The record is chained per (tenant, workspace, packet) exactly like the revenue decision
    chain: each record links to its predecessor's ``record_hash`` and re-derives its own via
    :func:`~revenue_integrity.audit.canonical_hash`, so the whole history is tamper-evident.
    """

    closure_id: str
    tenant_id: str
    workspace_id: str
    packet_id: str
    finding_id: str
    action: GapClosureAction
    gap_status: GapStatus
    barrier_code: str | None
    reason: str
    actor_id: str
    actor_roles: tuple[ReviewerRole, ...]
    decided_at: str
    closed_at: str | None
    packet_record_hash: str
    packet_hash: str
    automation_plan_hash: str
    idempotency_key: str
    previous_record_hash: str | None
    record_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_closure_schema_version": GAP_CLOSURE_SCHEMA_VERSION,
            "closure_id": self.closure_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "packet_id": self.packet_id,
            "finding_id": self.finding_id,
            "action": self.action.value,
            "gap_status": self.gap_status.value,
            "barrier_code": self.barrier_code,
            "reason": self.reason,
            "actor_id": self.actor_id,
            "actor_roles": [role.value for role in self.actor_roles],
            "decided_at": self.decided_at,
            "closed_at": self.closed_at,
            "packet_record_hash": self.packet_record_hash,
            "packet_hash": self.packet_hash,
            "automation_plan_hash": self.automation_plan_hash,
            "idempotency_key": self.idempotency_key,
            "previous_record_hash": self.previous_record_hash,
            "record_hash": self.record_hash,
        }


class SQLiteGapClosureRepository:
    """Durable, tenant-scoped, hash-chained store for gap-closure records."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._open() as connection, connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS gap_closures (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, closure_id TEXT NOT NULL UNIQUE,
                tenant_id TEXT NOT NULL, workspace_id TEXT NOT NULL, packet_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, payload TEXT NOT NULL,
                record_hash TEXT NOT NULL UNIQUE,
                UNIQUE(tenant_id, workspace_id, idempotency_key))""")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_gap_closures_scope "
                "ON gap_closures(tenant_id, workspace_id, packet_id, sequence)"
            )

    def _open(self) -> contextlib.closing[sqlite3.Connection]:
        return _open_sqlite(self.path)

    def append(self, record: GapClosureRecord, *, expected_previous_hash: str | None) -> None:
        with self._open() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT record_hash FROM gap_closures WHERE tenant_id=? AND workspace_id=? AND packet_id=? ORDER BY sequence DESC LIMIT 1",
                (record.tenant_id, record.workspace_id, record.packet_id),
            ).fetchone()
            current = row[0] if row else None
            if current != expected_previous_hash or record.previous_record_hash != current:
                raise ValueError("gap closure chain changed; reload before submitting")
            connection.execute(
                "INSERT INTO gap_closures(closure_id,tenant_id,workspace_id,packet_id,idempotency_key,payload,record_hash) VALUES(?,?,?,?,?,?,?)",
                (
                    record.closure_id, record.tenant_id, record.workspace_id,
                    record.packet_id, record.idempotency_key,
                    json.dumps(record.to_dict(), sort_keys=True), record.record_hash,
                ),
            )

    def list_for_packet(self, tenant_id: str, workspace_id: str, packet_id: str) -> Sequence[GapClosureRecord]:
        with self._open() as connection:
            rows = connection.execute(
                "SELECT payload FROM gap_closures WHERE tenant_id=? AND workspace_id=? AND packet_id=? ORDER BY sequence",
                (tenant_id, workspace_id, packet_id),
            ).fetchall()
        return tuple(_gap_closure_from_dict(json.loads(row[0])) for row in rows)

    def find_by_idempotency(
        self, tenant_id: str, workspace_id: str, idempotency_key: str
    ) -> GapClosureRecord | None:
        with self._open() as connection:
            row = connection.execute(
                "SELECT payload FROM gap_closures WHERE tenant_id=? AND workspace_id=? AND idempotency_key=?",
                (tenant_id, workspace_id, idempotency_key),
            ).fetchone()
        return None if row is None else _gap_closure_from_dict(json.loads(row[0]))


class GapClosureService:
    """Governed close / exception / withdraw transitions for clinical care gaps.

    Mirrors :class:`ReviewWorkflowService` validation (packet + plan integrity, tenant scope,
    exact packet-finding match, idempotency, hash-chained persistence) but on the separate
    gap lifecycle. THE WALL: a gap closure can only target a clinical_care_gap finding, can
    never carry a claim mutation, and must be recorded by an authorized clinical role. Revenue
    decision recording is untouched.
    """

    def __init__(self, repository: SQLiteGapClosureRepository, clock: Callable[[], datetime] | None = None) -> None:
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(UTC))

    def submit(
        self, *, packet: Mapping[str, Any], automation_plan: Mapping[str, Any],
        actor: ReviewerIdentity, finding_id: str, action: GapClosureAction, reason: str,
        idempotency_key: str, barrier_code: str | None = None,
        claim_mutation: Mapping[str, Any] | None = None,
    ) -> GapClosureRecord:
        if not isinstance(action, GapClosureAction):
            raise ValueError("gap closure action is unsupported")
        # THE WALL: a gap closure may never carry a claim mutation of any kind.
        if claim_mutation:
            raise PermissionError("a gap closure action must not carry a claim mutation")
        _verify_packet_and_plan_scope(packet, automation_plan, actor)
        # Only an authorized clinical role may decide a gap.
        if not _GAP_CLOSURE_ROLES.intersection(actor.roles):
            raise PermissionError("reviewer roles do not permit a gap closure decision")
        packet_finding = next(
            (
                item for item in packet.get("findings", [])
                if isinstance(item, Mapping) and item.get("finding_id") == finding_id
            ),
            None,
        )
        if not isinstance(packet_finding, Mapping):
            raise ValueError("finding does not belong to review packet")
        # The action must target a clinical_care_gap finding; a revenue finding is off-limits.
        if packet_finding.get("gap_domain") is None:
            raise ValueError("gap closure can only target a clinical_care_gap finding")
        if dict(packet_finding.get("proposed_change") or {}):
            # Defensive: a gap finding must never carry a claim-mutating change.
            raise PermissionError("clinical_care_gap finding unexpectedly carries a claim mutation")
        automation = next(
            (
                item for item in automation_plan.get("findings") or []
                if isinstance(item, Mapping) and item.get("finding_id") == finding_id
            ),
            None,
        )
        if not isinstance(automation, Mapping):
            raise ValueError("finding does not belong to automation plan")
        canonical_finding = _canonical_packet_finding(packet_finding)
        if automation.get("finding_hash") != canonical_hash(canonical_finding):
            raise ValueError("automation finding does not match the exact packet finding")
        reason = reason.strip()
        if not reason or len(reason) > 1000:
            raise ValueError("gap closure reason must contain 1 to 1000 characters")
        if barrier_code is not None:
            barrier_code = barrier_code.strip()
            if not barrier_code or len(barrier_code) > 128:
                raise ValueError("gap closure barrier_code must contain 1 to 128 characters")
        # A barrier code documents an obstacle to closure; it is only meaningful when the gap
        # is not being closed as done. Keep it permissive but bounded.
        idempotency_key = idempotency_key.strip()
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("idempotency_key must contain 1 to 128 characters")
        existing = self.repository.find_by_idempotency(
            actor.tenant_id, actor.workspace_id, idempotency_key
        )
        if existing is not None:
            if not _matches_idempotent_closure(
                existing, packet=packet, plan=automation_plan, actor=actor,
                finding_id=finding_id, action=action, reason=reason, barrier_code=barrier_code,
            ):
                raise ValueError("idempotency key was already used for a different gap closure")
            return existing
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("workflow clock must return a timezone-aware datetime")
        prior = self.repository.list_for_packet(actor.tenant_id, actor.workspace_id, str(packet["packet_id"]))
        if prior and not verify_gap_closure_chain(prior):
            raise ValueError("existing gap closure chain failed integrity verification")
        if any(item.finding_id == finding_id for item in prior):
            raise ValueError("gap already has a terminal decision; use a governed reversal")
        previous_hash = prior[-1].record_hash if prior else None
        gap_status = _GAP_ACTION_TO_STATUS[action]
        decided_at = now.astimezone(UTC).isoformat().replace("+00:00", "Z")
        # A closed/exception gap is stamped; a withdrawal has no closure timestamp.
        closed_at = decided_at if action in {GapClosureAction.CLOSE, GapClosureAction.EXCEPTION} else None
        body = {
            "gap_closure_schema_version": GAP_CLOSURE_SCHEMA_VERSION,
            "tenant_id": actor.tenant_id, "workspace_id": actor.workspace_id,
            "packet_id": packet["packet_id"], "finding_id": finding_id,
            "action": action.value, "gap_status": gap_status.value,
            "barrier_code": barrier_code, "reason": reason, "actor_id": actor.actor_id,
            "actor_roles": sorted(role.value for role in actor.roles),
            "decided_at": decided_at, "closed_at": closed_at,
            "packet_record_hash": packet["provenance"]["record_hash"],
            "packet_hash": packet["provenance"]["packet_hash"],
            "automation_plan_hash": automation_plan["plan_hash"],
            "idempotency_key": idempotency_key,
            "previous_record_hash": previous_hash,
        }
        digest = canonical_hash(body)
        record = GapClosureRecord(
            closure_id=f"gap-closure-{digest[:20]}", tenant_id=actor.tenant_id,
            workspace_id=actor.workspace_id, packet_id=str(packet["packet_id"]),
            finding_id=finding_id, action=action, gap_status=gap_status,
            barrier_code=barrier_code, reason=reason, actor_id=actor.actor_id,
            actor_roles=tuple(sorted(actor.roles, key=str)), decided_at=decided_at,
            closed_at=closed_at,
            packet_record_hash=str(packet["provenance"]["record_hash"]),
            packet_hash=str(packet["provenance"]["packet_hash"]),
            automation_plan_hash=str(automation_plan["plan_hash"]),
            idempotency_key=idempotency_key, previous_record_hash=previous_hash,
            record_hash=digest,
        )
        try:
            self.repository.append(record, expected_previous_hash=previous_hash)
            return record
        except (sqlite3.IntegrityError, ValueError):
            winner = self.repository.find_by_idempotency(
                actor.tenant_id, actor.workspace_id, idempotency_key
            )
            if winner is not None and _matches_idempotent_closure(
                winner, packet=packet, plan=automation_plan, actor=actor,
                finding_id=finding_id, action=action, reason=reason, barrier_code=barrier_code,
            ):
                return winner
            raise


def _verify_hash_chain(
    records: Sequence[Any], *, hash_key: str, id_key: str, previous_attr: str, id_prefix: str
) -> bool:
    """Tamper-evidence check shared by the revenue-decision and gap-closure chains.

    Each record type is chained the same way: every entry links to its predecessor's hash
    and re-derives its own via :func:`~revenue_integrity.audit.canonical_hash` over its
    ``to_dict()`` payload with the hash field and the derived id field removed, and its id is
    ``f"{id_prefix}{hash[:20]}"``. The chains differ only in the field/attribute names, so
    this helper is parameterized by them; the verification logic is identical, preventing the
    two chain verifiers from drifting.
    """
    previous: str | None = None
    for record in records:
        payload = record.to_dict()
        claimed = payload.pop(hash_key)
        record_id = payload.pop(id_key)
        if (getattr(record, previous_attr) != previous or canonical_hash(payload) != claimed
                or record_id != f"{id_prefix}{claimed[:20]}"):
            return False
        previous = claimed
    return True


def verify_gap_closure_chain(records: Sequence[GapClosureRecord]) -> bool:
    return _verify_hash_chain(
        records, hash_key="record_hash", id_key="closure_id",
        previous_attr="previous_record_hash", id_prefix="gap-closure-",
    )


def _gap_closure_from_dict(data: Mapping[str, Any]) -> GapClosureRecord:
    if data.get("gap_closure_schema_version") != GAP_CLOSURE_SCHEMA_VERSION:
        raise ValueError("unsupported gap closure schema version")
    return GapClosureRecord(
        closure_id=data["closure_id"], tenant_id=data["tenant_id"], workspace_id=data["workspace_id"],
        packet_id=data["packet_id"], finding_id=data["finding_id"],
        action=GapClosureAction(data["action"]), gap_status=GapStatus(data["gap_status"]),
        barrier_code=data["barrier_code"], reason=data["reason"], actor_id=data["actor_id"],
        actor_roles=tuple(ReviewerRole(role) for role in data["actor_roles"]),
        decided_at=data["decided_at"], closed_at=data["closed_at"],
        packet_record_hash=data["packet_record_hash"], packet_hash=data["packet_hash"],
        automation_plan_hash=data["automation_plan_hash"], idempotency_key=data["idempotency_key"],
        previous_record_hash=data["previous_record_hash"], record_hash=data["record_hash"],
    )


def _matches_idempotent_closure(
    record: GapClosureRecord, *, packet: Mapping[str, Any], plan: Mapping[str, Any],
    actor: ReviewerIdentity, finding_id: str, action: GapClosureAction, reason: str,
    barrier_code: str | None,
) -> bool:
    provenance = packet.get("provenance")
    packet_hash = provenance.get("packet_hash") if isinstance(provenance, Mapping) else None
    return (
        record.packet_id == packet.get("packet_id")
        and record.finding_id == finding_id
        and record.action is action
        and record.reason == reason
        and record.barrier_code == barrier_code
        and record.actor_id == actor.actor_id
        and record.actor_roles == tuple(sorted(actor.roles, key=str))
        and record.packet_hash == packet_hash
        and record.automation_plan_hash == plan.get("plan_hash")
    )


def verify_decision_chain(decisions: Sequence[ReviewDecision]) -> bool:
    return _verify_hash_chain(
        decisions, hash_key="decision_hash", id_key="decision_id",
        previous_attr="previous_decision_hash", id_prefix="decision-",
    )


def _decision_from_dict(data: Mapping[str, Any]) -> ReviewDecision:
    if data.get("decision_schema_version") != DECISION_SCHEMA_VERSION:
        raise ValueError("unsupported review decision schema version")
    return ReviewDecision(
        decision_id=data["decision_id"], tenant_id=data["tenant_id"], workspace_id=data["workspace_id"],
        packet_id=data["packet_id"], finding_id=data["finding_id"], action=ReviewAction(data["action"]),
        reason_code=DecisionReasonCode(data["reason_code"]),
        reason=data["reason"], actor_id=data["actor_id"], actor_roles=tuple(ReviewerRole(role) for role in data["actor_roles"]),
        decided_at=data["decided_at"], packet_record_hash=data["packet_record_hash"],
        packet_hash=data["packet_hash"], automation_plan_hash=data["automation_plan_hash"],
        automation_policy_hash=data["automation_policy_hash"], idempotency_key=data["idempotency_key"],
        previous_decision_hash=data["previous_decision_hash"], decision_hash=data["decision_hash"],
    )


def summarize_decision_feedback(decisions: Sequence[ReviewDecision]) -> dict[str, Any]:
    """Aggregate governed labels for offline evaluation; never changes policy automatically."""
    total = len(decisions)
    accepted = sum(item.action is not ReviewAction.DISMISS_WITH_REASON for item in decisions)
    by_reason = {
        reason.value: sum(item.reason_code is reason for item in decisions)
        for reason in DecisionReasonCode
    }
    return {
        "total_decisions": total,
        "accepted": accepted,
        "dismissed": total - accepted,
        "acceptance_rate": None if total == 0 else accepted / total,
        "by_reason_code": by_reason,
    }


def _is_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _matches_idempotent_request(
    decision: ReviewDecision, *, packet: Mapping[str, Any], plan: Mapping[str, Any],
    actor: ReviewerIdentity, finding_id: str, action: ReviewAction,
    reason_code: DecisionReasonCode, reason: str,
) -> bool:
    provenance = packet.get("provenance")
    packet_hash = provenance.get("packet_hash") if isinstance(provenance, Mapping) else None
    return (
        decision.packet_id == packet.get("packet_id")
        and decision.finding_id == finding_id
        and decision.action is action
        and decision.reason_code is reason_code
        and decision.reason == reason
        and decision.actor_id == actor.actor_id
        and decision.actor_roles == tuple(sorted(actor.roles, key=str))
        and decision.packet_hash == packet_hash
        and decision.automation_plan_hash == plan.get("plan_hash")
    )
