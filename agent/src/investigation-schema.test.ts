import test from 'node:test'
import assert from 'node:assert/strict'

import { createReconciliationOutputSchema, investigationPacketSchema } from './schema.ts'

const encounter = {
  schema_version: '2.0.0', case_id: 'case-1', patient_id: 'patient-1', encounter_id: 'encounter-1',
  admitted_at: '2026-01-01T00:00:00Z', discharged_at: '2026-01-02T00:00:00Z', metadata: {},
  evidence: [{ evidence_id: 'ev-1', document_id: 'doc-1', author_role: 'nurse', recorded_at: '2026-01-01T01:00:00Z', text: 'Documented care.' }],
  ontology: { ontology_id: 'wound-care', ontology_version: '1.0.0', ontology_digest: 'a'.repeat(64), entities: [{ entity_id: 'wound-1', entity_type: 'Wound', label: 'Documented wound', properties: {} }], relations: [] },
  assertions: [{ assertion_id: 'as-1', subject_id: 'wound-1', concept: 'wound', status: 'present', documentation_status: 'explicit', confidence: 0.9, attributes: {}, evidence_ids: ['ev-1'] }],
  claim: { diagnoses: [], procedures: [], charges: [] },
  provenance: { framework: 'mastra', model_id: 'test/model', agent_id: 'test', extracted_at: '2026-01-02T00:00:00Z', schema_version: '2.0.0', extraction_policy: { max_documents: 1, max_document_characters: 100, max_total_document_characters: 100, max_evidence_items: 10, max_evidence_characters: 100, max_total_evidence_characters: 100, max_entities: 10, max_relations: 10, max_assertions: 10 } },
}

test('reconciliation schema accepts grounded hypotheses', () => {
  const packet = investigationPacketSchema.parse({ packet_id: 'packet-1', encounter, financial: {}, payer_context: {}, policy_context: {}, data_quality: {}, allowed_data_views: ['clinical', 'financial'] })
  const result = createReconciliationOutputSchema(packet).parse({ hypotheses: [{ hypothesis_id: 'opp-1', category: 'missed_charge', encounter_id: 'encounter-1', hypothesis: 'Possible charge capture gap.', evidence_ids: ['ev-1'], assertion_ids: ['as-1'], confidence: { evidence: 0.9, semantic: 0.8, financial: 0.4 } }] })
  assert.equal(result.hypotheses.length, 1)
})

test('reconciliation schema rejects unknown evidence', () => {
  const packet = investigationPacketSchema.parse({ packet_id: 'packet-1', encounter, financial: {}, payer_context: {}, policy_context: {}, data_quality: {}, allowed_data_views: ['clinical', 'financial'] })
  assert.throws(() => createReconciliationOutputSchema(packet).parse({ hypotheses: [{ hypothesis_id: 'opp-1', category: 'missed_charge', encounter_id: 'encounter-1', hypothesis: 'Possible charge capture gap.', evidence_ids: ['missing'], confidence: { evidence: 0.9, semantic: 0.8, financial: 0.4 } }] }))
})
