from __future__ import annotations

import contextlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .audit import canonical_hash
from .automation import AutomationTier, verify_automation_plan_hash
from .models import LifecycleState
from .workflow import _open_sqlite


#: The automation queue value that marks a clinical_care_gap finding. Its auto-routed
#: findings ride the dedicated ``CARE_GAP_ALERT`` lanes below, never the revenue lanes.
_CARE_GAP_QUEUE = "care_gap"


class RoutingLane(StrEnum):
    """Where in the encounter lifecycle a governed route surfaces.

    ``RETROSPECTIVE_CORRECTION`` is the default lane (the historical behavior): the claim
    has billed, so the route is a post-hoc correction. ``PROSPECTIVE_QUERY`` fires when the
    encounter has not billed yet (prospective/concurrent), so the same governed queue action
    is surfaced *before* billing as a pre-bill query. The lane never changes the queue or the
    required review — it only records the lifecycle position.

    ``CARE_GAP_ALERT`` (and its prospective variant) are a fully separate lane for
    auto-routed clinical_care_gap findings. They carry the analytics alert to the care team;
    they never mutate a claim, assign a DRG, or bypass review, and they are structurally
    distinct from the revenue lanes so revenue routing stays byte-identical.
    """

    RETROSPECTIVE_CORRECTION = "retrospective_correction"
    PROSPECTIVE_QUERY = "prospective_query"
    CARE_GAP_ALERT = "care_gap_alert"
    CARE_GAP_ALERT_PROSPECTIVE = "care_gap_alert_prospective"


#: Lifecycle states for which a raised finding is surfaced as a pre-bill query rather than a
#: retrospective correction. Deterministic; drives ``RoutingLane`` selection.
_PROSPECTIVE_LIFECYCLE_STATES = frozenset(
    {LifecycleState.PROSPECTIVE, LifecycleState.CONCURRENT}
)


def route_lane_for_lifecycle(lifecycle_state: LifecycleState) -> RoutingLane:
    """Deterministic lane from encounter lifecycle position.

    Prospective/concurrent encounters (not yet billed) surface eligible findings as a
    pre-bill ``PROSPECTIVE_QUERY``; retrospective/post-bill encounters keep the historical
    ``RETROSPECTIVE_CORRECTION`` lane. This never bypasses the governed queue action or the
    human review the automation plan already required.
    """
    if lifecycle_state in _PROSPECTIVE_LIFECYCLE_STATES:
        return RoutingLane.PROSPECTIVE_QUERY
    return RoutingLane.RETROSPECTIVE_CORRECTION


def care_gap_lane_for_lifecycle(lifecycle_state: LifecycleState) -> RoutingLane:
    """Deterministic CARE_GAP_ALERT lane for an auto-routed clinical care gap.

    Mirrors :func:`route_lane_for_lifecycle` on the separate gap lane: prospective/concurrent
    encounters get ``CARE_GAP_ALERT_PROSPECTIVE`` (a pre-bill clinical alert), everything else
    gets ``CARE_GAP_ALERT``. The lane never changes the queue action or the required review.
    """
    if lifecycle_state in _PROSPECTIVE_LIFECYCLE_STATES:
        return RoutingLane.CARE_GAP_ALERT_PROSPECTIVE
    return RoutingLane.CARE_GAP_ALERT


def _lane_for_route(queue: str, lifecycle_state: LifecycleState) -> RoutingLane:
    """Pick the lane for a single auto-routed finding from its queue + lifecycle position."""
    if queue == _CARE_GAP_QUEUE:
        return care_gap_lane_for_lifecycle(lifecycle_state)
    return route_lane_for_lifecycle(lifecycle_state)


@dataclass(frozen=True, slots=True)
class RouteTask:
    route_id: str
    tenant_id: str
    workspace_id: str
    packet_id: str
    packet_hash: str
    automation_plan_hash: str
    automation_id: str
    finding_id: str
    queue: str
    action: str
    created_at: str
    status: str = "pending"
    # Additive, deterministic lifecycle lane. Defaults to the retrospective correction lane
    # so legacy (retrospective) routes stay byte-identical, including their route_id.
    lane: str = RoutingLane.RETROSPECTIVE_CORRECTION.value

    def to_dict(self) -> dict[str, Any]:
        body = {
            "route_id": self.route_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "packet_id": self.packet_id,
            "packet_hash": self.packet_hash,
            "automation_plan_hash": self.automation_plan_hash,
            "automation_id": self.automation_id,
            "finding_id": self.finding_id,
            "queue": self.queue,
            "action": self.action,
            "created_at": self.created_at,
            "status": self.status,
        }
        # Only serialize the lane when it departs from the historical default so legacy
        # retrospective route payloads (and their round-trip) remain byte-identical.
        if self.lane != RoutingLane.RETROSPECTIVE_CORRECTION.value:
            body["lane"] = self.lane
        return body


