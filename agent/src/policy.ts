import type { AgentExtraction, SourceBundle } from './schema.ts'

export interface ExtractionPolicy {
  maxDocuments: number
  maxDocumentCharacters: number
  maxTotalDocumentCharacters: number
  maxEvidenceItems: number
  maxEvidenceCharacters: number
  maxTotalEvidenceCharacters: number
  maxEntities: number
  maxRelations: number
  maxAssertions: number
}

export const DEFAULT_EXTRACTION_POLICY: Readonly<ExtractionPolicy> = Object.freeze({
  maxDocuments: 200,
  maxDocumentCharacters: 200_000,
  maxTotalDocumentCharacters: 1_000_000,
  maxEvidenceItems: 2_000,
  maxEvidenceCharacters: 2_000,
  maxTotalEvidenceCharacters: 250_000,
  maxEntities: 2_000,
  maxRelations: 5_000,
  maxAssertions: 2_000,
})

export function resolveExtractionPolicy(
  overrides: Partial<ExtractionPolicy> = {},
): Readonly<ExtractionPolicy> {
  const policy = { ...DEFAULT_EXTRACTION_POLICY, ...overrides }
  for (const [name, value] of Object.entries(policy)) {
    if (!Number.isSafeInteger(value) || value <= 0) {
      throw new Error(`extraction policy ${name} must be a positive safe integer`)
    }
  }
  return Object.freeze(policy)
}

export function validateRawSourceBundleLimits(
  input: unknown,
  policy: Readonly<ExtractionPolicy>,
): void {
  if (!isRecord(input) || !Array.isArray(input.documents)) return
  if (input.documents.length > policy.maxDocuments) {
    throw new Error(`source bundle exceeds maxDocuments (${policy.maxDocuments})`)
  }
  let totalCharacters = 0
  for (const document of input.documents) {
    if (!isRecord(document) || typeof document.text !== 'string') continue
    if (document.text.length > policy.maxDocumentCharacters) {
      throw new Error(`source document exceeds maxDocumentCharacters (${policy.maxDocumentCharacters})`)
    }
    totalCharacters += document.text.length
    if (totalCharacters > policy.maxTotalDocumentCharacters) {
      throw new Error(`source bundle exceeds maxTotalDocumentCharacters (${policy.maxTotalDocumentCharacters})`)
    }
  }
}

export function validateExtractionLimits(
  extraction: AgentExtraction,
  policy: Readonly<ExtractionPolicy>,
): void {
  if (extraction.evidence.length > policy.maxEvidenceItems) {
    throw new Error(`agent extraction exceeds maxEvidenceItems (${policy.maxEvidenceItems})`)
  }
  if (extraction.ontology.entities.length > policy.maxEntities) {
    throw new Error(`agent extraction exceeds maxEntities (${policy.maxEntities})`)
  }
  if (extraction.ontology.relations.length > policy.maxRelations) {
    throw new Error(`agent extraction exceeds maxRelations (${policy.maxRelations})`)
  }
  if (extraction.assertions.length > policy.maxAssertions) {
    throw new Error(`agent extraction exceeds maxAssertions (${policy.maxAssertions})`)
  }
  let totalEvidenceCharacters = 0
  for (const evidence of extraction.evidence) {
    if (evidence.text.length > policy.maxEvidenceCharacters) {
      throw new Error(`evidence excerpt exceeds maxEvidenceCharacters (${policy.maxEvidenceCharacters})`)
    }
    totalEvidenceCharacters += evidence.text.length
    if (totalEvidenceCharacters > policy.maxTotalEvidenceCharacters) {
      throw new Error(`agent extraction exceeds maxTotalEvidenceCharacters (${policy.maxTotalEvidenceCharacters})`)
    }
  }
}

export function policyPromptContract(policy: Readonly<ExtractionPolicy>): object {
  return {
    max_evidence_items: policy.maxEvidenceItems,
    max_evidence_characters: policy.maxEvidenceCharacters,
    max_total_evidence_characters: policy.maxTotalEvidenceCharacters,
    max_entities: policy.maxEntities,
    max_relations: policy.maxRelations,
    max_assertions: policy.maxAssertions,
  }
}

export function policyAuditRecord(policy: Readonly<ExtractionPolicy>): object {
  return {
    max_documents: policy.maxDocuments,
    max_document_characters: policy.maxDocumentCharacters,
    max_total_document_characters: policy.maxTotalDocumentCharacters,
    max_evidence_items: policy.maxEvidenceItems,
    max_evidence_characters: policy.maxEvidenceCharacters,
    max_total_evidence_characters: policy.maxTotalEvidenceCharacters,
    max_entities: policy.maxEntities,
    max_relations: policy.maxRelations,
    max_assertions: policy.maxAssertions,
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}
