import test from 'node:test'
import assert from 'node:assert/strict'

import type { Agent } from '@mastra/core/agent'

import { critiqueDocumentation, detectDocumentationGaps } from './documentation-critic.ts'

const encounter = {
  schema_version: '2.0.0', case_id: 'case-1', patient_id: 'patient-1', encounter_id: 'encounter-1',
  admitted_at: '2026-01-01T00:00:00Z', discharged_at: '2026-01-02T00:00:00Z', metadata: {},
  evidence: [
    { evidence_id: 'ev-1', document_id: 'doc-1', author_role: 'nurse', recorded_at: '2026-01-01T01:00:00Z', text: 'Documented wound care.' },
    { evidence_id: 'ev-2', document_id: 'doc-1', author_role: 'nurse', recorded_at: '2026-01-01T01:00:00Z', text: 'No infection noted.' },
  ],
  ontology: {
    ontology_id: 'wound-care', ontology_version: '1.0.0', ontology_digest: 'a'.repeat(64),
    entities: [
      { entity_id: 'wound-1', entity_type: 'Wound', label: 'Documented wound', properties: {} },
      { entity_id: 'inf-1', entity_type: 'Infection', label: 'Possible infection', properties: {} },
    ],
    relations: [],
  },
  assertions: [
    { assertion_id: 'as-explicit', subject_id: 'wound-1', concept: 'wound', status: 'present', documentation_status: 'explicit', confidence: 0.95, attributes: {}, evidence_ids: ['ev-1'], contradicting_evidence_ids: [] },
    { assertion_id: 'as-inferred', subject_id: 'inf-1', concept: 'infection', status: 'present', documentation_status: 'inferred', confidence: 0.9, attributes: {}, evidence_ids: ['ev-2'], contradicting_evidence_ids: [] },
    { assertion_id: 'as-low', subject_id: 'wound-1', concept: 'depth', status: 'uncertain', documentation_status: 'explicit', confidence: 0.3, attributes: {}, evidence_ids: ['ev-1'], contradicting_evidence_ids: [] },
  ],
  claim: { diagnoses: [], procedures: [], charges: [] },
  provenance: { framework: 'mastra', model_id: 'test/model', agent_id: 'test', extracted_at: '2026-01-02T00:00:00Z', schema_version: '2.0.0', extraction_policy: { max_documents: 1, max_document_characters: 100, max_total_document_characters: 100, max_evidence_items: 10, max_evidence_characters: 100, max_total_evidence_characters: 100, max_entities: 10, max_relations: 10, max_assertions: 10 } },
}

const fakeAgent = (object: unknown): Agent => ({ generate: async () => ({ object }) } as unknown as Agent)

test('deterministic gap detector flags inferred and low-confidence assertions in stable order', () => {
  const gaps = detectDocumentationGaps(encounter)
  assert.deepEqual(gaps.map(g => [g.assertion_id, g.gap_kind]), [
    ['as-inferred', 'inferred_only'],
    ['as-low', 'low_confidence'],
  ])
  assert.equal(gaps[0]?.subject_id, 'inf-1')
  assert.deepEqual(gaps[1]?.evidence_ids, ['ev-1'])
})

test('critique accepts schema-constrained observations citing real assertions', async () => {
  const agent = fakeAgent({
    observations: [
      { observation_id: 'OBS-1', assertion_id: 'as-inferred', subject_id: 'inf-1', gap_kind: 'inferred_only', observation: 'Infection is inferred only.', evidence_ids: ['ev-2'], suggested_documentation: 'Confirm with culture result.' },
    ],
  })
  const result = await critiqueDocumentation(encounter, { agent })
  assert.equal(result.observations.length, 1)
  assert.equal(result.observations[0]?.gap_kind, 'inferred_only')
})

test('critique rejects observations that reference an unknown assertion', async () => {
  const agent = fakeAgent({
    observations: [
      { observation_id: 'OBS-1', assertion_id: 'as-missing', subject_id: 'wound-1', gap_kind: 'missing_evidence', observation: 'No such assertion.', evidence_ids: [], suggested_documentation: '' },
    ],
  })
  await assert.rejects(() => critiqueDocumentation(encounter, { agent }), /unknown assertion/)
})

test('critique rejects a subject_id that does not match its assertion', async () => {
  const agent = fakeAgent({
    observations: [
      { observation_id: 'OBS-1', assertion_id: 'as-inferred', subject_id: 'wound-1', gap_kind: 'inferred_only', observation: 'Wrong subject.', evidence_ids: [], suggested_documentation: '' },
    ],
  })
  await assert.rejects(() => critiqueDocumentation(encounter, { agent }), /must match the assertion subject_id/)
})

test('critique rejects duplicate observations for one assertion', async () => {
  const agent = fakeAgent({
    observations: [
      { observation_id: 'OBS-1', assertion_id: 'as-inferred', subject_id: 'inf-1', gap_kind: 'inferred_only', observation: 'First.', evidence_ids: [], suggested_documentation: '' },
      { observation_id: 'OBS-2', assertion_id: 'as-inferred', subject_id: 'inf-1', gap_kind: 'low_confidence', observation: 'Second.', evidence_ids: [], suggested_documentation: '' },
    ],
  })
  await assert.rejects(() => critiqueDocumentation(encounter, { agent }), /duplicate observation for assertion/)
})
