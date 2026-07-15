import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { resolveModelId, validateGrounding } from './agents/encounter-extractor.ts'
import { agentExtractionSchema, encounterCaseSchema, sourceBundleSchema } from './schema.ts'

const fixture = JSON.parse(await readFile('../examples/source_bundle_pressure_injury.json', 'utf8'))
const encounterFixture = JSON.parse(await readFile('../examples/case_pressure_injury.json', 'utf8'))

const extraction = {
  evidence: [{
    evidence_id: 'EV-001',
    document_id: 'WOUND-CONSULT-001',
    author_role: 'physician',
    recorded_at: '2026-06-01T14:30:00Z',
    text: 'Stage 4 pressure injury of the sacral region with exposed muscle.',
  }],
  assertions: [{
    assertion_id: 'AS-001',
    concept: 'pressure_injury',
    status: 'present' as const,
    documentation_status: 'explicit' as const,
    confidence: 0.98,
    attributes: { site: 'sacral_region', stage: 4 },
    evidence_ids: ['EV-001'],
    contradicting_evidence_ids: [],
  }],
}

test('source bundle and grounded extraction validate', () => {
  const source = sourceBundleSchema.parse(fixture)
  const parsed = agentExtractionSchema.parse(extraction)
  assert.doesNotThrow(() => validateGrounding(source, parsed))
})

test('shared encounter fixture satisfies the TypeScript contract', () => {
  assert.doesNotThrow(() => encounterCaseSchema.parse(encounterFixture))
})

test('fabricated evidence excerpt is rejected', () => {
  const source = sourceBundleSchema.parse(fixture)
  const parsed = agentExtractionSchema.parse({
    ...extraction,
    evidence: [{ ...extraction.evidence[0], text: 'Fabricated stage and site.' }],
  })
  assert.throws(() => validateGrounding(source, parsed), /not an exact source excerpt/)
})

test('source metadata changes are rejected', () => {
  const source = sourceBundleSchema.parse(fixture)
  const parsed = agentExtractionSchema.parse({
    ...extraction,
    evidence: [{ ...extraction.evidence[0], author_role: 'unknown' }],
  })
  assert.throws(() => validateGrounding(source, parsed), /does not preserve source metadata/)
})

test('model IDs require provider/model format', () => {
  assert.equal(resolveModelId({ MODEL_ID: 'anthropic/example-model' }), 'anthropic/example-model')
  assert.throws(() => resolveModelId({ MODEL_ID: 'invalid' }), /provider\/model/)
})