class SQLiteRoutingOutbox:
    """Transactional handoff for safe routes; downstream delivery is adapter-specific."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._open() as connection, connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS automation_route_outbox (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                route_id TEXT NOT NULL UNIQUE,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                automation_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'delivered')),
                UNIQUE(tenant_id, workspace_id, automation_id))""")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_route_outbox_pending "
                "ON automation_route_outbox(tenant_id, workspace_id, status, sequence)"
            )

    def _open(self) -> contextlib.closing[sqlite3.Connection]:
        return _open_sqlite(self.path)

    def enqueue_plan(
        self,
        plan: Mapping[str, Any],
        *,
        clock: Callable[[], datetime] | None = None,
        lifecycle_state: LifecycleState = LifecycleState.RETROSPECTIVE,
    ) -> tuple[RouteTask, ...]:
        if not verify_automation_plan_hash(plan):
            raise ValueError("automation plan failed integrity verification")
        now = (clock or (lambda: datetime.now(UTC)))()
        if now.tzinfo is None:
            raise ValueError("routing clock must return a timezone-aware datetime")
        created_at = now.astimezone(UTC).isoformat().replace("+00:00", "Z")
        tenant = _mapping(plan.get("tenant"), "automation plan tenant")
        packet = _mapping(plan.get("packet"), "automation plan packet")
        candidates = [
            item for item in plan.get("findings", [])
            if isinstance(item, Mapping) and item.get("tier") == AutomationTier.AUTO_ROUTED.value
        ]
        tasks = tuple(
            _build_task(
                item,
                tenant_id=_text(tenant.get("tenant_id"), "tenant_id"),
                workspace_id=_text(tenant.get("workspace_id"), "workspace_id"),
                packet_id=_text(packet.get("packet_id"), "packet_id"),
                packet_hash=_digest(packet.get("packet_hash"), "packet_hash"),
                plan_hash=_digest(plan.get("plan_hash"), "plan_hash"),
                created_at=created_at,
                lifecycle_state=lifecycle_state,
            )
            for item in sorted(candidates, key=lambda value: str(value.get("automation_id", "")))
        )
        with self._open() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            for task in tasks:
                connection.execute(
                    "INSERT OR IGNORE INTO automation_route_outbox"
                    "(route_id,tenant_id,workspace_id,automation_id,payload,status) VALUES(?,?,?,?,?,?)",
                    (
                        task.route_id, task.tenant_id, task.workspace_id, task.automation_id,
                        json.dumps(task.to_dict(), sort_keys=True), task.status,
                    ),
                )
        pending = self.list_pending(
            _text(tenant.get("tenant_id"), "tenant_id"),
            _text(tenant.get("workspace_id"), "workspace_id"),
        )
        candidate_ids = {task.automation_id for task in tasks}
        return tuple(
            task for task in pending
            if task.automation_plan_hash == plan["plan_hash"] and task.automation_id in candidate_ids
        )

    def list_pending(self, tenant_id: str, workspace_id: str) -> tuple[RouteTask, ...]:
        with self._open() as connection:
            rows = connection.execute(
                "SELECT payload FROM automation_route_outbox "
                "WHERE tenant_id=? AND workspace_id=? AND status='pending' ORDER BY sequence",
                (tenant_id, workspace_id),
            ).fetchall()
        return tuple(RouteTask(**json.loads(row[0])) for row in rows)

    def mark_delivered(self, tenant_id: str, workspace_id: str, route_id: str) -> None:
        with self._open() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE automation_route_outbox SET status='delivered' "
                "WHERE tenant_id=? AND workspace_id=? AND route_id=? AND status='pending'",
                (tenant_id, workspace_id, route_id),
            ).rowcount
            if changed != 1:
                raise ValueError("pending route task was not found in tenant scope")


def _build_task(
    item: Mapping[str, Any], *, tenant_id: str, workspace_id: str,
    packet_id: str, packet_hash: str, plan_hash: str, created_at: str,
    lifecycle_state: LifecycleState = LifecycleState.RETROSPECTIVE,
) -> RouteTask:
    automation_id = _text(item.get("automation_id"), "automation_id")
    finding_id = _text(item.get("finding_id"), "finding_id")
    queue = _text(item.get("queue"), "queue")
    action = _text(item.get("recommended_action"), "recommended_action")
    if queue == "none" or not action.startswith("route_to_"):
        raise ValueError("auto-routed finding requires a governed queue action")
    # A clinical care gap rides the dedicated CARE_GAP_ALERT lane; everything else keeps the
    # historical revenue lane selection. The lane records position only — never the action.
    lane = _lane_for_route(queue, lifecycle_state)
    body = {
        "tenant_id": tenant_id, "workspace_id": workspace_id,
        "packet_id": packet_id, "packet_hash": packet_hash,
        "automation_plan_hash": plan_hash, "automation_id": automation_id,
        "finding_id": finding_id, "queue": queue, "action": action,
    }
    # The lane joins the route_id hash only when it departs from the historical default so a
    # retrospective route keeps its exact route_id; a prospective query gets a distinct id.
    identity = body if lane is RoutingLane.RETROSPECTIVE_CORRECTION else {**body, "lane": lane.value}
    return RouteTask(
        route_id=f"route-{canonical_hash(identity)[:20]}", created_at=created_at,
        lane=lane.value, **body,
    )


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value


def _digest(value: Any, label: str) -> str:
    text = _text(value, label)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return text
