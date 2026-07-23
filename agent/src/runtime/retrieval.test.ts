import assert from 'node:assert/strict'
import { test } from 'node:test'

import { designAdapter } from '../agents/adapter-designer.ts'
import { DEFAULT_ONTOLOGY_DEFINITION } from '../ontology.ts'
import type { AdapterDefinition, BulkProfile } from '../onboarding/schema.ts'
import { adapterFieldTokens, featuresFromProfile } from './adapter-precedent.ts'
import { retrieve, type Exemplar } from './retrieval.ts'

const DIGEST = 'a'.repeat(64)

function exemplar(id: string, features: string[]): Exemplar<{ id: string }> {
  return { id, features, payload: { id } }
}

test('retrieve ranks by feature overlap and is deterministic', () => {
  const library = [exemplar('mercy', ['mrn', 'admit_dttm', 'icd10_cm', 'pi_stage']), exemplar('clinic', ['patient_id', 'note_text', 'stage'])]
  const query = ['mrn', 'admit_dttm', 'icd10_cm']
  const a = retrieve(query, library, { k: 1 })
  const b = retrieve(query, library, { k: 1 })
  assert.equal(a.exemplars[0]!.payload.id, 'mercy')
  assert.equal(a.digest, b.digest)
})

test('retrieve bounds to k and filters out zero-overlap exemplars', () => {
  const library = [exemplar('a', ['x', 'y']), exemplar('b', ['y', 'z']), exemplar('c', ['p', 'q'])]
  assert.equal(retrieve(['y'], library, { k: 1 }).exemplars.length, 1)
  assert.deepEqual(retrieve(['totally', 'unrelated'], library, { k: 5 }).exemplars, [])
})

test('adapterFieldTokens collects the bound source columns', () => {
  const adapter = {
    encounter: { case_id: { field: 'case_id' }, admitted_at: { field: 'admit_dttm', operations: [{ op: 'datetime' }] } },
    claim: { diagnoses: { value: { field: 'icd10_cm' } } },
  }
  assert.deepEqual(new Set(adapterFieldTokens(adapter)), new Set(['case_id', 'admit_dttm', 'icd10_cm']))
})

test('featuresFromProfile collects every column name', () => {
  const profile = { artifacts: [{ columns: [{ name: 'mrn' }, { name: 'encounter_id' }] }, { columns: [{ name: 'icd10_cm' }] }] } as unknown as BulkProfile
  assert.deepEqual(featuresFromProfile(profile), ['mrn', 'encounter_id', 'icd10_cm'])
})

test('designAdapter uses RAG to send only the relevant precedent to the model', async () => {
  const profile = {
    profile_version: '1.0.0', schema_fingerprint: DIGEST, input_manifest_digest: DIGEST,
    artifact_count: 1, total_bytes: 100,
    artifacts: [{
      artifact_id: 'encounters.csv', path: 'encounters.csv', format: 'csv', size_bytes: 100,
      profiled_rows: 1, truncated: false,
      columns: [
        { name: 'mrn', inferred_types: ['string'], missing_count: 0, distinct_count: 1 },
        { name: 'admit_dttm', inferred_types: ['string'], missing_count: 0, distinct_count: 1 },
        { name: 'icd10_cm', inferred_types: ['string'], missing_count: 0, distinct_count: 1 },
      ],
      sample_rows: [],
    }],
  }
  const library: Exemplar<AdapterDefinition>[] = [
    { id: 'relevant', features: ['mrn', 'admit_dttm', 'icd10_cm'], payload: { adapter_id: 'relevant-adapter' } as unknown as AdapterDefinition },
    { id: 'irrelevant', features: ['patient_id', 'note_text', 'charge_code'], payload: { adapter_id: 'irrelevant-adapter' } as unknown as AdapterDefinition },
  ]

  let capturedPrompt = ''
  const stubAgent = {
    generate: async (prompt: string) => {
      capturedPrompt = prompt
      throw new Error('__stop__')  // capture the input, then halt before a real generation
    },
  } as unknown as Parameters<typeof designAdapter>[2] extends { agent?: infer A } ? A : never

  await assert.rejects(
    designAdapter(profile, DEFAULT_ONTOLOGY_DEFINITION, { agent: stubAgent as never, precedentLibrary: library, retrieveK: 1 }),
    /__stop__/,
  )
  assert.match(capturedPrompt, /relevant-adapter/)
  assert.doesNotMatch(capturedPrompt, /irrelevant-adapter/)
})
