import { createHash } from 'node:crypto'

/**
 * Deterministic retrieval-augmented context selection for the agents (TS mirror of the Python
 * `runtime.retrieval`). Given a task's feature tokens, return the top-k most relevant prior
 * exemplars by transparent Jaccard overlap with a content-hash tiebreak — fully reproducible.
 * Used so agents receive a small, relevant set of precedents (RAG) instead of everything, cutting
 * prompt tokens and raising accuracy.
 */

export interface Exemplar<T = unknown> {
  id: string
  features: string[]
  payload: T
}

export interface RetrievalResult<T = unknown> {
  exemplars: Exemplar<T>[]
  scores: number[]
  scanned: number
  digest: string
}

export function normalizeFeatures(features: Iterable<string>): string[] {
  const tokens = new Set<string>()
  for (const feature of features) {
    if (typeof feature === 'string' && feature.trim()) tokens.add(feature.trim().toLowerCase())
  }
  return [...tokens].sort()
}

function contentHash<T>(exemplar: Exemplar<T>): string {
  return createHash('sha256')
    .update(JSON.stringify({ features: normalizeFeatures(exemplar.features), payload: exemplar.payload }))
    .digest('hex')
}

export function retrieve<T>(
  queryFeatures: string[],
  exemplars: Exemplar<T>[],
  options: { k?: number; minOverlap?: number } = {},
): RetrievalResult<T> {
  const k = options.k ?? 5
  const minOverlap = options.minOverlap ?? 1
  if (!Number.isInteger(k) || k <= 0) throw new Error('k must be a positive integer')
  const query = new Set(normalizeFeatures(queryFeatures))

  const scored = exemplars
    .map(exemplar => {
      const features = new Set(normalizeFeatures(exemplar.features))
      let intersection = 0
      for (const token of query) if (features.has(token)) intersection += 1
      const union = new Set([...query, ...features]).size
      return { exemplar, score: union ? intersection / union : 0, intersection, hash: contentHash(exemplar) }
    })
    .filter(item => item.intersection >= minOverlap)
    // Highest score first; ties broken deterministically by content hash.
    .sort((left, right) => (right.score - left.score) || left.hash.localeCompare(right.hash))

  const top = scored.slice(0, k)
  const digest = createHash('sha256')
    .update(JSON.stringify({ query: [...query].sort(), k, minOverlap, results: top.map(item => item.hash) }))
    .digest('hex')
  return {
    exemplars: top.map(item => item.exemplar),
    scores: top.map(item => Number(item.score.toFixed(6))),
    scanned: exemplars.length,
    digest,
  }
}
