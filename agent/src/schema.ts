import { z } from 'zod'

export const SCHEMA_VERSION = '2.0.0'

const nonEmptyString = z.string().trim().min(1)
const isoDateTime = z.iso.datetime({ offset: true })
const uniqueNonEmptyStringArray = z.array(nonEmptyString).min(1).check(
  z.refine(items => new Set(items).size === items.length, 'must not contain duplicates'),
)

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

export const sourceLocatorSchema = z.object({
  adapter_id: nonEmptyString,
  adapter_version: nonEmptyString,
  resource: nonEmptyString,
  path: nonEmptyString,
  sheet: nonEmptyString.optional(),
  row_number: z.number().int().positive(),
  source_record_id: nonEmptyString,
  field_names: uniqueNonEmptyStringArray,
}).strict().check(z.refine(locator => (
  !locator.path.startsWith('/')
  && !locator.path.startsWith('~')
  && !locator.path.includes('\\')
  && !locator.path.split('/').includes('..')
), { message: 'path must be safe and relative', path: ['path'] }))

export const evidenceSchema = z.object({
  evidence_id: nonEmptyString,
  document_id: nonEmptyString,
  author_role: nonEmptyString,
  recorded_at: isoDateTime,
  text: nonEmptyString,
  source_locator: sourceLocatorSchema.optional(),
}).strict()

export const conceptCodeSchema = z.object({
  system: nonEmptyString,
  code: nonEmptyString,
  display: nonEmptyString,
}).strict()

export const ontologyEntitySchema = z.object({
  entity_id: nonEmptyString,
  entity_type: nonEmptyString,
  label: nonEmptyString,
  concept: conceptCodeSchema.optional(),
  properties: z.record(z.string(), z.unknown()),
}).strict()

export const ontologyRelationSchema = z.object({
  relation_id: nonEmptyString,
  predicate: nonEmptyString,
  source_id: nonEmptyString,
  target_id: nonEmptyString,
  assertion_status: z.enum(['present', 'absent', 'uncertain', 'historical']),
  documentation_status: z.enum(['explicit', 'inferred', 'conflicted', 'absent']),
  confidence: z.number().min(0).max(1),
  evidence_ids: z.array(nonEmptyString),
  contradicting_evidence_ids: z.array(nonEmptyString).default([]),
}).strict()

export const ontologyGraphSchema = z.object({
  ontology_id: nonEmptyString,
  ontology_version: nonEmptyString,
  ontology_digest: z.string().regex(/^[0-9a-f]{64}$/),
  entities: z.array(ontologyEntitySchema),
  relations: z.array(ontologyRelationSchema),
}).strict()

export const structuralGraphSchema = z.object({
  entities: z.array(ontologyEntitySchema),
  relations: z.array(ontologyRelationSchema),
}).strict()

export const assertionSchema = z.object({
  assertion_id: nonEmptyString,
  subject_id: nonEmptyString,
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
  ontology: ontologyGraphSchema,
  assertions: z.array(assertionSchema),
}).strict()

export const ingestionProvenanceSchema = z.object({
  framework: z.literal('deterministic-adapter'),
  adapter_id: nonEmptyString,
  adapter_version: nonEmptyString,
  source_schema_fingerprint: z.string().regex(/^[0-9a-f]{64}$/),
  input_manifest_digest: z.string().regex(/^[0-9a-f]{64}$/),
  transformed_at: isoDateTime,
  runtime_version: nonEmptyString,
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
  structured_extraction: agentExtractionOutputSchema.optional(),
  ingestion_provenance: ingestionProvenanceSchema.optional(),
}).strict().check(z.refine(bundle => new Date(bundle.admitted_at) <= new Date(bundle.discharged_at), {
  message: 'admitted_at must not be after discharged_at',
  path: ['admitted_at'],
})).check(z.refine(bundle => new Set(bundle.documents.map(document => document.document_id)).size === bundle.documents.length, {
  message: 'document_id values must be unique',
  path: ['documents'],
}))

export function createAgentExtractionSchema(allowedExternalEntityIds: Iterable<string> = []) {
  const allowed = new Set(allowedExternalEntityIds)
  return agentExtractionOutputSchema.superRefine((value, context) => {
    validateLineage(value, context, allowed)
  })
}

