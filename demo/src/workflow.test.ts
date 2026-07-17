import { beforeEach, describe, expect, it } from 'vitest'

import { primaryAutomationPlan, primaryReviewPacket } from './data'
import { BrowserDemoWorkflowGateway, type ReviewerIdentity } from './workflow'

describe('review workflow gateway', () => {
  beforeEach(() => window.localStorage.clear())

  const reviewer: ReviewerIdentity = {
    actor_id: 'coder-1',
    tenant_id: primaryReviewPacket.tenant.tenant_id,
    workspace_id: primaryReviewPacket.tenant.workspace_id,
    roles: ['coder'],
  }

  it('persists a scoped decision tied to the packet record hash', async () => {
    const gateway = new BrowserDemoWorkflowGateway(window.localStorage)
    const findingId = primaryReviewPacket.findings[0]!.finding_id
    const decision = await gateway.submit(primaryReviewPacket, primaryAutomationPlan, reviewer, findingId, 'route_to_coding', 'evidence_confirmed', 'Coder validation required', 'test-submit')
    expect(decision.packet_record_hash).toBe(primaryReviewPacket.provenance.record_hash)
    expect(decision.packet_hash).toBe(primaryReviewPacket.provenance.packet_hash)
    await expect(gateway.list(primaryReviewPacket, reviewer)).resolves.toEqual([decision])
  })

  it('denies cross-tenant and read-only reviewers', async () => {
    const gateway = new BrowserDemoWorkflowGateway(window.localStorage)
    const findingId = primaryReviewPacket.findings[0]!.finding_id
    await expect(gateway.list(primaryReviewPacket, { ...reviewer, tenant_id: 'other' })).rejects.toThrow(/tenant scope/i)
    await expect(gateway.submit(primaryReviewPacket, primaryAutomationPlan, { ...reviewer, roles: ['read_only'] }, findingId, 'route_to_coding', 'evidence_confirmed', 'review', 'reader')).rejects.toThrow(/role/i)
  })

  it('is idempotent and rejects a conflicting terminal decision', async () => {
    const gateway = new BrowserDemoWorkflowGateway(window.localStorage)
    const findingId = primaryReviewPacket.findings[0]!.finding_id
    const first = await gateway.submit(primaryReviewPacket, primaryAutomationPlan, reviewer, findingId, 'route_to_coding', 'evidence_confirmed', 'review', 'stable-key')
    const retry = await gateway.submit(primaryReviewPacket, primaryAutomationPlan, reviewer, findingId, 'route_to_coding', 'evidence_confirmed', 'review', 'stable-key')
    expect(retry).toEqual(first)
    await expect(gateway.submit(primaryReviewPacket, primaryAutomationPlan, reviewer, findingId, 'dismiss_with_reason', 'duplicate', 'duplicate', 'new-key')).rejects.toThrow(/terminal decision/i)
  })
})
