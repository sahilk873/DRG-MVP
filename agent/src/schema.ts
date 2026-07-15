import { z } from 'zod'

export const SCHEMA_VERSION = '1.0.0'

const nonEmptyString = z.string().trim().min(1)
const isoDateTime = z.iso.datetime({ offset: true })

export const sourceDocumentSchema = z.object({
  document_id: nonEmptyString,
  author_role: nonEmptyString,
  recorded_at: isoDateTime,
  text: nonEmptyString,
}).strict()

export const claimSchema = z.object({
  diagnoses: z.array(nonEmptyString).check(z.refine(items => new Set(items).size === items.length, 'must not contain duplicates')),
  procedures: z.array(nonEmptyString).check(z.refine(items => new Set(items).size === items.length, 'must not contain duplicates')),
  charges: z.array(nonEmptyString).check(z.refine(items => new Set(items).size === items.length, 'must not contain duplicates')),
  drg: nonEmptyString.nullable().optional(),
  allowed_amount_cents: z.number().int().nonnegative().nullable().optional(),
}).strict()

export const sourceBundleSchema = z.object({
  case_id: nonEmptyString,
  patient_id: nonEmptyString,
  encounter_id: nonEmptyString,
  admitted_at: isoDateTime,
  discharged_at: isoDateTime,
  metadata: z.record(z.string(), z.unknown()).default({}),
  documents: z.array(sourceDocumentSchema),
  claim: claimSchema,
}).strict().check(z.refine(bundle => new Date(bundle.admitted_at) <= new Date(bundle.discharged_at), {
  message: 'admitted_at must not be after discharged_at',
  path: ['admitted_at'],
})).check(z.refine(bundle => new Set(bundle.documents.map(document => document.document_id)).size === bundle.documents.length, {
  message: 'document_id values must be unique',
  path: ['documents'],
}))

export const evidenceSchema = z.object({
  evidence_id: nonEmptyString,
  document_id: nonEmptyString,
  author_role: nonEmptyString,
  recorded_at: isoDateTime,
  text: nonEmptyString,
}).strict()

export const assertionSchema = z.object({
  assertion_id: nonEmptyString,
  concept: nonEmptyString,
  status: z.enum(['present', 'absent', 'uncertain', 'historical']),
  documentation_status: z.enum(['explicit', 'inferred', 'conflicted', 'absent']),
  confidence: z.number().min(0).max(1),
  attributes: z.record(z.string(), z.unknown()),
  evidence_ids: z.array(nonEmptyString).min(1),
  contradicting_evidence_ids: z.array(nonEmptyString).default([]),
}).strict()

export const agentExtractionOutputSchema = z.object({
  evidence: z.array(evidenceSchema),
  assertions: z.array(assertionSchema),
}).strict()

export const agentExtractionSchema = agentExtractionOutputSchema.superRefine(validateLineage)

export const provenanceSchema = z.object({
  framework: z.literal('mastra'),
  model_id: nonEmptyString,
  agent_id: nonEmptyString,
  extracted_at: isoDateTime,
  schema_version: z.literal(SCHEMA_VERSION),
}).strict()

export const encounterCaseSchema = z.object({
  schema_version: z.literal(SCHEMA_VERSION),
  case_id: nonEmptyString,
  patient_id: nonEmptyString,
  encounter_id: nonEmptyString,
  admitted_at: isoDateTime,
  discharged_at: isoDateTime,
  metadata: z.record(z.string(), z.unknown()).default({}),
  evidence: z.array(evidenceSchema),
  assertions: z.array(assertionSchema),
  claim: claimSchema,
  provenance: provenanceSchema,
}).strict().superRefine(validateLineage)

export type SourceBundle = z.infer<typeof sourceBundleSchema>
export type AgentExtraction = z.infer<typeof agentExtractionSchema>
export type EncounterCase = z.infer<typeof encounterCaseSchema>

function validateLineage(
  value: { evidence: z.infer<typeof evidenceSchema>[]; assertions: z.infer<typeof assertionSchema>[] },
  context: z.RefinementCtx,
): void {
  const evidenceIds = new Set(value.evidence.map(item => item.evidence_id))
  const assertionIds = new Set<string>()

  if (evidenceIds.size !== value.evidence.length) {
    context.addIssue({ code: 'custom', path: ['evidence'], message: 'evidence_id values must be unique' })
  }
  for (const [index, assertion] of value.assertions.entries()) {
    if (assertionIds.has(assertion.assertion_id)) {
      context.addIssue({ code: 'custom', path: ['assertions', index, 'assertion_id'], message: 'assertion_id values must be unique' })
    }
    assertionIds.add(assertion.assertion_id)
    const supporting = new Set(assertion.evidence_ids)
    for (const evidenceId of [...assertion.evidence_ids, ...assertion.contradicting_evidence_ids]) {
      if (!evidenceIds.has(evidenceId)) {
        context.addIssue({ code: 'custom', path: ['assertions', index, 'evidence_ids'], message: `unknown evidence reference: ${evidenceId}` })
      }
    }
    for (const evidenceId of assertion.contradicting_evidence_ids) {
      if (supporting.has(evidenceId)) {
        context.addIssue({ code: 'custom', path: ['assertions', index], message: `evidence cannot both support and contradict an assertion: ${evidenceId}` })
      }
    }
  }
}

