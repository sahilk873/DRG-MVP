import assert from 'node:assert/strict'
import { test } from 'node:test'

import { agentExtractionSchema, createAgentExtractionSchema } from './schema.ts'

// The TS validateLineage boundary is the agent/browser-side anti-hallucination guard: it
// rejects extractions whose relations/assertions cite entities or evidence the model did
// not ground. It is function-scoped, so we exercise it the way real consumers do — through
// createAgentExtractionSchema(...).safeParse — and assert on the emitted issues.

const DIGEST = 'a'.repeat(64)

function baseExtraction() {
  return {
    evidence: [
      { evidence_id: 'EV-1', document_id: 'DOC-1', author_role: 'rn', recorded_at: '2026-01-01T00:00:00Z', text: 'wound note' },
      { evidence_id: 'EV-2', document_id: 'DOC-1', author_role: 'md', recorded_at: '2026-01-01T01:00:00Z', text: 'progress note' },
    ],
    ontology: {
      ontology_id: 'wound-care-encounter-ontology',
      ontology_version: '1.0.0-draft',
      ontology_digest: DIGEST,
      entities: [
        { entity_id: 'wound:1', entity_type: 'PressureInjury', label: 'Sacral PI', properties: {} },
        { entity_id: 'enc:1', entity_type: 'Encounter', label: 'Encounter', properties: {} },
      ],
      relations: [
        {
          relation_id: 'REL-1', predicate: 'occurred_during', source_id: 'wound:1', target_id: 'enc:1',
          assertion_status: 'present', documentation_status: 'explicit', confidence: 0.9,
          evidence_ids: ['EV-1'] as string[], contradicting_evidence_ids: [] as string[],
        },
      ],
    },
    assertions: [
      {
        assertion_id: 'AS-1', subject_id: 'wound:1', concept: 'pressure injury stage 4',
        status: 'present', documentation_status: 'explicit', confidence: 0.9,
        attributes: {}, evidence_ids: ['EV-1'] as string[], contradicting_evidence_ids: [] as string[],
      },
    ],
  }
}

test('a fully grounded extraction parses with zero issues', () => {
  const result = agentExtractionSchema.safeParse(baseExtraction())
  assert.equal(result.success, true, JSON.stringify(result.error?.issues))
})

test('an assertion citing an unknown ontology subject is rejected', () => {
  const payload = baseExtraction()
  payload.assertions[0].subject_id = 'wound:ghost'
  const result = agentExtractionSchema.safeParse(payload)
  assert.equal(result.success, false)
  assert.ok(result.error!.issues.some(issue => issue.message.includes('unknown ontology subject')))
})

test('an assertion citing evidence that does not exist is rejected', () => {
  const payload = baseExtraction()
  payload.assertions[0].evidence_ids = ['EV-404']
  const result = agentExtractionSchema.safeParse(payload)
  assert.equal(result.success, false)
  assert.ok(result.error!.issues.some(issue => issue.message.includes('unknown evidence reference')))
})

test('a relation pointing at an unknown entity is rejected', () => {
  const payload = baseExtraction()
  payload.ontology.relations[0].target_id = 'enc:ghost'
  const result = agentExtractionSchema.safeParse(payload)
  assert.equal(result.success, false)
  assert.ok(result.error!.issues.some(issue => issue.message.includes('unknown entity reference')))
})

test('evidence cannot both support and contradict an assertion', () => {
  const payload = baseExtraction()
  payload.assertions[0].contradicting_evidence_ids = ['EV-1']
  const result = agentExtractionSchema.safeParse(payload)
  assert.equal(result.success, false)
  assert.ok(result.error!.issues.some(issue => issue.message.toLowerCase().includes('cannot both support and contradict')))
})

test('duplicate evidence ids are rejected', () => {
  const payload = baseExtraction()
  payload.evidence.push({ ...payload.evidence[0] })
  const result = agentExtractionSchema.safeParse(payload)
  assert.equal(result.success, false)
  assert.ok(result.error!.issues.some(issue => issue.message.includes('evidence_id values must be unique')))
})

test('allowedExternalEntityIds admits a structural-graph-only subject only when whitelisted', () => {
  const payload = baseExtraction()
  payload.assertions[0].subject_id = 'patient:external'
  // Default schema rejects the external subject...
  assert.equal(agentExtractionSchema.safeParse(payload).success, false)
  // ...but an explicitly whitelisted external id is accepted.
  const permissive = createAgentExtractionSchema(['patient:external'])
  assert.equal(permissive.safeParse(payload).success, true)
})
