import { z } from 'zod'

const nonEmptyString = z.string().trim().min(1)
const digest = z.string().regex(/^[0-9a-f]{64}$/)
const expressionOperationSchema = z.object({
  op: z.enum(['trim', 'lower', 'upper', 'integer', 'number', 'boolean', 'datetime', 'split', 'map']),
  delimiter: nonEmptyString.optional(),
  format: nonEmptyString.optional(),
  timezone: nonEmptyString.optional(),
  values: z.record(z.string(), z.unknown()).optional(),
}).strict().superRefine((value, context) => {
  if (value.op === 'split' && value.delimiter === undefined) {
    context.addIssue({ code: 'custom', path: ['delimiter'], message: 'split requires delimiter' })
  }
  if (value.op === 'map' && (!value.values || Object.keys(value.values).length === 0)) {
    context.addIssue({ code: 'custom', path: ['values'], message: 'map requires non-empty values' })
  }
  if (value.op !== 'datetime' && (value.format !== undefined || value.timezone !== undefined)) {
    context.addIssue({ code: 'custom', message: 'only datetime accepts format or timezone' })
  }
})

export const adapterExpressionSchema = z.object({
  field: nonEmptyString.optional(),
  constant: z.unknown().optional(),
  template: nonEmptyString.optional(),
  operations: z.array(expressionOperationSchema).optional(),
}).strict().superRefine((value, context) => {
  const modes = Number(value.field !== undefined) + Number(Object.hasOwn(value, 'constant')) + Number(value.template !== undefined)
  if (modes !== 1) context.addIssue({ code: 'custom', message: 'exactly one of field, constant or template is required' })
  if (value.template) {
    for (const match of value.template.matchAll(/{([^{}]+)}/g)) {
      if (!/^[A-Za-z_][A-Za-z0-9_ -]*$/.test(match[1] ?? '')) {
        context.addIssue({ code: 'custom', path: ['template'], message: 'template placeholders must be simple field names' })
      }
    }
  }
})

const rowConditionSchema = z.object({
  field: nonEmptyString,
  op: z.enum(['eq', 'not_eq', 'in', 'not_in', 'present', 'not_present']),
  value: z.unknown().optional(),
}).strict().superRefine((condition, context) => {
  const hasValue = Object.hasOwn(condition, 'value')
  if (['present', 'not_present'].includes(condition.op) && hasValue) {
    context.addIssue({ code: 'custom', path: ['value'], message: `${condition.op} does not accept value` })
  }
  if (!['present', 'not_present'].includes(condition.op) && !hasValue) {
    context.addIssue({ code: 'custom', path: ['value'], message: `${condition.op} requires value` })
  }
  if (['in', 'not_in'].includes(condition.op) && (!Array.isArray(condition.value) || condition.value.length === 0)) {
    context.addIssue({ code: 'custom', path: ['value'], message: `${condition.op} requires a non-empty array` })
  }
})

const resourceSchema = z.object({
  path: nonEmptyString,
  format: z.enum(['csv', 'json', 'jsonl', 'xlsx']),
  sheet: nonEmptyString.optional(),
}).strict().superRefine((value, context) => {
  if (value.format === 'xlsx' && value.sheet === undefined) {
    context.addIssue({ code: 'custom', path: ['sheet'], message: 'xlsx requires sheet' })
  }
  if (value.path.startsWith('/') || value.path.split('/').includes('..') || value.path.startsWith('~') || value.path.includes('\\')) {
    context.addIssue({ code: 'custom', path: ['path'], message: 'path must be safe and relative' })
  }
})

const collectionBindingSchema = z.object({
  resource: nonEmptyString,
  encounter_id: adapterExpressionSchema,
  value: adapterExpressionSchema,
  where: z.array(rowConditionSchema).optional(),
}).strict()

const encounterBindingSchema = z.object({
  resource: nonEmptyString,
  case_id: adapterExpressionSchema,
  patient_id: adapterExpressionSchema,
  encounter_id: adapterExpressionSchema,
  admitted_at: adapterExpressionSchema,
  discharged_at: adapterExpressionSchema,
  metadata: z.record(z.string(), adapterExpressionSchema).optional(),
  where: z.array(rowConditionSchema).optional(),
}).strict()

const documentBindingSchema = z.object({
  resource: nonEmptyString,
  encounter_id: adapterExpressionSchema,
  document_id: adapterExpressionSchema,
  author_role: adapterExpressionSchema,
  recorded_at: adapterExpressionSchema,
  text: adapterExpressionSchema,
  where: z.array(rowConditionSchema).optional(),
}).strict()

