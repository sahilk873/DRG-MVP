import { createHash } from 'node:crypto'

import type { OntologyDefinition } from './schema.ts'

/**
 * Deterministic ontology-subgraph retrieval.
 *
 * The encounter extractor does not need the full ontology contract (all 49 classes, every
 * relation and value-set) inlined into every prompt. That is a large, mostly-irrelevant
 * token cost paid on every call. `selectOntologySubgraph` deterministically selects only
 * the classes plausibly relevant to a document term set (plus their full ancestor chain,
 * the relations whose endpoints are selected, and the value-sets those classes reference)
 * and returns a stable selection digest.
 *
 * This is a PROMPT HINT only. It never changes what the deterministic validators accept:
 * `validateOntologyGraph` still runs against the full definition, so retrieval can never
 * widen what validates — only shrink what the model is shown. Fewer tokens per call, and
 * a tighter, more relevant contract tends to raise extraction precision too.
 */

export interface OntologySelection {
  classIds: string[]
  relationIds: string[]
  valueSetIds: string[]
  selectionDigest: string
}

function tokenize(value: string): string[] {
  return value.toLowerCase().split(/[^a-z0-9]+/).filter(token => token.length >= 3)
}

function classTokens(definition: OntologyDefinition): Map<string, Set<string>> {
  const byClass = new Map<string, Set<string>>()
  const valueSets = definition.value_sets ?? {}
  for (const cls of definition.classes) {
    const tokens = new Set<string>([...tokenize(cls.class_id), ...tokenize(cls.label)])
    if (cls.value_set) {
      for (const member of valueSets[cls.value_set] ?? []) {
        for (const token of tokenize(String(member))) tokens.add(token)
      }
    }
    byClass.set(cls.class_id, tokens)
  }
  return byClass
}

export function selectOntologySubgraph(
  definition: OntologyDefinition,
  terms: Iterable<string>,
): OntologySelection {
  const classes = new Map(definition.classes.map(cls => [cls.class_id, cls]))
  const wanted = new Set<string>()
  for (const term of terms) for (const token of tokenize(term)) wanted.add(token)

  const selected = new Set<string>()
  const addWithAncestors = (classId: string): void => {
    let current: string | undefined = classId
    const guard = new Set<string>()
    while (current && classes.has(current) && !guard.has(current)) {
      guard.add(current)
      selected.add(current)
      current = classes.get(current)?.parent
    }
  }

  const tokensByClass = classTokens(definition)
  for (const [classId, tokens] of tokensByClass) {
    for (const token of tokens) {
      if (wanted.has(token)) { addWithAncestors(classId); break }
    }
  }

  // Relations whose domain AND range each intersect the selected classes are usable by the
  // model; include them (their endpoint classes are already selected, so the slice stays closed).
  const relationIds: string[] = []
  for (const relation of definition.relations) {
    const domainHit = relation.domain.some(classId => selected.has(classId))
    const rangeHit = relation.range.some(classId => selected.has(classId))
    if (domainHit && rangeHit) relationIds.push(relation.relation_id)
  }

  // Only the value-sets referenced by selected classes.
  const valueSetIds = new Set<string>()
  for (const classId of selected) {
    const valueSet = classes.get(classId)?.value_set
    if (valueSet) valueSetIds.add(valueSet)
  }

  const selection = {
    classIds: [...selected].sort(),
    relationIds: [...relationIds].sort(),
    valueSetIds: [...valueSetIds].sort(),
  }
  const selectionDigest = createHash('sha256')
    .update(JSON.stringify(selection))
    .digest('hex')
  return { ...selection, selectionDigest }
}

/**
 * Project the ontology prompt contract down to a selected slice. Shape matches
 * `ontologyPromptContract` so it is a drop-in for the extractor's `ontology_contract`.
 * Reserved structural entity IDs are always retained.
 */
export function projectOntologyContract(
  definition: OntologyDefinition,
  selection: OntologySelection,
  ontologyDigest: string,
): object {
  const classIds = new Set(selection.classIds)
  const relationIds = new Set(selection.relationIds)
  const valueSetIds = new Set(selection.valueSetIds)
  const valueSets = definition.value_sets ?? {}
  return {
    ontology_id: definition.ontology_id,
    ontology_version: definition.version,
    ontology_digest: ontologyDigest,
    selection_digest: selection.selectionDigest,
    reserved_entity_ids: definition.structural_graph.entities.map(entity => entity.entity_id),
    classes: definition.classes
      .filter(cls => classIds.has(cls.class_id))
      .map(({ class_id, parent, abstract, value_set }) => ({ class_id, parent, abstract: abstract ?? false, value_set })),
    relations: definition.relations
      .filter(relation => relationIds.has(relation.relation_id))
      .map(({ relation_id, domain, range, requires_evidence }) => ({ relation_id, domain, range, requires_evidence })),
    value_sets: Object.fromEntries(Object.entries(valueSets).filter(([key]) => valueSetIds.has(key))),
  }
}

/** Rough token estimate (~4 chars/token) for measuring prompt-contract savings. */
export function estimateTokens(value: unknown): number {
  return Math.ceil(JSON.stringify(value).length / 4)
}
