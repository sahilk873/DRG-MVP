import { z } from 'zod'

const nonEmpty = z.string().min(1)

const evidenceSchema = z.object({
  evidence_id: nonEmpty,
  document_id: nonEmpty,
  author_role: nonEmpty,
  recorded_at: nonEmpty,
  text: nonEmpty,
  source_locator: z.object({
    adapter_id: nonEmpty,
    adapter_version: nonEmpty,
    resource: nonEmpty,
    path: nonEmpty,
    row_number: z.number().int().positive(),
    source_record_id: nonEmpty,
    field_names: z.array(nonEmpty).min(1),
    sheet: nonEmpty.optional(),
  }).strict().optional(),
}).strict()

const findingSchema = z.object({
  finding_id: nonEmpty,
  rule_id: nonEmpty,
  rule_package_id: nonEmpty,
  rule_package_version: nonEmpty,
  title: nonEmpty,
  disposition: z.enum(['coding_review', 'cdi_query', 'charge_review', 'compliance_review', 'insufficient_evidence', 'no_opportunity']),
  confidence: z.number().min(0).max(1),
  proposed_change: z.record(z.string(), z.unknown()),
  subject_ids: z.array(nonEmpty),
  assertion_ids: z.array(nonEmpty),
  evidence_ids: z.array(nonEmpty),
  contradicting_evidence_ids: z.array(nonEmpty),
  rationale: nonEmpty,
  requires_human_review: z.boolean(),
  submitted_drg: nonEmpty.nullable(),
  current_drg: nonEmpty,
  simulated_drg: nonEmpty,
  estimated_impact_cents: z.number().int().nullable(),
  impact_status: z.enum(['estimated', 'not_applicable', 'unavailable']),
  grouper_version: nonEmpty,
}).strict()

export const reviewPacketSchema = z.object({
  review_packet_schema_version: z.literal('3.0.0'),
  packet_id: z.string().regex(/^packet-[0-9a-f]{20}$/),
  environment: z.enum(['development', 'synthetic', 'validation', 'production']),
  tenant: z.object({
    tenant_id: z.string().min(1).max(128).regex(/^[A-Za-z0-9._-]+$/),
    workspace_id: z.string().min(1).max(128).regex(/^[A-Za-z0-9._-]+$/),
  }).strict(),
  case: z.object({
    schema_version: z.literal('2.0.0'),
    case_id: nonEmpty,
    patient_id: nonEmpty,
    encounter_id: nonEmpty,
    admitted_at: nonEmpty,
    discharged_at: nonEmpty,
    metadata: z.record(z.string(), z.unknown()),
    claim: z.object({
      diagnoses: z.array(nonEmpty),
      procedures: z.array(nonEmpty),
      charges: z.array(nonEmpty),
      drg: nonEmpty.nullable().optional(),
      allowed_amount_cents: z.number().int().nonnegative().nullable().optional(),
    }).strict(),
  }).strict(),
  evidence: z.array(evidenceSchema),
  ontology: z.object({
    ontology_id: nonEmpty,
    ontology_version: nonEmpty,
    ontology_digest: z.string().regex(/^[0-9a-f]{64}$/),
    entities: z.array(z.object({
      entity_id: nonEmpty,
      entity_type: nonEmpty,
      label: nonEmpty,
      properties: z.record(z.string(), z.unknown()),
      concept: z.object({ system: nonEmpty, code: nonEmpty, display: nonEmpty }).strict().optional(),
    }).strict()),
    relations: z.array(z.object({
      relation_id: nonEmpty,
      predicate: nonEmpty,
      source_id: nonEmpty,
      target_id: nonEmpty,
      assertion_status: nonEmpty,
      documentation_status: nonEmpty,
      confidence: z.number().min(0).max(1),
      evidence_ids: z.array(nonEmpty),
      contradicting_evidence_ids: z.array(nonEmpty).optional(),
    }).strict()),
  }).strict(),
  assertions: z.array(z.object({
    assertion_id: nonEmpty,
    subject_id: nonEmpty,
    concept: nonEmpty,
    status: nonEmpty,
    documentation_status: nonEmpty,
    confidence: z.number().min(0).max(1),
    attributes: z.record(z.string(), z.unknown()),
    evidence_ids: z.array(nonEmpty),
    contradicting_evidence_ids: z.array(nonEmpty).optional(),
  }).strict()),
  findings: z.array(findingSchema),
  controls: z.object({
    claim_mutation_allowed: z.literal(false),
    human_review_required: z.boolean(),
    permitted_actions: z.array(z.enum(['route_to_coding', 'route_to_cdi', 'route_to_charge_review', 'route_to_compliance', 'dismiss_with_reason'])),
  }).strict(),
  provenance: z.object({
    evaluated_at: nonEmpty,
    engine_version: nonEmpty,
    case_hash: z.string().regex(/^[0-9a-f]{64}$/),
    rule_package_id: nonEmpty,
    rule_package_version: nonEmpty,
    rule_package_hash: z.string().regex(/^[0-9a-f]{64}$/),
    record_hash: z.string().regex(/^[0-9a-f]{64}$/),
    packet_hash: z.string().regex(/^[0-9a-f]{64}$/),
    previous_record_hash: nonEmpty.nullable(),
    grouper_versions: z.array(nonEmpty),
  }).strict(),
}).strict().superRefine((packet, context) => {
  const evidenceIds = new Set(packet.evidence.map(item => item.evidence_id))
  const assertionIds = new Set(packet.assertions.map(item => item.assertion_id))
  const subjectIds = new Set(packet.ontology.entities.map(item => item.entity_id))
  const findingIds = packet.findings.map(item => item.finding_id)
  if (new Set(findingIds).size !== findingIds.length) {
    context.addIssue({ code: 'custom', message: 'packet finding IDs must be unique' })
  }
  if (packet.findings.some(item => item.requires_human_review) && !packet.controls.human_review_required) {
    context.addIssue({ code: 'custom', message: 'packet controls must preserve finding-level human review' })
  }
  for (const finding of packet.findings) {
    if (
      (finding.impact_status === 'estimated' && finding.estimated_impact_cents == null)
      || (finding.impact_status !== 'estimated' && finding.estimated_impact_cents != null)
    ) {
      context.addIssue({ code: 'custom', message: 'finding impact status and estimate are inconsistent' })
    }
    if (finding.proposed_change && Object.keys(finding.proposed_change).length > 0 && !finding.requires_human_review) {
      context.addIssue({ code: 'custom', message: 'claim-affecting findings must require human review' })
    }
    for (const evidenceId of [...finding.evidence_ids, ...finding.contradicting_evidence_ids]) {
      if (!evidenceIds.has(evidenceId)) context.addIssue({ code: 'custom', message: `finding references unknown evidence ${evidenceId}` })
    }
    for (const assertionId of finding.assertion_ids) {
      if (!assertionIds.has(assertionId)) context.addIssue({ code: 'custom', message: `finding references unknown assertion ${assertionId}` })
    }
    for (const subjectId of finding.subject_ids) {
      if (!subjectIds.has(subjectId)) context.addIssue({ code: 'custom', message: `finding references unknown ontology subject ${subjectId}` })
    }
  }
})

export type ReviewPacket = z.infer<typeof reviewPacketSchema>

export function parseReviewPacket(value: unknown): ReviewPacket {
  return reviewPacketSchema.parse(value)
}
