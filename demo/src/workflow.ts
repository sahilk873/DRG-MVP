import type { ReviewPacket } from './review-packet'
import type { AutomationPlan } from './automation-plan'

export type ReviewerRole = 'coder' | 'cdi_specialist' | 'charge_reviewer' | 'compliance_reviewer' | 'admin' | 'read_only'
export type ReviewAction = 'route_to_coding' | 'route_to_cdi' | 'route_to_charge_review' | 'route_to_compliance' | 'dismiss_with_reason'
export type DecisionReasonCode = 'evidence_confirmed' | 'documentation_not_supported' | 'duplicate' | 'already_corrected' | 'other_governed'

export interface ReviewerIdentity {
  actor_id: string
  tenant_id: string
  workspace_id: string
  roles: ReviewerRole[]
}

export interface ReviewDecision {
  decision_id: string
  packet_id: string
  finding_id: string
  action: ReviewAction
  reason_code: DecisionReasonCode
  reason: string
  actor_id: string
  decided_at: string
  packet_record_hash: string
  packet_hash: string
  automation_plan_hash: string
  automation_policy_hash: string
  idempotency_key: string
}

export interface ReviewWorkflowGateway {
  submit(packet: ReviewPacket, plan: AutomationPlan, actor: ReviewerIdentity, findingId: string, action: ReviewAction, reasonCode: DecisionReasonCode, reason: string, idempotencyKey: string): Promise<ReviewDecision>
  list(packet: ReviewPacket, actor: ReviewerIdentity): Promise<ReviewDecision[]>
}

const roleActions: Record<ReviewerRole, ReviewAction[]> = {
  coder: ['route_to_coding', 'route_to_cdi', 'dismiss_with_reason'],
  cdi_specialist: ['route_to_cdi', 'route_to_coding', 'dismiss_with_reason'],
  charge_reviewer: ['route_to_charge_review', 'route_to_compliance', 'dismiss_with_reason'],
  compliance_reviewer: ['route_to_compliance', 'dismiss_with_reason'],
  admin: ['route_to_coding', 'route_to_cdi', 'route_to_charge_review', 'route_to_compliance', 'dismiss_with_reason'],
  read_only: [],
}

/** Synthetic browser adapter. Production injects the authenticated workflow API. */
export class BrowserDemoWorkflowGateway implements ReviewWorkflowGateway {
  constructor(private readonly storage: Storage) {}

  async submit(packet: ReviewPacket, plan: AutomationPlan, actor: ReviewerIdentity, findingId: string, action: ReviewAction, reasonCode: DecisionReasonCode, reason: string, idempotencyKey: string): Promise<ReviewDecision> {
    this.authorize(packet, actor, action)
    this.authorizePlan(packet, plan, findingId, action)
    if (!packet.findings.some(finding => finding.finding_id === findingId)) throw new Error('Finding is not part of this packet')
    if (action === 'dismiss_with_reason' && reasonCode === 'evidence_confirmed') throw new Error('Dismissal requires a governed dismissal reason code')
    if (action !== 'dismiss_with_reason' && reasonCode !== 'evidence_confirmed') throw new Error('Routing requires the evidence_confirmed reason code')
    const normalizedReason = reason.trim()
    if (!normalizedReason || normalizedReason.length > 1000) throw new Error('A governed decision reason is required')
    const normalizedKey = idempotencyKey.trim()
    if (!normalizedKey || normalizedKey.length > 128) throw new Error('A valid idempotency key is required')
    const existing = await this.list(packet, actor)
    const retried = existing.find(item => item.idempotency_key === normalizedKey)
    if (retried) {
      if (
        retried.finding_id !== findingId
        || retried.action !== action
        || retried.reason_code !== reasonCode
        || retried.reason !== normalizedReason
        || retried.packet_hash !== packet.provenance.packet_hash
        || retried.automation_plan_hash !== plan.plan_hash
      ) throw new Error('Idempotency key was used for another decision')
      return retried
    }
    if (existing.some(item => item.finding_id === findingId)) throw new Error('This finding already has a terminal decision')
    const decidedAt = new Date().toISOString()
    const decision: ReviewDecision = {
      decision_id: `demo-decision-${normalizedKey}`,
      packet_id: packet.packet_id,
      finding_id: findingId,
      action,
      reason_code: reasonCode,
      reason: normalizedReason,
      actor_id: actor.actor_id,
      decided_at: decidedAt,
      packet_record_hash: packet.provenance.record_hash,
      packet_hash: packet.provenance.packet_hash,
      automation_plan_hash: plan.plan_hash,
      automation_policy_hash: plan.policy.digest,
      idempotency_key: normalizedKey,
    }
    this.storage.setItem(this.key(packet), JSON.stringify([...existing, decision]))
    return decision
  }