export const agentExtractionSchema = createAgentExtractionSchema()

export const extractionPolicyRecordSchema = z.object({
  max_documents: z.number().int().positive(),
  max_document_characters: z.number().int().positive(),
  max_total_document_characters: z.number().int().positive(),
  max_evidence_items: z.number().int().positive(),
  max_evidence_characters: z.number().int().positive(),
  max_total_evidence_characters: z.number().int().positive(),
  max_entities: z.number().int().positive(),
  max_relations: z.number().int().positive(),
  max_assertions: z.number().int().positive(),
}).strict()

export const provenanceSchema = z.object({
  framework: z.literal('mastra'),
  model_id: nonEmptyString,
  agent_id: nonEmptyString,
  extracted_at: isoDateTime,
  schema_version: z.literal(SCHEMA_VERSION),
  extraction_policy: extractionPolicyRecordSchema,
  ingestion: ingestionProvenanceSchema.optional(),
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
  ontology: ontologyGraphSchema,
  assertions: z.array(assertionSchema),
  claim: claimSchema,
  provenance: provenanceSchema,
}).strict().superRefine((value, context) => validateLineage(value, context))

const opportunityCategorySchema = z.enum([
  'missed_diagnosis',
  'missed_procedure',
  'missed_charge',
  'coding_specificity',
  'drg_discrepancy',
  'documentation_gap',
  'denial_risk',
  'payment_variance',
  'unsupported_billing',
])

const confidenceDimensionsSchema = z.object({
  evidence: z.number().min(0).max(1),
  semantic: z.number().min(0).max(1),
  financial: z.number().min(0).max(1),
}).strict()

export const investigationPacketSchema = z.object({
  packet_id: nonEmptyString,
  encounter: encounterCaseSchema,
  financial: z.record(z.string(), z.unknown()).default({}),
  payer_context: z.record(z.string(), z.unknown()).default({}),
  policy_context: z.record(z.string(), z.unknown()).default({}),
  data_quality: z.record(z.string(), z.unknown()).default({}),
  allowed_data_views: uniqueNonEmptyStringArray,
}).strict().superRefine((value, context) => {
  if (!value.allowed_data_views.includes('clinical')) {
    context.addIssue({ code: 'custom', path: ['allowed_data_views'], message: 'clinical access is required' })
  }
})

export const opportunityHypothesisSchema = z.object({
  hypothesis_id: nonEmptyString,
  category: opportunityCategorySchema,
  encounter_id: nonEmptyString,
  hypothesis: nonEmptyString,
  evidence_ids: z.array(nonEmptyString),
  contradicting_evidence_ids: z.array(nonEmptyString).default([]),
  assertion_ids: z.array(nonEmptyString).default([]),
  claim_line_ids: z.array(nonEmptyString).default([]),
  missing_information: z.array(nonEmptyString).default([]),
  candidate_codes: z.array(nonEmptyString).default([]),
  candidate_drgs: z.array(nonEmptyString).default([]),
  required_validations: z.array(nonEmptyString).default([]),
  recommended_action: z.string().default(''),
  confidence: confidenceDimensionsSchema,
  materiality_cents: z.number().int().nonnegative().nullable().default(null),
}).strict()

export const opportunityCritiqueSchema = z.object({
  hypothesis_id: nonEmptyString,
  supported: z.boolean(),
  counterevidence_ids: z.array(nonEmptyString).default([]),
  rationale: nonEmptyString,
  missing_information: z.array(nonEmptyString).default([]),
}).strict()

