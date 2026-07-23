import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import Ajv2020 from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'

const fixtures = [
  ['../schemas/source_bundle.schema.json', '../examples/source_bundle_pressure_injury.json'],
  ['../schemas/encounter_case.schema.json', '../examples/case_pressure_injury.json'],
  ['../schemas/encounter_case.schema.json', '../examples/case_diabetic_foot_ulcer_episode.json'],
  ['../schemas/ontology_definition.schema.json', '../src/revenue_integrity/data/wound_care_ontology_v1.json'],
  ['../schemas/ontology_definition.schema.json', '../src/revenue_integrity/data/wound_care_ontology_v2.json'],
  ['../schemas/ontology_definition.schema.json', '../src/revenue_integrity/data/wound_care_ontology_v3.json'],
  ['../schemas/rule_package.schema.json', '../rules/wound_care_v1.json'],
  ['../schemas/rule_package.schema.json', '../rules/wound_care_gaps_v1.json'],
  ['../schemas/adapter_definition.schema.json', '../examples/adapters/clinic_alpha_wound_care_v1.json'],
] as const

test('published JSON Schemas compile and accept canonical fixtures', async () => {
  const ajv = new Ajv2020({ allErrors: true, strict: true })
  addFormats(ajv)
  ajv.addSchema(JSON.parse(await readFile('../schemas/claim.schema.json', 'utf8')))
  ajv.addSchema(JSON.parse(await readFile('../schemas/extraction_fragment.schema.json', 'utf8')))
  // Compile each schema once (AJV rejects re-adding a schema with a duplicate $id), then reuse
  // the compiled validator across every fixture that binds to it.
  const validators = new Map<string, ReturnType<typeof ajv.compile>>()
  for (const [schemaPath, fixturePath] of fixtures) {
    let validate = validators.get(schemaPath)
    if (validate === undefined) {
      validate = ajv.compile(JSON.parse(await readFile(schemaPath, 'utf8')))
      validators.set(schemaPath, validate)
    }
    const fixture = JSON.parse(await readFile(fixturePath, 'utf8'))
    assert.equal(
      validate(fixture),
      true,
      `${fixturePath} does not satisfy ${schemaPath}: ${ajv.errorsText(validate.errors)}`,
    )
  }
})

test('profile and extraction fragment JSON Schemas compile strictly', async () => {
  const ajv = new Ajv2020({ allErrors: true, strict: true })
  addFormats(ajv)
  for (const schemaPath of ['../schemas/bulk_profile.schema.json', '../schemas/extraction_fragment.schema.json']) {
    const schema = JSON.parse(await readFile(schemaPath, 'utf8'))
    assert.doesNotThrow(() => ajv.compile(schema))
  }
})

test('review packet schema accepts the engine-generated demo handoff', async () => {
  const ajv = new Ajv2020({ allErrors: true, strict: true })
  addFormats(ajv)
  ajv.addSchema(JSON.parse(await readFile('../schemas/claim.schema.json', 'utf8')))
  ajv.addSchema(JSON.parse(await readFile('../schemas/encounter_case.schema.json', 'utf8')))
  const schema = JSON.parse(await readFile('../schemas/review_packet.schema.json', 'utf8'))
  const fixture = JSON.parse(await readFile('../demo/src/fixtures/review-packet.json', 'utf8'))
  const validate = ajv.compile(schema)
  assert.equal(validate(fixture), true, ajv.errorsText(validate.errors))
  assert.equal(fixture.controls.claim_mutation_allowed, false)
  assert.equal(fixture.controls.human_review_required, true)
})

test('rule package schema enforces the clinical/revenue domain wall (finding #9)', async () => {
  const ajv = new Ajv2020({ allErrors: true, strict: true })
  addFormats(ajv)
  const schema = JSON.parse(await readFile('../schemas/rule_package.schema.json', 'utf8'))
  const validate = ajv.compile(schema)

  const gapPkg = JSON.parse(await readFile('../rules/wound_care_gaps_v1.json', 'utf8'))
  const revPkg = JSON.parse(await readFile('../rules/wound_care_v1.json', 'utf8'))

  // The shipped, governed packages must still satisfy the wall.
  assert.equal(validate(gapPkg), true, ajv.errorsText(validate.errors))
  assert.equal(validate(revPkg), true, ajv.errorsText(validate.errors))

  // A clinical_care_gap rule carrying a non-empty proposed_change must be rejected by the
  // JSON schema itself (not merely by the Python parse-time wall in rules.py).
  const gapWithMutation = JSON.parse(JSON.stringify(gapPkg))
  gapWithMutation.rules[0].then.proposed_change = { add_diagnoses: ['L89.153'] }
  assert.equal(
    validate(gapWithMutation),
    false,
    'clinical_care_gap rule with a proposed_change must fail JSON-Schema validation',
  )

  // A clinical_care_gap rule that does not require human review must also be rejected.
  const gapNoReview = JSON.parse(JSON.stringify(gapPkg))
  gapNoReview.rules[0].then.requires_human_review = false
  assert.equal(
    validate(gapNoReview),
    false,
    'clinical_care_gap rule must require human review',
  )

  // A revenue_integrity rule carrying any clinical-care-gap field must be rejected.
  const revWithGapField = JSON.parse(JSON.stringify(revPkg))
  revWithGapField.rules[0].then.gap_domain = 'missing_action'
  assert.equal(
    validate(revWithGapField),
    false,
    'revenue_integrity rule carrying gap_domain must fail JSON-Schema validation',
  )

  const revWithUrgency = JSON.parse(JSON.stringify(revPkg))
  revWithUrgency.rules[0].then.alert_urgency = 'urgent'
  assert.equal(
    validate(revWithUrgency),
    false,
    'revenue_integrity rule carrying alert_urgency must fail JSON-Schema validation',
  )
})

test('review decision schema compiles', async () => {
  const ajv = new Ajv2020({ allErrors: true, strict: true })
  addFormats(ajv)
  const schema = JSON.parse(await readFile('../schemas/review_decision.schema.json', 'utf8'))
  assert.doesNotThrow(() => ajv.compile(schema))
})

test('automation plan schema accepts the deterministic demo plan', async () => {
  const ajv = new Ajv2020({ allErrors: true, strict: true })
  addFormats(ajv)
  const schema = JSON.parse(await readFile('../schemas/automation_plan.schema.json', 'utf8'))
  const fixture = JSON.parse(await readFile('../demo/src/fixtures/automation-plan.json', 'utf8'))
  const validate = ajv.compile(schema)
  assert.equal(validate(fixture), true, ajv.errorsText(validate.errors))
  assert.equal(fixture.review_now_finding_ids.length, 1)
  assert.equal(fixture.findings[0].tier, 'quick_confirm')
})

test('encounter schema rejects an ontology digest with the wrong shape', async () => {
  const ajv = new Ajv2020({ allErrors: true, strict: true })
  addFormats(ajv)
  ajv.addSchema(JSON.parse(await readFile('../schemas/claim.schema.json', 'utf8')))
  const schema = JSON.parse(await readFile('../schemas/encounter_case.schema.json', 'utf8'))
  const fixture = JSON.parse(await readFile('../examples/case_pressure_injury.json', 'utf8'))
  fixture.ontology.ontology_digest = 'invalid'
  assert.equal(ajv.compile(schema)(fixture), false)
})
