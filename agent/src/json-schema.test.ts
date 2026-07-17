import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import Ajv2020 from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'

const fixtures = [
  ['../schemas/source_bundle.schema.json', '../examples/source_bundle_pressure_injury.json'],
  ['../schemas/encounter_case.schema.json', '../examples/case_pressure_injury.json'],
  ['../schemas/ontology_definition.schema.json', '../src/revenue_integrity/data/wound_care_ontology_v1.json'],
  ['../schemas/rule_package.schema.json', '../rules/wound_care_v1.json'],
  ['../schemas/adapter_definition.schema.json', '../examples/adapters/clinic_alpha_wound_care_v1.json'],
] as const

test('published JSON Schemas compile and accept canonical fixtures', async () => {
  const ajv = new Ajv2020({ allErrors: true, strict: true })
  addFormats(ajv)
  ajv.addSchema(JSON.parse(await readFile('../schemas/claim.schema.json', 'utf8')))
  ajv.addSchema(JSON.parse(await readFile('../schemas/extraction_fragment.schema.json', 'utf8')))
  for (const [schemaPath, fixturePath] of fixtures) {
    const schema = JSON.parse(await readFile(schemaPath, 'utf8'))
    const fixture = JSON.parse(await readFile(fixturePath, 'utf8'))
    const validate = ajv.compile(schema)
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

test('review decision schema compiles', async () => {
  const ajv = new Ajv2020({ allErrors: true, strict: true })
  addFormats(ajv)
  const schema = JSON.parse(await readFile('../schemas/review_decision.schema.json', 'utf8'))
  assert.doesNotThrow(() => ajv.compile(schema))
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