export function createReconciliationOutputSchema(packet: z.infer<typeof investigationPacketSchema>) {
  const evidenceIds = new Set(packet.encounter.evidence.map(item => item.evidence_id))
  const assertionIds = new Set(packet.encounter.assertions.map(item => item.assertion_id))
  return z.object({ hypotheses: z.array(opportunityHypothesisSchema) }).strict().superRefine((value, context) => {
    const seen = new Set<string>()
    for (const [index, hypothesis] of value.hypotheses.entries()) {
      if (seen.has(hypothesis.hypothesis_id)) {
        context.addIssue({ code: 'custom', path: ['hypotheses', index, 'hypothesis_id'], message: 'hypothesis_id values must be unique' })
      }
      seen.add(hypothesis.hypothesis_id)
      if (hypothesis.encounter_id !== packet.encounter.encounter_id) {
        context.addIssue({ code: 'custom', path: ['hypotheses', index, 'encounter_id'], message: 'must match packet encounter_id' })
      }
      if (!hypothesis.evidence_ids.length && hypothesis.category !== 'payment_variance') {
        context.addIssue({ code: 'custom', path: ['hypotheses', index, 'evidence_ids'], message: 'supporting evidence is required' })
      }
      for (const evidenceId of hypothesis.evidence_ids) {
        if (!evidenceIds.has(evidenceId)) context.addIssue({ code: 'custom', path: ['hypotheses', index, 'evidence_ids'], message: `unknown evidence: ${evidenceId}` })
      }
      for (const evidenceId of hypothesis.contradicting_evidence_ids) {
        if (!evidenceIds.has(evidenceId)) context.addIssue({ code: 'custom', path: ['hypotheses', index, 'contradicting_evidence_ids'], message: `unknown evidence: ${evidenceId}` })
        if (hypothesis.evidence_ids.includes(evidenceId)) context.addIssue({ code: 'custom', path: ['hypotheses', index], message: `evidence cannot both support and contradict: ${evidenceId}` })
      }
      for (const assertionId of hypothesis.assertion_ids) {
        if (!assertionIds.has(assertionId)) context.addIssue({ code: 'custom', path: ['hypotheses', index, 'assertion_ids'], message: `unknown assertion: ${assertionId}` })
      }
    }
  })
}

export function createCritiqueOutputSchema(
  packet: z.infer<typeof investigationPacketSchema>,
  hypotheses: readonly z.infer<typeof opportunityHypothesisSchema>[],
) {
  const hypothesisIds = new Set(hypotheses.map(item => item.hypothesis_id))
  const evidenceIds = new Set(packet.encounter.evidence.map(item => item.evidence_id))
  return z.object({ critiques: z.array(opportunityCritiqueSchema) }).strict().superRefine((value, context) => {
    const seen = new Set<string>()
    for (const [index, critique] of value.critiques.entries()) {
      if (!hypothesisIds.has(critique.hypothesis_id)) context.addIssue({ code: 'custom', path: ['critiques', index, 'hypothesis_id'], message: 'unknown hypothesis_id' })
      if (seen.has(critique.hypothesis_id)) context.addIssue({ code: 'custom', path: ['critiques', index, 'hypothesis_id'], message: 'duplicate critique' })
      seen.add(critique.hypothesis_id)
      for (const evidenceId of critique.counterevidence_ids) {
        if (!evidenceIds.has(evidenceId)) context.addIssue({ code: 'custom', path: ['critiques', index, 'counterevidence_ids'], message: `unknown evidence: ${evidenceId}` })
      }
    }
  })
}

export const ontologyDefinitionSchema = z.object({
  ontology_id: nonEmptyString,
  version: nonEmptyString,
  status: z.enum(['draft', 'clinical-review-required', 'approved']),
  purpose: nonEmptyString.optional(),
  sources: z.array(z.object({
    source_id: nonEmptyString,
    title: nonEmptyString,
    contribution: nonEmptyString,
  }).strict()).optional(),
  structural_graph: structuralGraphSchema,
  classes: z.array(z.object({
    class_id: nonEmptyString,
    label: nonEmptyString,
    parent: nonEmptyString.optional(),
    abstract: z.boolean().optional(),
    value_set: nonEmptyString.optional(),
  }).strict()).min(1),
  relations: z.array(z.object({
    relation_id: nonEmptyString,
    domain: uniqueNonEmptyStringArray,
    range: uniqueNonEmptyStringArray,
    requires_evidence: z.boolean(),
  }).strict()),
  value_sets: z.record(z.string(), uniqueNonEmptyStringArray).optional(),
}).strict()

export type SourceBundle = z.infer<typeof sourceBundleSchema>
export type AgentExtraction = z.infer<typeof agentExtractionSchema>
export type EncounterCase = z.infer<typeof encounterCaseSchema>
export type OntologyDefinition = z.infer<typeof ontologyDefinitionSchema>
export type OntologyGraph = z.infer<typeof ontologyGraphSchema>
export type InvestigationPacket = z.infer<typeof investigationPacketSchema>
export type OpportunityHypothesis = z.infer<typeof opportunityHypothesisSchema>
export type OpportunityCritique = z.infer<typeof opportunityCritiqueSchema>

