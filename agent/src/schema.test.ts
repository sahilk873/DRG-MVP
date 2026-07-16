import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { resolveModelId, validateGrounding } from './agents/encounter-extractor.ts'
import {
  DEFAULT_ONTOLOGY_DEFINITION,
  mergeWithStructuralGraph,
  validateOntologyGraph,
} from './ontology.ts'
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
  ontology: {
    ontology_id: 'wound-care-encounter-ontology',
    ontology_version: '1.0.0-draft',
    entities: [{
      entity_id: 'wound:1',
      entity_type: 'PressureInjury',
      label: 'Sacral pressure injury',
      properties: {},
    }],
    relations: [{
      relation_id: 'rel:patient-wound',
      predicate: 'hasWound',
      source_id: 'root:patient',
      target_id: 'wound:1',
      assertion_status: 'present' as const,
      documentation_status: 'explicit' as const,
      confidence: 0.98,
      evidence_ids: ['EV-001'],
      contradicting_evidence_ids: [],
    }],
  },
  assertions: [{
    assertion_id: 'AS-001',
    subject_id: 'wound:1',
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
  const graph = mergeWithStructuralGraph(DEFAULT_ONTOLOGY_DEFINITION, parsed.ontology)
  assert.doesNotThrow(() => validateOntologyGraph(DEFAULT_ONTOLOGY_DEFINITION, graph, new Set(['EV-001'])))
})

test('shared encounter fixture satisfies the TypeScript contract', () => {
  const encounter = encounterCaseSchema.parse(encounterFixture)
  assert.doesNotThrow(() => validateOntologyGraph(
    DEFAULT_ONTOLOGY_DEFINITION,
    encounter.ontology,
    new Set(encounter.evidence.map(item => item.evidence_id)),
  ))
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

test('ontology domain and range violations fail closed', () => {
  const parsed = agentExtractionSchema.parse({
    ...extraction,
    ontology: {
      ...extraction.ontology,
      relations: [{
        ...extraction.ontology.relations[0],
        predicate: 'hasStage',
      }],
    },
  })
  const graph = mergeWithStructuralGraph(DEFAULT_ONTOLOGY_DEFINITION, parsed.ontology)
  assert.throws(
    () => validateOntologyGraph(DEFAULT_ONTOLOGY_DEFINITION, graph, new Set(['EV-001'])),
    /invalid source type/,
  )
})
