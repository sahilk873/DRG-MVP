import type { ReviewPacket } from './review-packet'

export type ReviewerRole = 'coder' | 'cdi_specialist' | 'charge_reviewer' | 'compliance_reviewer' | 'admin' | 'read_only'
export type ReviewAction = 'route_to_coding' | 'route_to_cdi' | 'route_to_charge_review' | 'route_to_compliance' | 'dismiss_with_reason'

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
  reason: string
  actor_id: string
  decided_at: string
  packet_record_hash: string
}

export interface ReviewWorkflowGateway {
  submit(packet: ReviewPacket, actor: ReviewerIdentity, findingId: string, action: ReviewAction, reason: string): Promise<ReviewDecision>
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

  async submit(packet: ReviewPacket, actor: ReviewerIdentity, findingId: string, action: ReviewAction, reason: string): Promise<ReviewDecision> {
    this.authorize(packet, actor, action)
    if (!packet.findings.some(finding => finding.finding_id === findingId)) throw new Error('Finding is not part of this packet')
    const normalizedReason = reason.trim()
    if (!normalizedReason || normalizedReason.length > 1000) throw new Error('A governed decision reason is required')
    const decidedAt = new Date().toISOString()
    const decision: ReviewDecision = {
      decision_id: `demo-decision-${decidedAt}-${action}`,
      packet_id: packet.packet_id,
      finding_id: findingId,
      action,
      reason: normalizedReason,
      actor_id: actor.actor_id,
      decided_at: decidedAt,
      packet_record_hash: packet.provenance.record_hash,
    }
    this.storage.setItem(this.key(packet), JSON.stringify([...await this.list(packet, actor), decision]))
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
          && item.packet_record_hash === packet.provenance.record_hash)
        : []
    } catch {
      return []
    }
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
    && typeof decision.actor_id === 'string'
    && typeof decision.decided_at === 'string'
    && typeof decision.packet_record_hash === 'string'
    && ['route_to_coding', 'route_to_cdi', 'route_to_charge_review', 'route_to_compliance', 'dismiss_with_reason'].includes(decision.action ?? '')
}
