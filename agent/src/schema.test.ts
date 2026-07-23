import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import type { Agent } from '@mastra/core/agent'

import { extractEncounterCase, resolveModelId, validateGrounding } from './agents/encounter-extractor.ts'
import { validateAdapterSemantics } from './agents/adapter-designer.ts'
import { adapterDefinitionSchema, bulkProfileSchema } from './onboarding/schema.ts'
import {
  DEFAULT_ONTOLOGY_DEFINITION,
  mergeWithStructuralGraph,
  ontologyDigest,
  validateOntologyGraph,
} from './ontology.ts'
import {
  createAgentExtractionSchema,
  createGapExtractionSchema,
  encounterCaseSchema,
  gapExtractionOutputSchema,
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
    ontology_version: '1.1.0-draft',
    ontology_digest: '66da3211d53adaa7cffc4fd45e0a7ca86175f5a7774d5ce80d4a34a0a0786f52',
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

test('model evidence cannot claim deterministic row lineage', () => {
  const source = sourceBundleSchema.parse(fixture)
  const parsed = createAgentExtractionSchema(
    DEFAULT_ONTOLOGY_DEFINITION.structural_graph.entities.map(entity => entity.entity_id),
  ).parse({
    ...extraction,
    evidence: [{
      ...extraction.evidence[0],
      source_locator: {
        adapter_id: 'fabricated',
        adapter_version: '1',
        resource: 'fake',
        path: 'fake.csv',
        row_number: 1,
        source_record_id: 'fake',
        field_names: ['fake'],
      },
    }],
  })
  assert.throws(() => validateGrounding(source, parsed), /cannot provide a deterministic source locator/)
})

test('deterministic and model ontology fragments merge into one validated case', async () => {
  const structuredEvidence = {
    ...extraction.evidence[0],
    evidence_id: 'STRUCTURED-EV-001',
    document_id: 'structured-source:1',
    author_role: 'source-system',
    source_locator: {
      adapter_id: 'clinic-alpha',
      adapter_version: '1.0.0',
      resource: 'wounds',
      path: 'wounds.csv',
      row_number: 1,
      source_record_id: 'W-1',
      field_names: ['stage'],
    },
  }
  const structured = {
    evidence: [structuredEvidence],
    ontology: {
      ...extraction.ontology,
      entities: [{
        entity_id: 'wound:structured',
        entity_type: 'PressureInjury',
        label: 'Structured pressure injury',
        properties: {},
      }],
      relations: [{
        ...extraction.ontology.relations[0],
        relation_id: 'rel:structured-wound',
        target_id: 'wound:structured',
        evidence_ids: ['STRUCTURED-EV-001'],
      }],
    },
    assertions: [{
      ...extraction.assertions[0],
      assertion_id: 'AS-STRUCTURED-001',
      subject_id: 'wound:structured',
      evidence_ids: ['STRUCTURED-EV-001'],
    }],
  }
  const fakeAgent = {
    generate: async () => ({
      object: {
        evidence: [],
        ontology: {
          ontology_id: DEFAULT_ONTOLOGY_DEFINITION.ontology_id,
          ontology_version: DEFAULT_ONTOLOGY_DEFINITION.version,
          ontology_digest: ontologyDigest(DEFAULT_ONTOLOGY_DEFINITION),
          entities: [],
          relations: [],
        },
        assertions: [],
      },
    }),
  } as unknown as Agent
  const transformed = {
    ...fixture,
    structured_extraction: structured,
    ingestion_provenance: {
      framework: 'deterministic-adapter',
      adapter_id: 'clinic-alpha',
      adapter_version: '1.0.0',
      source_schema_fingerprint: '1'.repeat(64),
      input_manifest_digest: '2'.repeat(64),
      transformed_at: '2026-07-15T00:00:00Z',
      runtime_version: '1.0.0',
    },
  }

  const encounter = await extractEncounterCase(transformed, {
    agent: fakeAgent,
    modelId: 'test/empty-extraction',
    now: () => new Date('2026-07-15T00:01:00Z'),
  })
  assert.equal(encounter.assertions[0]?.assertion_id, 'AS-STRUCTURED-001')
  assert.equal(encounter.evidence[0]?.source_locator?.row_number, 1)
  assert.equal(encounter.provenance.ingestion?.adapter_id, 'clinic-alpha')
})

test('adapter contract accepts the governed fixture and rejects ontology drift', async () => {
  const adapter = adapterDefinitionSchema.parse(
    JSON.parse(await readFile('../examples/adapters/clinic_alpha_wound_care_v1.json', 'utf8')),
  )
  const columnsByPath: Record<string, string[]> = {
    'encounters.csv': ['case_id', 'patient_id', 'encounter_id', 'admitted_at', 'discharged_at', 'facility'],
    'notes.csv': ['encounter_id', 'note_id', 'author_role', 'recorded_at', 'note_text'],
    'claims.csv': ['encounter_id', 'submitted_drg', 'allowed_amount_cents'],
    'diagnoses.csv': ['encounter_id', 'diagnosis_code'],
    'charges.csv': ['encounter_id', 'charge_code'],
    'wound_assessments.csv': ['encounter_id', 'assessment_id', 'wound_id', 'recorded_at', 'stage', 'site', 'poa'],
  }
  const profile = bulkProfileSchema.parse({
    profile_version: '1.0.0',
    schema_fingerprint: adapter.source_schema_fingerprint,
    input_manifest_digest: '0'.repeat(64),
    artifact_count: 6,
    total_bytes: 691,
    artifacts: Object.values(adapter.resources).map(resource => ({
      artifact_id: resource.sheet ? `${resource.path}#${resource.sheet}` : resource.path,
      path: resource.path,
      format: resource.format,
      ...(resource.sheet ? { sheet: resource.sheet } : {}),
      size_bytes: 1,
      profiled_rows: 1,
      truncated: false,
      columns: (columnsByPath[resource.path] ?? []).map(name => ({
        name,
        inferred_types: ['string'],
        missing_count: 0,
        distinct_count: 1,
      })),
      sample_rows: [],
    })),
  })
  assert.deepEqual(validateAdapterSemantics(profile, DEFAULT_ONTOLOGY_DEFINITION, adapter), [])
  const changed = structuredClone(adapter)
  changed.structured_projections[0]!.entities[0]!.entity_type = 'UnknownClass'
  assert.match(
    validateAdapterSemantics(profile, DEFAULT_ONTOLOGY_DEFINITION, changed).join(';'),
    /unknown class/,
  )
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

// --------------------------------------------------------------------------
// Clinical-care-gap EXTRACTION-ONLY schema
// --------------------------------------------------------------------------
const gapExtraction = {
  assessments: [
    {
      assessment_id: 'assessment:day0',
      subject_id: 'assessment:day0',
      observed_at: '2026-06-01T10:00:00Z',
      measurement: { length_cm: 2.4, width_cm: 1.8, depth_cm: 0.3 },
      evidence_ids: ['EV-DFU-DAY0'],
    },
    {
      assessment_id: 'assessment:day14',
      subject_id: 'assessment:day14',
      observed_at: '2026-06-15T10:00:00Z',
      measurement: { length_cm: 2.4, width_cm: 1.8 },
      evidence_ids: ['EV-DFU-DAY14'],
    },
  ],
  candidate_findings: [
    {
      candidate_id: 'cand:stalled-healing',
      subject_id: 'assessment:day14',
      concept: 'stalled_wound_healing',
      status: 'present' as const,
      documentation_status: 'explicit' as const,
      confidence: 0.9,
      observation: 'Ulcer unchanged at 2.4 x 1.8 cm after two weeks of standard care.',
      evidence_ids: ['EV-DFU-DAY14'],
      contradicting_evidence_ids: [],
    },
  ],
}

const gapEvidenceIds = ['EV-DFU-DAY0', 'EV-DFU-DAY14']

test('valid dated wound assessments and candidate findings parse as extraction-only', () => {
  const parsed = gapExtractionOutputSchema.parse(gapExtraction)
  assert.equal(parsed.assessments.length, 2)
  assert.equal(parsed.assessments[0].measurement.length_cm, 2.4)
  assert.equal(parsed.assessments[0].measurement.depth_cm, 0.3)
  assert.equal(parsed.candidate_findings[0].concept, 'stalled_wound_healing')
  // Lineage-checked variant accepts citations that resolve to real evidence.
  assert.doesNotThrow(() => createGapExtractionSchema(gapEvidenceIds).parse(gapExtraction))
})

test('gap extraction may not carry an authoritative gap DECISION field', () => {
  // gap_domain is an authoritative engine output; a strict schema rejects it on the finding.
  const withGapDomain = structuredClone(gapExtraction)
  ;(withGapDomain.candidate_findings[0] as Record<string, unknown>).gap_domain = 'delayed_action'
  assert.throws(() => gapExtractionOutputSchema.parse(withGapDomain))

  // An urgency / alert level is likewise an engine decision, never an extraction.
  const withUrgency = structuredClone(gapExtraction)
  ;(withUrgency.candidate_findings[0] as Record<string, unknown>).alert_urgency = 'urgent'
  assert.throws(() => gapExtractionOutputSchema.parse(withUrgency))

  // A recommended clinical action is a decision the engine/clinician makes, not the model.
  const withAction = structuredClone(gapExtraction)
  ;(withAction.candidate_findings[0] as Record<string, unknown>).recommended_action = 'Reassess the wound.'
  assert.throws(() => gapExtractionOutputSchema.parse(withAction))
})

test('gap extraction may not carry timing math or claim/DRG/payment fields', () => {
  // Elapsed-day / trend math is derived deterministically by Python, never emitted here.
  const withTiming = structuredClone(gapExtraction)
  ;(withTiming.assessments[1] as Record<string, unknown>).days_since_baseline = 14
  assert.throws(() => gapExtractionOutputSchema.parse(withTiming))

  const withTrend = structuredClone(gapExtraction)
  ;(withTrend.assessments[1] as Record<string, unknown>).size_trend_pct = 0
  assert.throws(() => gapExtractionOutputSchema.parse(withTrend))

  // No authoritative financial field may ride an extraction measurement.
  const withPayment = structuredClone(gapExtraction)
  ;(withPayment.assessments[0].measurement as Record<string, unknown>).allowed_amount_cents = 500000
  assert.throws(() => gapExtractionOutputSchema.parse(withPayment))

  const withDrg = structuredClone(gapExtraction)
  ;(withDrg.candidate_findings[0] as Record<string, unknown>).drg = 'DEMO-292'
  assert.throws(() => gapExtractionOutputSchema.parse(withDrg))
})

test('gap extraction rejects ungrounded citations and self-contradicting evidence', () => {
  const unknownEvidence = structuredClone(gapExtraction)
  unknownEvidence.candidate_findings[0].evidence_ids = ['EV-DOES-NOT-EXIST']
  assert.throws(() => createGapExtractionSchema(gapEvidenceIds).parse(unknownEvidence))

  const conflicting = structuredClone(gapExtraction)
  ;(conflicting.candidate_findings[0] as Record<string, unknown>).contradicting_evidence_ids = ['EV-DFU-DAY14']
  assert.throws(() => createGapExtractionSchema(gapEvidenceIds).parse(conflicting))
})