const claimBindingSchema = z.object({
  resource: nonEmptyString,
  encounter_id: adapterExpressionSchema,
  drg: adapterExpressionSchema.optional(),
  allowed_amount_cents: adapterExpressionSchema.optional(),
  diagnoses: collectionBindingSchema.optional(),
  procedures: collectionBindingSchema.optional(),
  charges: collectionBindingSchema.optional(),
  where: z.array(rowConditionSchema).optional(),
}).strict()

const evidenceProjectionSchema = z.object({
  evidence_id: adapterExpressionSchema,
  document_id: adapterExpressionSchema,
  author_role: adapterExpressionSchema,
  recorded_at: adapterExpressionSchema,
  text: adapterExpressionSchema,
  field_names: z.array(nonEmptyString).min(1),
}).strict()

const entityProjectionSchema = z.object({
  entity_id: adapterExpressionSchema,
  entity_type: nonEmptyString,
  label: adapterExpressionSchema,
  properties: z.record(z.string(), adapterExpressionSchema),
}).strict()

const relationProjectionSchema = z.object({
  relation_id: adapterExpressionSchema,
  predicate: nonEmptyString,
  source_id: adapterExpressionSchema,
  target_id: adapterExpressionSchema,
  assertion_status: z.enum(['present', 'absent', 'uncertain', 'historical']),
  documentation_status: z.enum(['explicit', 'inferred', 'conflicted', 'absent']),
  confidence: z.number().min(0).max(1),
  cite_evidence: z.boolean(),
}).strict()

const assertionProjectionSchema = z.object({
  assertion_id: adapterExpressionSchema,
  subject_id: adapterExpressionSchema,
  concept: nonEmptyString,
  status: z.enum(['present', 'absent', 'uncertain', 'historical']),
  documentation_status: z.enum(['explicit', 'inferred', 'conflicted', 'absent']),
  confidence: z.number().min(0).max(1),
  attributes: z.record(z.string(), adapterExpressionSchema),
}).strict()

const structuredProjectionSchema = z.object({
  projection_id: nonEmptyString,
  resource: nonEmptyString,
  encounter_id: adapterExpressionSchema,
  source_record_id: adapterExpressionSchema,
  evidence: evidenceProjectionSchema,
  entities: z.array(entityProjectionSchema),
  relations: z.array(relationProjectionSchema),
  assertions: z.array(assertionProjectionSchema),
  where: z.array(rowConditionSchema).optional(),
}).strict()

export const adapterDefinitionSchema = z.object({
  adapter_id: nonEmptyString,
  version: nonEmptyString,
  status: z.enum(['draft', 'approved-for-demo', 'approved']),
  source_schema_fingerprint: digest,
  ontology: z.object({
    ontology_id: nonEmptyString,
    version: nonEmptyString,
    digest,
  }).strict(),
  resources: z.record(z.string(), resourceSchema),
  encounter: encounterBindingSchema,
  documents: z.array(documentBindingSchema),
  claim: claimBindingSchema,
  structured_projections: z.array(structuredProjectionSchema),
}).strict().superRefine((value, context) => {
  const resources = new Set(Object.keys(value.resources))
  const references = [
    value.encounter.resource,
    value.claim.resource,
    ...value.documents.map(item => item.resource),
    ...value.structured_projections.map(item => item.resource),
    ...(['diagnoses', 'procedures', 'charges'] as const)
      .map(name => value.claim[name]?.resource)
      .filter((name): name is string => name !== undefined),
  ]
  for (const resource of references) {
    if (!resources.has(resource)) context.addIssue({ code: 'custom', path: ['resources'], message: `unknown resource reference: ${resource}` })
  }
  const projectionIds = value.structured_projections.map(item => item.projection_id)
  if (new Set(projectionIds).size !== projectionIds.length) {
    context.addIssue({ code: 'custom', path: ['structured_projections'], message: 'projection IDs must be unique' })
  }
})

export const bulkProfileSchema = z.object({
  profile_version: z.literal('1.0.0'),
  schema_fingerprint: digest,
  input_manifest_digest: digest,
  artifact_count: z.number().int().nonnegative(),
  total_bytes: z.number().int().nonnegative(),
  artifacts: z.array(z.object({
    artifact_id: nonEmptyString,
    path: nonEmptyString,
    format: nonEmptyString,
    sheet: nonEmptyString.optional(),
    size_bytes: z.number().int().nonnegative(),
    profiled_rows: z.number().int().nonnegative(),
    truncated: z.boolean(),
    columns: z.array(z.object({
      name: nonEmptyString,
      inferred_types: z.array(nonEmptyString).min(1),
      missing_count: z.number().int().nonnegative(),
      distinct_count: z.number().int().nonnegative(),
    }).strict()),
    sample_rows: z.array(z.record(z.string(), z.unknown())),
    error: nonEmptyString.optional(),
  }).strict()),
}).strict()

export type AdapterDefinition = z.infer<typeof adapterDefinitionSchema>
export type BulkProfile = z.infer<typeof bulkProfileSchema>
