import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { resolveModelId, validateGrounding } from './agents/encounter-extractor.ts'
import {
  DEFAULT_ONTOLOGY_DEFINITION,
  mergeWithStructuralGraph,
  ontologyDigest,
  validateOntologyGraph,
} from './ontology.ts'
import {
  createAgentExtractionSchema,
  encounterCaseSchema,
  ontologyDefinitionSchema,
  sourceBundleSchema,
} from './schema.ts'
import {
  resolveExtractionPolicy,
  validateExtractionLimits,
  validateRawSourceBundleLimits,
} from './policy.ts'

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
    ontology_digest: 'eb2b6f6aa447825fa45012fd23a91fe0f572fe280cf776a239699d6230390779',
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
  const parsed = createAgentExtractionSchema(
    DEFAULT_ONTOLOGY_DEFINITION.structural_graph.entities.map(entity => entity.entity_id),
  ).parse(extraction)
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

test('Python and TypeScript use the same semantic ontology digest', () => {
  assert.equal(
    ontologyDigest(DEFAULT_ONTOLOGY_DEFINITION),
    encounterFixture.ontology.ontology_digest,
  )
  const changedDefinition = structuredClone(DEFAULT_ONTOLOGY_DEFINITION)
  const pressureInjury = changedDefinition.classes.find(item => item.class_id === 'PressureInjury')
  assert.ok(pressureInjury)
  pressureInjury.label = 'Changed label that also changes the agent contract'
  assert.notEqual(ontologyDigest(changedDefinition), encounterFixture.ontology.ontology_digest)
  assert.throws(
    () => validateOntologyGraph(changedDefinition, encounterFixture.ontology, new Set(['EV-001'])),
    /digest/,
  )
})

test('fabricated evidence excerpt is rejected', () => {
  const source = sourceBundleSchema.parse(fixture)
  const parsed = createAgentExtractionSchema(
    DEFAULT_ONTOLOGY_DEFINITION.structural_graph.entities.map(entity => entity.entity_id),
  ).parse({
    ...extraction,
    evidence: [{ ...extraction.evidence[0], text: 'Fabricated stage and site.' }],
  })
  assert.throws(() => validateGrounding(source, parsed), /not an exact source excerpt/)
})

test('source metadata changes are rejected', () => {
  const source = sourceBundleSchema.parse(fixture)
  const parsed = createAgentExtractionSchema(
    DEFAULT_ONTOLOGY_DEFINITION.structural_graph.entities.map(entity => entity.entity_id),
  ).parse({
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
  const parsed = createAgentExtractionSchema(
    DEFAULT_ONTOLOGY_DEFINITION.structural_graph.entities.map(entity => entity.entity_id),
  ).parse({
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

test('structural graph templates are data-driven', () => {
  const definition = structuredClone(DEFAULT_ONTOLOGY_DEFINITION)
  const idMap = new Map([
    ['root:patient', 'anchor:patient'],
    ['root:encounter', 'anchor:encounter'],
    ['root:claim', 'anchor:claim'],
  ])
  definition.structural_graph.entities = definition.structural_graph.entities.map(entity => ({
    ...entity,
    entity_id: idMap.get(entity.entity_id) ?? entity.entity_id,
  }))
  definition.structural_graph.relations = definition.structural_graph.relations.map(relation => ({
    ...relation,
    source_id: idMap.get(relation.source_id) ?? relation.source_id,
    target_id: idMap.get(relation.target_id) ?? relation.target_id,
  }))
  const fragment = {
    ...extraction.ontology,
    ontology_digest: ontologyDigest(definition),
    relations: extraction.ontology.relations.map(relation => ({
      ...relation,
      source_id: 'anchor:patient',
    })),
  }
  const graph = mergeWithStructuralGraph(definition, fragment)
  assert.equal(graph.entities[0].entity_id, 'anchor:patient')
  assert.doesNotThrow(() => validateOntologyGraph(definition, graph, new Set(['EV-001'])))
})

test('configurable source and extraction budgets fail before orchestration continues', () => {
  const sourcePolicy = resolveExtractionPolicy({ maxDocuments: 1 })
  assert.throws(
    () => validateRawSourceBundleLimits({ ...fixture, documents: [fixture.documents[0], fixture.documents[0]] }, sourcePolicy),
    /maxDocuments/,
  )

  const outputPolicy = resolveExtractionPolicy({ maxEvidenceCharacters: 10 })
  assert.throws(
    () => validateExtractionLimits(extraction, outputPolicy),
    /maxEvidenceCharacters/,
  )
})

test('invalid extraction policy values fail closed', () => {
  assert.throws(
    () => resolveExtractionPolicy({ maxEntities: 0 }),
    /positive safe integer/,
  )
})

test('ontology extensions fail until the versioned contract supports them', () => {
  const changed = structuredClone(DEFAULT_ONTOLOGY_DEFINITION) as unknown as Record<string, unknown>
  changed.unreviewed_semantics = true
  assert.throws(() => ontologyDefinitionSchema.parse(changed))
})
