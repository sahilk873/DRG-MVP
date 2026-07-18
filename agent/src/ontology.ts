import { createHash } from 'node:crypto'

import woundCareOntology from '../../src/revenue_integrity/data/wound_care_ontology_v1.json' with { type: 'json' }

import {
  ontologyDefinitionSchema,
  type OntologyDefinition,
  type OntologyGraph,
} from './schema.ts'

export const DEFAULT_ONTOLOGY_DEFINITION = ontologyDefinitionSchema.parse(woundCareOntology)
validateOntologyDefinition(DEFAULT_ONTOLOGY_DEFINITION)

export function ontologyDigest(definition: OntologyDefinition): string {
  const material = {
    ontology_id: definition.ontology_id,
    version: definition.version,
    status: definition.status,
    structural_graph: {
      entities: [...definition.structural_graph.entities]
        .sort((left, right) => left.entity_id.localeCompare(right.entity_id))
        .map(item => ({
          entity_id: item.entity_id,
          entity_type: item.entity_type,
          label: item.label,
          concept: item.concept ?? null,
          properties: item.properties,
        })),
      relations: [...definition.structural_graph.relations]
        .sort((left, right) => left.relation_id.localeCompare(right.relation_id))
        .map(item => ({
          relation_id: item.relation_id,
          predicate: item.predicate,
          source_id: item.source_id,
          target_id: item.target_id,
          assertion_status: item.assertion_status,
          documentation_status: item.documentation_status,
          confidence: String(item.confidence),
          evidence_ids: [...item.evidence_ids].sort(),
          contradicting_evidence_ids: [...item.contradicting_evidence_ids].sort(),
        })),
    },
    classes: [...definition.classes]
      .sort((left, right) => left.class_id.localeCompare(right.class_id))
      .map(item => ({
        class_id: item.class_id,
        label: item.label,
        parent: item.parent ?? null,
        abstract: item.abstract ?? false,
        value_set: item.value_set ?? null,
      })),
    relations: [...definition.relations]
      .sort((left, right) => left.relation_id.localeCompare(right.relation_id))
      .map(item => ({
        relation_id: item.relation_id,
        domain: [...item.domain].sort(),
        range: [...item.range].sort(),
        requires_evidence: item.requires_evidence,
      })),
    value_sets: Object.fromEntries(
      Object.entries(definition.value_sets ?? {})
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, values]) => [key, [...values].sort()]),
    ),
  }
  return createHash('sha256').update(stableStringify(material)).digest('hex')
}

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
  validateOntologyGraphSemantics(definition, {
    ontology_id: definition.ontology_id,
    ontology_version: definition.version,
    ontology_digest: ontologyDigest(definition),
    ...definition.structural_graph,
  }, new Set())
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
  if (graph.ontology_digest !== ontologyDigest(definition)) {
    throw new Error('ontology graph digest does not match the configured ontology definition')
  }
  validateOntologyGraphSemantics(definition, graph, evidenceIds)
}

function validateOntologyGraphSemantics(
  definition: OntologyDefinition,
  graph: OntologyGraph,
  evidenceIds: ReadonlySet<string>,
): void {
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
  ...fragments: OntologyGraph[]
): OntologyGraph {
  const digest = ontologyDigest(definition)
  const reservedIds = new Set(definition.structural_graph.entities.map(entity => entity.entity_id))
  const entityIds = new Set(reservedIds)
  const relationIds = new Set(definition.structural_graph.relations.map(relation => relation.relation_id))
  for (const fragment of fragments) {
    if (
      fragment.ontology_id !== definition.ontology_id
      || fragment.ontology_version !== definition.version
      || fragment.ontology_digest !== digest
    ) {
      throw new Error('ontology fragment does not match the configured ontology definition')
    }
    for (const entity of fragment.entities) {
      if (reservedIds.has(entity.entity_id)) {
        throw new Error(`ontology fragment cannot replace structural entity ${entity.entity_id}`)
      }
      if (entityIds.has(entity.entity_id)) throw new Error(`ontology fragments contain duplicate entity ${entity.entity_id}`)
      entityIds.add(entity.entity_id)
    }
    for (const relation of fragment.relations) {
      if (relationIds.has(relation.relation_id)) throw new Error(`ontology fragments contain duplicate relation ${relation.relation_id}`)
      relationIds.add(relation.relation_id)
    }
  }
  return {
    ontology_id: definition.ontology_id,
    ontology_version: definition.version,
    ontology_digest: digest,
    entities: [...definition.structural_graph.entities, ...fragments.flatMap(fragment => fragment.entities)],
    relations: [...definition.structural_graph.relations, ...fragments.flatMap(fragment => fragment.relations)],
  }
}

export function ontologyPromptContract(definition: OntologyDefinition): object {
  return {
    ontology_id: definition.ontology_id,
    ontology_version: definition.version,
    ontology_digest: ontologyDigest(definition),
    reserved_entity_ids: definition.structural_graph.entities.map(entity => entity.entity_id),
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

function stableStringify(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`
  if (value !== null && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${stableStringify(item)}`)
    return `{${entries.join(',')}}`
  }
  return JSON.stringify(value)
}