  async list(packet: ReviewPacket, actor: ReviewerIdentity): Promise<ReviewDecision[]> {
    this.authorizeScope(packet, actor)
    const value = this.storage.getItem(this.key(packet))
    if (!value) return []
    try {
      const parsed: unknown = JSON.parse(value)
      return Array.isArray(parsed)
        ? parsed.filter((item): item is ReviewDecision => isDecision(item)
          && item.packet_id === packet.packet_id
          && item.packet_record_hash === packet.provenance.record_hash
          && item.packet_hash === packet.provenance.packet_hash)
        : []
    } catch {
      return []
    }
  }

  /** Presenter-only reset for the synthetic browser demo. Not part of the production gateway contract. */
  async reset(packet: ReviewPacket, actor: ReviewerIdentity): Promise<void> {
    this.authorizeScope(packet, actor)
    this.storage.removeItem(this.key(packet))
  }

  private authorize(packet: ReviewPacket, actor: ReviewerIdentity, action: ReviewAction) {
    this.authorizeScope(packet, actor)
    if (!packet.controls.permitted_actions.includes(action)) throw new Error('Action is not permitted by the review packet')
    if (!actor.roles.some(role => roleActions[role].includes(action))) throw new Error('Your role does not permit this action')
  }

  private authorizeScope(packet: ReviewPacket, actor: ReviewerIdentity) {
    if (packet.tenant.tenant_id !== actor.tenant_id || packet.tenant.workspace_id !== actor.workspace_id) {
      throw new Error('Reviewer and packet tenant scope do not match')
    }
  }

  private authorizePlan(packet: ReviewPacket, plan: AutomationPlan, findingId: string, action: ReviewAction) {
    if (
      plan.tenant.tenant_id !== packet.tenant.tenant_id
      || plan.tenant.workspace_id !== packet.tenant.workspace_id
      || plan.packet.packet_id !== packet.packet_id
      || plan.packet.packet_hash !== packet.provenance.packet_hash
    ) throw new Error('Automation plan does not match this review packet')
    const item = plan.findings.find(candidate => candidate.finding_id === findingId)
    if (!item || !plan.review_now_finding_ids.includes(findingId)) throw new Error('Finding is not selected for human review')
    if (!item.allowed_actions.includes(action)) throw new Error('Action is not permitted for this finding')
  }

  private key(packet: ReviewPacket) {
    return `encounter-demo:${packet.tenant.tenant_id}:${packet.tenant.workspace_id}:${packet.packet_id}`
  }
}

function isDecision(value: unknown): value is ReviewDecision {
  if (!value || typeof value !== 'object') return false
  const decision = value as Partial<ReviewDecision>
  return typeof decision.decision_id === 'string'
    && typeof decision.packet_id === 'string'
    && typeof decision.finding_id === 'string'
    && typeof decision.reason === 'string'
    && ['evidence_confirmed', 'documentation_not_supported', 'duplicate', 'already_corrected', 'other_governed'].includes(decision.reason_code ?? '')
    && typeof decision.actor_id === 'string'
    && typeof decision.decided_at === 'string'
    && typeof decision.packet_record_hash === 'string'
    && typeof decision.packet_hash === 'string'
    && typeof decision.automation_plan_hash === 'string'
    && typeof decision.automation_policy_hash === 'string'
    && typeof decision.idempotency_key === 'string'
    && ['route_to_coding', 'route_to_cdi', 'route_to_charge_review', 'route_to_compliance', 'dismiss_with_reason'].includes(decision.action ?? '')
}
