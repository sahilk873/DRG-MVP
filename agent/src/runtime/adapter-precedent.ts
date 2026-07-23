import type { AdapterDefinition, BulkProfile } from '../onboarding/schema.ts'
import type { Exemplar } from './retrieval.ts'

/** Feature tokens for a new onboarding task: every column name across the bounded profile. */
export function featuresFromProfile(profile: BulkProfile): string[] {
  const tokens: string[] = []
  for (const artifact of profile.artifacts ?? []) {
    for (const column of artifact.columns ?? []) tokens.push(column.name)
  }
  return tokens
}

/** The source columns an approved adapter binds — every `{"field": "..."}` it references. */
export function adapterFieldTokens(adapter: unknown): string[] {
  const tokens: string[] = []
  const walk = (node: unknown): void => {
    if (Array.isArray(node)) {
      node.forEach(walk)
      return
    }
    if (node && typeof node === 'object') {
      const record = node as Record<string, unknown>
      if (typeof record.field === 'string') tokens.push(record.field)
      for (const value of Object.values(record)) walk(value)
    }
  }
  walk(adapter)
  return tokens
}

/** Build a retrievable exemplar from a previously-approved adapter. */
export function exemplarFromAdapter(adapter: AdapterDefinition): Exemplar<AdapterDefinition> {
  return {
    id: `${adapter.adapter_id}@${adapter.version}`,
    features: adapterFieldTokens(adapter),
    payload: adapter,
  }
}
