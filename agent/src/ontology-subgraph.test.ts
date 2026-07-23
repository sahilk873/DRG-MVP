import assert from 'node:assert/strict'
import { test } from 'node:test'

import { DEFAULT_ONTOLOGY_DEFINITION, ontologyDigest, ontologyPromptContract } from './ontology.ts'
import {
  estimateTokens,
  projectOntologyContract,
  selectOntologySubgraph,
} from './ontology-subgraph.ts'

const definition = DEFAULT_ONTOLOGY_DEFINITION

test('selects pressure-injury classes with their full ancestor chain', () => {
  const selection = selectOntologySubgraph(definition, ['stage 4 sacral pressure injury'])
  assert.ok(selection.classIds.includes('PressureInjury'))
  // Ancestor chain must be closed: PressureInjury -> Wound -> ... up to a root.
  const classes = new Map(definition.classes.map(c => [c.class_id, c]))
  for (const id of selection.classIds) {
    const parent = classes.get(id)?.parent
    if (parent) assert.ok(selection.classIds.includes(parent), `missing ancestor ${parent} of ${id}`)
  }
})

test('selected relations have a selected class on both domain and range sides', () => {
  const selection = selectOntologySubgraph(definition, ['pressure injury stage exudate'])
  const selected = new Set(selection.classIds)
  const relById = new Map(definition.relations.map(r => [r.relation_id, r]))
  for (const relationId of selection.relationIds) {
    const relation = relById.get(relationId)!
    assert.ok(relation.domain.some(c => selected.has(c)), `${relationId} domain not covered`)
    assert.ok(relation.range.some(c => selected.has(c)), `${relationId} range not covered`)
  }
})

test('only value-sets referenced by selected classes are included', () => {
  const selection = selectOntologySubgraph(definition, ['pressure injury stage'])
  const classes = new Map(definition.classes.map(c => [c.class_id, c]))
  const referenced = new Set(
    selection.classIds.map(id => classes.get(id)?.value_set).filter(Boolean) as string[],
  )
  assert.deepEqual([...selection.valueSetIds].sort(), [...referenced].sort())
})

test('selection is deterministic and digest-stable across runs', () => {
  const a = selectOntologySubgraph(definition, ['pressure injury', 'sacrum'])
  const b = selectOntologySubgraph(definition, ['pressure injury', 'sacrum'])
  assert.deepEqual(a, b)
  assert.equal(a.selectionDigest, b.selectionDigest)
})

test('recall safety: a sibling concept in the terms still enters the subgraph', () => {
  // A pressure-injury bundle that also mentions a diabetic foot ulcer must pull that class in.
  const selection = selectOntologySubgraph(definition, ['pressure injury', 'diabetic foot ulcer'])
  assert.ok(selection.classIds.includes('DiabeticFootUlcer'))
  assert.ok(selection.classIds.includes('PressureInjury'))
})

test('scoped contract is a strict subset that meaningfully cuts tokens', () => {
  const selection = selectOntologySubgraph(definition, ['pressure injury stage'])
  const full = ontologyPromptContract(definition)
  const scoped = projectOntologyContract(definition, selection, ontologyDigest(definition))
  const fullClasses = (full as { classes: unknown[] }).classes.length
  const scopedClasses = (scoped as { classes: unknown[] }).classes.length
  assert.ok(scopedClasses < fullClasses, 'scoped contract must have fewer classes')
  assert.ok(estimateTokens(scoped) < estimateTokens(full), 'scoped contract must be cheaper')
})

test('retrieval cannot widen validation: the ontology digest is unchanged', () => {
  // The selector is a prompt hint only; it must not touch the ontology artifact/digest.
  const before = ontologyDigest(definition)
  selectOntologySubgraph(definition, ['pressure injury'])
  assert.equal(ontologyDigest(definition), before)
})
