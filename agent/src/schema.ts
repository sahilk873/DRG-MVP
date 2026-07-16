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
