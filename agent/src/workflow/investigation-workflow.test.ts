import test from 'node:test'
import assert from 'node:assert/strict'

import type { Agent } from '@mastra/core/agent'

import {
  INVESTIGATION_WORKFLOW_ID,
  INVESTIGATION_WORKFLOW_STEPS,
  investigationBundleSchema,
  runInvestigationWorkflow,
} from './investigation-workflow.ts'

const encounter = {
  schema_version: '2.0.0', case_id: 'case-1', patient_id: 'patient-1', encounter_id: 'encounter-1',
  admitted_at: '2026-01-01T00:00:00Z', discharged_at: '2026-01-02T00:00:00Z', metadata: {},
  evidence: [
    { evidence_id: 'ev-1', document_id: 'doc-1', author_role: 'nurse', recorded_at: '2026-01-01T01:00:00Z', text: 'Documented wound care.' },
  ],
  ontology: {
    ontology_id: 'wound-care', ontology_version: '1.0.0', ontology_digest: 'a'.repeat(64),
    entities: [{ entity_id: 'wound-1', entity_type: 'Wound', label: 'Documented wound', properties: {} }],
    relations: [],
  },
  assertions: [
    { assertion_id: 'as-1', subject_id: 'wound-1', concept: 'wound', status: 'present', documentation_status: 'inferred', confidence: 0.9, attributes: {}, evidence_ids: ['ev-1'], contradicting_evidence_ids: [] },
  ],
  claim: { diagnoses: [], procedures: [], charges: [] },
  provenance: { framework: 'mastra', model_id: 'test/model', agent_id: 'test', extracted_at: '2026-01-02T00:00:00Z', schema_version: '2.0.0', extraction_policy: { max_documents: 1, max_document_characters: 100, max_total_document_characters: 100, max_evidence_items: 10, max_evidence_characters: 100, max_total_evidence_characters: 100, max_entities: 10, max_relations: 10, max_assertions: 10 } },
}

const packet = {
  packet_id: 'packet-1', encounter, financial: {}, payer_context: {}, policy_context: {}, data_quality: {},
  allowed_data_views: ['clinical', 'financial'],
}

const hypothesis = (id: string, financial: number) => ({
  hypothesis_id: id, category: 'missed_charge' as const, encounter_id: 'encounter-1', hypothesis: 'Possible missed charge',
  evidence_ids: ['ev-1'], contradicting_evidence_ids: [], assertion_ids: ['as-1'], claim_line_ids: [], missing_information: [],
  candidate_codes: ['SUPPLY-1'], candidate_drgs: [], required_validations: [], recommended_action: '',
  confidence: { evidence: 0.9, semantic: 0.9, financial }, materiality_cents: null,
})

const fakeAgent = (object: unknown): Agent => ({ generate: async () => ({ object }) } as unknown as Agent)

test('workflow composes steps in fixed order and produces a schema-constrained bundle', async () => {
  const bundle = await runInvestigationWorkflow(packet, {
    modelId: 'test/model',
    agents: {
      reconciler: fakeAgent({ hypotheses: [hypothesis('low', 0.4), hypothesis('high', 0.9)] }),
      opportunityCritic: fakeAgent({ critiques: [
        { hypothesis_id: 'low', supported: true, counterevidence_ids: [], rationale: 'Supported', missing_information: [] },
        { hypothesis_id: 'high', supported: true, counterevidence_ids: [], rationale: 'Supported', missing_information: [] },
      ] }),
      documentationCritic: fakeAgent({ observations: [
        { observation_id: 'OBS-1', assertion_id: 'as-1', subject_id: 'wound-1', gap_kind: 'inferred_only', observation: 'Inferred wound.', evidence_ids: ['ev-1'], suggested_documentation: '' },
      ] }),
    },
  })

  investigationBundleSchema.parse(bundle)
  assert.equal(bundle.workflow_id, INVESTIGATION_WORKFLOW_ID)
  assert.deepEqual(bundle.steps, [...INVESTIGATION_WORKFLOW_STEPS])
  assert.equal(bundle.hypotheses.length, 2)
  // Synthesis keeps supported, dedupes identical candidate_codes -> single accepted.
  assert.deepEqual(bundle.accepted_hypotheses.map(h => h.hypothesis_id), ['high'])
  assert.equal(bundle.documentation_observations.length, 1)
  assert.equal(bundle.packet_id, 'packet-1')
  assert.equal(bundle.encounter_id, 'encounter-1')
})

test('workflow skips the critic when reconciliation yields no hypotheses', async () => {
  let critiqueCalled = false
  const bundle = await runInvestigationWorkflow(packet, {
    modelId: 'test/model',
    agents: {
      reconciler: fakeAgent({ hypotheses: [] }),
      opportunityCritic: { generate: async () => { critiqueCalled = true; return { object: { critiques: [] } } } } as unknown as Agent,
      documentationCritic: fakeAgent({ observations: [] }),
    },
  })
  assert.equal(critiqueCalled, false)
  assert.equal(bundle.hypotheses.length, 0)
  assert.equal(bundle.critiques.length, 0)
  assert.equal(bundle.accepted_hypotheses.length, 0)
})

test('workflow fails closed when the packet is not authorized for financial access', async () => {
  const clinicalOnly = { ...packet, allowed_data_views: ['clinical'] }
  await assert.rejects(
    () => runInvestigationWorkflow(clinicalOnly, {
      modelId: 'test/model',
      agents: {
        reconciler: fakeAgent({ hypotheses: [] }),
        documentationCritic: fakeAgent({ observations: [] }),
      },
    }),
    /financial access/,
  )
})

test('workflow rejects a rejected hypothesis from the accepted set', async () => {
  const bundle = await runInvestigationWorkflow(packet, {
    modelId: 'test/model',
    agents: {
      reconciler: fakeAgent({ hypotheses: [hypothesis('rejected', 1)] }),
      opportunityCritic: fakeAgent({ critiques: [
        { hypothesis_id: 'rejected', supported: false, counterevidence_ids: ['ev-1'], rationale: 'Already billed', missing_information: [] },
      ] }),
      documentationCritic: fakeAgent({ observations: [] }),
    },
  })
  assert.equal(bundle.hypotheses.length, 1)
  assert.equal(bundle.accepted_hypotheses.length, 0)
})
