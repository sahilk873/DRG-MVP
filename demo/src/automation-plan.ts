import { z } from 'zod'

const nonEmpty = z.string().min(1)
const digest = z.string().regex(/^[0-9a-f]{64}$/)
const action = z.enum(['route_to_coding', 'route_to_cdi', 'route_to_charge_review', 'route_to_compliance', 'dismiss_with_reason'])

export const automationPlanSchema = z.object({
  automation_schema_version: z.literal('1.0.0'),
  tenant: z.object({ tenant_id: nonEmpty, workspace_id: nonEmpty }).strict(),
  packet: z.object({
    packet_id: z.string().regex(/^packet-[0-9a-f]{20}$/),
    packet_hash: digest,
    case_id: nonEmpty,
    encounter_id: nonEmpty,
  }).strict(),
  policy: z.object({
    policy_id: nonEmpty,
    version: nonEmpty,
    quick_confirm_confidence: z.number().min(0).max(1),
    auto_route_confidence: z.number().min(0).max(1),
    auto_route_max_impact_cents: z.number().int().positive(),
    max_review_cases: z.number().int().positive(),
    max_review_seconds: z.number().int().positive(),
    digest,
  }).strict(),
  findings: z.array(z.object({
    automation_id: z.string().regex(/^automation-[0-9a-f]{20}$/),
    finding_id: nonEmpty,
    finding_hash: digest,
    semantic_fingerprint: digest,
    tier: z.enum(['suppressed', 'needs_enrichment', 'auto_routed', 'quick_confirm', 'focused_review', 'escalated']),
    queue: z.enum(['none', 'coding', 'cdi', 'charge', 'compliance']),
    reason_codes: z.array(nonEmpty),
    recommended_action: action.exclude(['dismiss_with_reason']).nullable(),
    allowed_actions: z.array(action),
    draft: z.record(z.string(), z.unknown()),
    priority_score: z.number().int().nonnegative(),
    estimated_review_seconds: z.number().int().nonnegative(),
    duplicate_of: nonEmpty.nullable(),
    related_finding_ids: z.array(nonEmpty),
  }).strict()),
  review_now_finding_ids: z.array(nonEmpty),
  deferred_finding_ids: z.array(nonEmpty),
  metrics: z.object({
    input_findings: z.number().int().nonnegative(),
    consolidated_findings: z.number().int().nonnegative(),
    review_now: z.number().int().nonnegative(),
    deferred: z.number().int().nonnegative(),
    estimated_review_seconds: z.number().int().nonnegative(),
    suppressed: z.number().int().nonnegative(),
    needs_enrichment: z.number().int().nonnegative(),
    auto_routed: z.number().int().nonnegative(),
    quick_confirm: z.number().int().nonnegative(),
    focused_review: z.number().int().nonnegative(),
    escalated: z.number().int().nonnegative(),
  }).strict(),
  plan_hash: digest,
}).strict().superRefine((plan, context) => {
  const allFindingIds = plan.findings.map(item => item.finding_id)
  const findingIds = new Set(allFindingIds)
  if (findingIds.size !== allFindingIds.length) {
    context.addIssue({ code: 'custom', message: 'automation finding IDs must be unique' })
  }
  for (const [name, ids] of [
    ['review_now_finding_ids', plan.review_now_finding_ids],
    ['deferred_finding_ids', plan.deferred_finding_ids],
  ] as const) {
    if (new Set(ids).size !== ids.length) context.addIssue({ code: 'custom', message: `${name} must be unique` })
  }
  const overlap = plan.review_now_finding_ids.find(id => plan.deferred_finding_ids.includes(id))
  if (overlap) context.addIssue({ code: 'custom', message: `finding ${overlap} cannot be both current and deferred` })
  for (const id of [...plan.review_now_finding_ids, ...plan.deferred_finding_ids]) {
    if (!findingIds.has(id)) context.addIssue({ code: 'custom', message: `automation queue references unknown finding ${id}` })
  }
  for (const item of plan.findings) {
    const humanEligible = ['quick_confirm', 'focused_review', 'escalated'].includes(item.tier)
    if (!humanEligible && item.allowed_actions.length) {
      context.addIssue({ code: 'custom', message: `non-human tier ${item.tier} cannot expose reviewer actions` })
    }
    if (humanEligible && (!item.recommended_action || !item.allowed_actions.includes(item.recommended_action))) {
      context.addIssue({ code: 'custom', message: `human tier ${item.tier} requires its recommended action` })
    }
    if (item.tier === 'auto_routed' && (!item.recommended_action || item.queue === 'none')) {
      context.addIssue({ code: 'custom', message: 'auto-routed findings require a queue and action' })
    }
    if (item.duplicate_of && !findingIds.has(item.duplicate_of)) {
      context.addIssue({ code: 'custom', message: `duplicate references unknown primary ${item.duplicate_of}` })
    }
  }
})

export type AutomationPlan = z.infer<typeof automationPlanSchema>
export type FindingAutomation = AutomationPlan['findings'][number]

export function parseAutomationPlan(value: unknown): AutomationPlan {
  return automationPlanSchema.parse(value)
}
