export type ViewId = 'overview' | 'queue' | 'case' | 'ingestion' | 'governance'

export type OpportunityStatus = 'Ready for review' | 'Needs documentation' | 'In review' | 'Cleared'
export type OpportunityType = 'Coding' | 'CDI' | 'Charge capture' | 'Compliance'

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
  impact: number
  age: string
  evidenceCount: number
  priority: 'High' | 'Medium' | 'Low'
}

export interface DemoStep {
  eyebrow: string
  title: string
  body: string
  proof: string
  view: ViewId
}
