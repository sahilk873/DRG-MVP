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
] as const

test('published JSON Schemas compile and accept canonical fixtures', async () => {
  const ajv = new Ajv2020({ allErrors: true, strict: true })
  addFormats(ajv)
  ajv.addSchema(JSON.parse(await readFile('../schemas/claim.schema.json', 'utf8')))
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

test('encounter schema rejects an ontology digest with the wrong shape', async () => {
  const ajv = new Ajv2020({ allErrors: true, strict: true })
  addFormats(ajv)
  ajv.addSchema(JSON.parse(await readFile('../schemas/claim.schema.json', 'utf8')))
  const schema = JSON.parse(await readFile('../schemas/encounter_case.schema.json', 'utf8'))
  const fixture = JSON.parse(await readFile('../examples/case_pressure_injury.json', 'utf8'))
  fixture.ontology.ontology_digest = 'invalid'
  assert.equal(ajv.compile(schema)(fixture), false)
})
