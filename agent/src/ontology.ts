import woundCareOntology from '../../src/revenue_integrity/data/wound_care_ontology_v1.json' with { type: 'json' }

import {
  ontologyDefinitionSchema,
  type OntologyDefinition,
  type OntologyGraph,
} from './schema.ts'

export const CORE_ENTITY_IDS = ['root:patient', 'root:encounter', 'root:claim'] as const

export const DEFAULT_ONTOLOGY_DEFINITION = ontologyDefinitionSchema.parse(woundCareOntology)
validateOntologyDefinition(DEFAULT_ONTOLOGY_DEFINITION)

export function validateOntologyDefinition(definition: OntologyDefinition): void {
  const classes = new Map<string, (typeof definition.classes)[number]>()
  for (const item of definition.classes) {
    if (classes.has(item.class_id)) throw new Error(`duplicate ontology class: ${item.class_id}`)
    classes.set(item.class_id, item)
    if (item.value_set && !definition.value_sets?.[item.value_set]) {
      throw new Error(`class ${item.class_id} has unknown value set ${item.value_set}`)
    }
  }
  const relations = new Set<string>()
  for (const item of definition.relations) {
    if (relations.has(item.relation_id)) throw new Error(`duplicate ontology relation: ${item.relation_id}`)
    relations.add(item.relation_id)
    for (const classId of [...item.domain, ...item.range]) {
      if (!classes.has(classId)) throw new Error(`relation ${item.relation_id} references unknown class ${classId}`)
    }
  }
  for (const item of definition.classes) {
    if (item.parent && !classes.has(item.parent)) throw new Error(`class ${item.class_id} has unknown parent ${item.parent}`)
    const lineage = new Set<string>()
    let current: string | undefined = item.class_id
    while (current) {
      if (lineage.has(current)) throw new Error(`ontology class hierarchy contains a cycle at ${current}`)
      lineage.add(current)
      current = classes.get(current)?.parent
    }
  }
}

export function validateOntologyGraph(
  definition: OntologyDefinition,
  graph: OntologyGraph,
  evidenceIds: ReadonlySet<string>,
): void {
  validateOntologyDefinition(definition)
  if (graph.ontology_id !== definition.ontology_id || graph.ontology_version !== definition.version) {
    throw new Error('ontology graph definition ID or version does not match the configured ontology')
  }
  const classes = new Map(definition.classes.map(item => [item.class_id, item]))
  const relationDefinitions = new Map(definition.relations.map(item => [item.relation_id, item]))
  const entities = new Map(graph.entities.map(item => [item.entity_id, item]))
  for (const entity of graph.entities) {
    const classDefinition = classes.get(entity.entity_type)
    if (!classDefinition) throw new Error(`unknown ontology class: ${entity.entity_type}`)
    if (classDefinition.abstract) throw new Error(`ontology entity cannot instantiate abstract class: ${entity.entity_type}`)
    if (classDefinition.value_set) {
      const values = definition.value_sets?.[classDefinition.value_set] ?? []
      if (!values.includes(entity.properties.value as string)) {
        throw new Error(`ontology entity ${entity.entity_id} value is not in value set ${classDefinition.value_set}`)
      }
    }
  }
  const isA = (classId: string, expected: string): boolean => {
    const seen = new Set<string>()
    let current: string | undefined = classId
    while (current && !seen.has(current)) {
      if (current === expected) return true
      seen.add(current)
      current = classes.get(current)?.parent
    }
    return false
  }
  for (const relation of graph.relations) {
    const relationDefinition = relationDefinitions.get(relation.predicate)
    if (!relationDefinition) throw new Error(`unknown ontology predicate: ${relation.predicate}`)
    const sourceType = entities.get(relation.source_id)?.entity_type
    const targetType = entities.get(relation.target_id)?.entity_type
    if (!sourceType || !targetType) throw new Error(`ontology relation ${relation.relation_id} has a dangling entity reference`)
    if (!relationDefinition.domain.some(classId => isA(sourceType, classId))) {
      throw new Error(`relation ${relation.relation_id} has invalid source type ${sourceType}`)
    }
    if (!relationDefinition.range.some(classId => isA(targetType, classId))) {
      throw new Error(`relation ${relation.relation_id} has invalid target type ${targetType}`)
    }
    if (relationDefinition.requires_evidence && relation.evidence_ids.length === 0) {
      throw new Error(`relation ${relation.relation_id} requires evidence`)
    }
    const supportingEvidence = new Set(relation.evidence_ids)
    for (const evidenceId of relation.contradicting_evidence_ids) {
      if (supportingEvidence.has(evidenceId)) {
        throw new Error(`relation ${relation.relation_id} cites evidence as both supporting and contradicting: ${evidenceId}`)
      }
    }
    for (const evidenceId of [...relation.evidence_ids, ...relation.contradicting_evidence_ids]) {
      if (!evidenceIds.has(evidenceId)) throw new Error(`relation ${relation.relation_id} references unknown evidence: ${evidenceId}`)
    }
  }
}

export function mergeWithStructuralGraph(
  definition: OntologyDefinition,
  fragment: OntologyGraph,
): OntologyGraph {
  if (fragment.ontology_id !== definition.ontology_id || fragment.ontology_version !== definition.version) {
    throw new Error('agent ontology fragment does not match the configured ontology definition')
  }
  const reservedIds = new Set<string>(CORE_ENTITY_IDS)
  const returnedReservedId = fragment.entities.find(entity => reservedIds.has(entity.entity_id))
  if (returnedReservedId) throw new Error(`agent cannot replace structural entity ${returnedReservedId.entity_id}`)
  return {
    ontology_id: definition.ontology_id,
    ontology_version: definition.version,
    entities: [
      { entity_id: 'root:patient', entity_type: 'Patient', label: 'Patient', properties: {} },
      { entity_id: 'root:encounter', entity_type: 'Encounter', label: 'Encounter', properties: {} },
      { entity_id: 'root:claim', entity_type: 'Claim', label: 'Submitted claim', properties: {} },
      ...fragment.entities,
    ],
    relations: [
      {
        relation_id: 'rel:patient-encounter',
        predicate: 'hasEncounter',
        source_id: 'root:patient',
        target_id: 'root:encounter',
        assertion_status: 'present',
        documentation_status: 'explicit',
        confidence: 1,
        evidence_ids: [],
        contradicting_evidence_ids: [],
      },
      {
        relation_id: 'rel:encounter-claim',
        predicate: 'hasClaim',
        source_id: 'root:encounter',
        target_id: 'root:claim',
        assertion_status: 'present',
        documentation_status: 'explicit',
        confidence: 1,
        evidence_ids: [],
        contradicting_evidence_ids: [],
      },
      ...fragment.relations,
    ],
  }
}

export function ontologyPromptContract(definition: OntologyDefinition): object {
  return {
    ontology_id: definition.ontology_id,
    ontology_version: definition.version,
    reserved_entity_ids: CORE_ENTITY_IDS,
    classes: definition.classes.map(({ class_id, parent, abstract, value_set }) => ({
      class_id,
      parent,
      abstract: abstract ?? false,
      value_set,
    })),
    relations: definition.relations.map(({ relation_id, domain, range, requires_evidence }) => ({
      relation_id,
      domain,
      range,
      requires_evidence,
    })),
    value_sets: definition.value_sets ?? {},
  }
}
