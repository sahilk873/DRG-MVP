export type ViewId = 'overview' | 'queue' | 'case' | 'ingestion' | 'governance' | 'care_gaps' | 'episode'

export type OpportunityStatus = 'Ready for review' | 'Needs documentation' | 'In review' | 'Cleared'
export type OpportunityType = 'Coding' | 'CDI' | 'Charge capture' | 'Compliance'
export type AutomationOutcome = 'human_exception' | 'auto_routed' | 'suppressed' | 'needs_enrichment'

export interface Opportunity {
  id: string
  patientId: string
  encounterId: string
  facility: string
  serviceLine: string
  type: OpportunityType
  title: string
  summary: string
  status: OpportunityStatus
  confidence: number
  currentDrg: string
  simulatedDrg: string
  impact: number | null
  age: string
  evidenceCount: number
  priority: 'High' | 'Medium' | 'Low'
  automationOutcome: AutomationOutcome
  automationTier: 'quick_confirm' | 'focused_review' | 'escalated' | 'auto_routed' | 'suppressed' | 'needs_enrichment'
  estimatedReviewSeconds: number
  relatedFindingIds: string[]
  packetBacked: boolean
}

export interface DemoStep {
  eyebrow: string
  title: string
  body: string
  proof: string
  view: ViewId
}
