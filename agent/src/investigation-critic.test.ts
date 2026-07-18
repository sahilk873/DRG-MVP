import test from 'node:test'
import assert from 'node:assert/strict'

import { synthesizeOpportunities } from './agents/investigation-critic.ts'

const hypothesis = (id: string, financial: number) => ({
  hypothesis_id: id, category: 'missed_charge' as const, encounter_id: 'enc-1', hypothesis: 'Possible missed charge',
  evidence_ids: ['ev-1'], contradicting_evidence_ids: [], assertion_ids: [], claim_line_ids: [], missing_information: [],
  candidate_codes: ['SUPPLY-1'], candidate_drgs: [], required_validations: [], recommended_action: '',
  confidence: { evidence: 0.9, semantic: 0.9, financial }, materiality_cents: null,
})

test('synthesis keeps supported hypotheses, ranks value, and deduplicates', () => {
  const result = synthesizeOpportunities([hypothesis('low', 0.4), hypothesis('high', 0.9)], [
    { hypothesis_id: 'low', supported: true, counterevidence_ids: [], rationale: 'Supported', missing_information: [] },
    { hypothesis_id: 'high', supported: true, counterevidence_ids: [], rationale: 'Supported', missing_information: [] },
  ])
  assert.deepEqual(result.map(item => item.hypothesis_id), ['high'])
})

test('synthesis fails closed when the critic rejects a hypothesis', () => {
  const result = synthesizeOpportunities([hypothesis('rejected', 1)], [
    { hypothesis_id: 'rejected', supported: false, counterevidence_ids: ['ev-1'], rationale: 'Already billed', missing_information: [] },
  ])
  assert.equal(result.length, 0)
})