function validateLineage(
  value: {
    evidence: z.infer<typeof evidenceSchema>[]
    ontology: z.infer<typeof ontologyGraphSchema>
    assertions: z.infer<typeof assertionSchema>[]
  },
  context: z.RefinementCtx,
  allowedExternalEntityIds: ReadonlySet<string> = new Set(),
): void {
  const evidenceIds = new Set(value.evidence.map(item => item.evidence_id))
  const assertionIds = new Set<string>()
  const entityIds = new Set<string>()
  const relationIds = new Set<string>()

  if (evidenceIds.size !== value.evidence.length) {
    context.addIssue({ code: 'custom', path: ['evidence'], message: 'evidence_id values must be unique' })
  }
  for (const [index, entity] of value.ontology.entities.entries()) {
    if (entityIds.has(entity.entity_id)) {
      context.addIssue({ code: 'custom', path: ['ontology', 'entities', index, 'entity_id'], message: 'entity_id values must be unique' })
    }
    entityIds.add(entity.entity_id)
  }
  for (const [index, relation] of value.ontology.relations.entries()) {
    if (relationIds.has(relation.relation_id)) {
      context.addIssue({ code: 'custom', path: ['ontology', 'relations', index, 'relation_id'], message: 'relation_id values must be unique' })
    }
    relationIds.add(relation.relation_id)
    if (new Set(relation.evidence_ids).size !== relation.evidence_ids.length) {
      context.addIssue({ code: 'custom', path: ['ontology', 'relations', index, 'evidence_ids'], message: 'must not contain duplicates' })
    }
    if (new Set(relation.contradicting_evidence_ids).size !== relation.contradicting_evidence_ids.length) {
      context.addIssue({ code: 'custom', path: ['ontology', 'relations', index, 'contradicting_evidence_ids'], message: 'must not contain duplicates' })
    }
    for (const [field, entityId] of [['source_id', relation.source_id], ['target_id', relation.target_id]] as const) {
      if (!entityIds.has(entityId) && !allowedExternalEntityIds.has(entityId)) {
        context.addIssue({ code: 'custom', path: ['ontology', 'relations', index, field], message: `unknown entity reference: ${entityId}` })
      }
    }
    for (const evidenceId of relation.evidence_ids) {
      if (!evidenceIds.has(evidenceId)) {
        context.addIssue({ code: 'custom', path: ['ontology', 'relations', index, 'evidence_ids'], message: `unknown evidence reference: ${evidenceId}` })
      }
    }
    const supporting = new Set(relation.evidence_ids)
    for (const evidenceId of relation.contradicting_evidence_ids) {
      if (!evidenceIds.has(evidenceId)) {
        context.addIssue({ code: 'custom', path: ['ontology', 'relations', index, 'contradicting_evidence_ids'], message: `unknown evidence reference: ${evidenceId}` })
      }
      if (supporting.has(evidenceId)) {
        context.addIssue({ code: 'custom', path: ['ontology', 'relations', index], message: `evidence cannot both support and contradict a relation: ${evidenceId}` })
      }
    }
  }
  for (const [index, assertion] of value.assertions.entries()) {
    if (assertionIds.has(assertion.assertion_id)) {
      context.addIssue({ code: 'custom', path: ['assertions', index, 'assertion_id'], message: 'assertion_id values must be unique' })
    }
    assertionIds.add(assertion.assertion_id)
    if (new Set(assertion.evidence_ids).size !== assertion.evidence_ids.length) {
      context.addIssue({ code: 'custom', path: ['assertions', index, 'evidence_ids'], message: 'must not contain duplicates' })
    }
    if (new Set(assertion.contradicting_evidence_ids).size !== assertion.contradicting_evidence_ids.length) {
      context.addIssue({ code: 'custom', path: ['assertions', index, 'contradicting_evidence_ids'], message: 'must not contain duplicates' })
    }
    if (!entityIds.has(assertion.subject_id) && !allowedExternalEntityIds.has(assertion.subject_id)) {
      context.addIssue({ code: 'custom', path: ['assertions', index, 'subject_id'], message: `unknown ontology subject: ${assertion.subject_id}` })
    }
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
