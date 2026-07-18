import type { DemoStep, Opportunity } from './types'
import reviewPacketFixture from './fixtures/review-packet.json'
import automationPlanFixture from './fixtures/automation-plan.json'
import { parseReviewPacket } from './review-packet'
import { parseAutomationPlan } from './automation-plan'

export const primaryReviewPacket = parseReviewPacket(reviewPacketFixture)
export const primaryAutomationPlan = parseAutomationPlan(automationPlanFixture)
if (
  primaryAutomationPlan.packet.packet_id !== primaryReviewPacket.packet_id
  || primaryAutomationPlan.packet.packet_hash !== primaryReviewPacket.provenance.packet_hash
) throw new Error('The demo automation plan must reference the exact review packet')
const primaryFinding = primaryReviewPacket.findings[0]
const primaryAutomation = primaryAutomationPlan.findings[0]
if (!primaryFinding || !primaryAutomation) throw new Error('The demo artifacts must contain a finding')

export const primaryOpportunity: Opportunity = {
  id: primaryFinding.finding_id,
  patientId: primaryReviewPacket.case.patient_id,
  encounterId: primaryReviewPacket.case.encounter_id,
  facility: 'Demo Hospital',
  serviceLine: 'Wound care',
  type: 'Coding',
  title: primaryFinding.title,
  summary: primaryFinding.rationale,
  status: 'Ready for review',
  confidence: Math.round(primaryFinding.confidence * 100),
  currentDrg: primaryFinding.current_drg,
  simulatedDrg: primaryFinding.simulated_drg,
  impact: primaryFinding.estimated_impact_cents == null
    ? null
    : primaryFinding.estimated_impact_cents / 100,
  age: '12 min',
  evidenceCount: primaryReviewPacket.evidence.length,
  priority: 'High',
  automationOutcome: 'human_exception',
  automationTier: primaryAutomation.tier,
  estimatedReviewSeconds: primaryAutomation.estimated_review_seconds,
  relatedFindingIds: primaryAutomation.related_finding_ids,
  packetBacked: true,
}

export const primaryInvestigation = {
  clinicalPass: {
    status: 'Complete', title: 'Billing-blind clinical reconstruction',
    detail: 'A stage 4 sacral pressure injury is grounded in the wound assessment before the system compares diagnosis, charge, or DRG data.',
    evidence: 'EV-001 · exact nurse assessment excerpt',
  },
  reconciliation: {
    status: 'Candidate', title: 'Clinical–financial mismatch',
    detail: 'The reconstructed diagnosis is absent from the submitted claim snapshot. It remains a hypothesis until deterministic validation completes.',
    evidence: 'Candidate diagnosis · L89.154',
  },
  critic: {
    status: 'Supported', title: 'Counterevidence review',
    detail: 'The critic found no conflicting source in this synthetic encounter. Contradictions would escalate rather than auto-route the finding.',
    evidence: 'No counterevidence emitted',
  },
  validation: {
    status: 'Verified', title: 'Deterministic simulation',
    detail: 'The validator preserves evidence lineage and simulates the candidate through the configured demo grouper without changing the source claim.',
    evidence: `DRG ${primaryOpportunity.currentDrg} → ${primaryOpportunity.simulatedDrg}`,
  },
} as const

export const opportunities: Opportunity[] = [
  primaryOpportunity,
  {
    id: 'OPP-10479',
    patientId: 'PAT-ALPHA-014',
    encounterId: 'ENC-ALPHA-014',
    facility: 'Alpha Medical Center',
    serviceLine: 'Wound care',
    type: 'CDI',
    title: 'Ulcer depth requires physician clarification',
    summary: 'Nursing documentation conflicts with the physician note on exposed tissue depth.',
    status: 'Needs documentation',
    confidence: 86,
    currentDrg: '603',
    simulatedDrg: '603',
    impact: 0,
    age: '34 min',
    evidenceCount: 7,
    priority: 'High',
    automationOutcome: 'human_exception', automationTier: 'escalated', estimatedReviewSeconds: 240,
    relatedFindingIds: ['finding-query-source-2'], packetBacked: false,
  },
  {
    id: 'OPP-10476',
    patientId: 'PAT-ALPHA-021',
    encounterId: 'ENC-ALPHA-021',
    facility: 'North Pavilion',
    serviceLine: 'Surgery',
    type: 'Charge capture',
    title: 'Negative-pressure therapy charge mismatch',
    summary: 'Procedure documentation is present; the corresponding therapy charge is not in the claim extract.',
    status: 'Ready for review',
    confidence: 94,
    currentDrg: '856',
    simulatedDrg: '856',
    impact: 1675,
    age: '1 hr',
    evidenceCount: 3,
    priority: 'Medium',
    automationOutcome: 'auto_routed', automationTier: 'auto_routed', estimatedReviewSeconds: 0,
    relatedFindingIds: [], packetBacked: false,
  },
  {
    id: 'OPP-10470',
    patientId: 'PAT-ALPHA-032',
    encounterId: 'ENC-ALPHA-032',
    facility: 'Alpha Medical Center',
    serviceLine: 'Vascular',
    type: 'Coding',
    title: 'Debridement depth differs from procedure code',
    summary: 'Operative note documents excisional debridement through fascia; coded depth is less specific.',
    status: 'In review',
    confidence: 91,
    currentDrg: '264',
    simulatedDrg: '263',
    impact: 3960,
    age: '2 hr',
    evidenceCount: 5,
    priority: 'Medium',
    automationOutcome: 'auto_routed', automationTier: 'auto_routed', estimatedReviewSeconds: 0,
    relatedFindingIds: [], packetBacked: false,
  },
  {
    id: 'OPP-10461',
    patientId: 'PAT-ALPHA-046',
    encounterId: 'ENC-ALPHA-046',
    facility: 'North Pavilion',
    serviceLine: 'Wound care',
    type: 'Compliance',
    title: 'POA status conflicts across source documents',
    summary: 'Admission skin assessment and later progress note contain contradictory present-on-admission status.',
    status: 'Needs documentation',
    confidence: 79,
    currentDrg: '592',
    simulatedDrg: '592',
    impact: 0,
    age: '4 hr',
    evidenceCount: 6,
    priority: 'High',
    automationOutcome: 'human_exception', automationTier: 'escalated', estimatedReviewSeconds: 240,
    relatedFindingIds: [], packetBacked: false,
  },
  {
    id: 'OPP-10461-DUP', patientId: 'PAT-ALPHA-046', encounterId: 'ENC-ALPHA-046',
    facility: 'North Pavilion', serviceLine: 'Wound care', type: 'Compliance',
    title: 'Duplicate POA conflict consolidated into OPP-10461',
    summary: 'The same semantic discrepancy was emitted by a second source rule and consolidated without double-counting impact.',
    status: 'Cleared', confidence: 79, currentDrg: '592', simulatedDrg: '592', impact: 0,
    age: '4 hr', evidenceCount: 2, priority: 'Low', automationOutcome: 'suppressed',
    automationTier: 'suppressed', estimatedReviewSeconds: 0, relatedFindingIds: [], packetBacked: false,
  },
]

export const humanOpportunities = opportunities.filter(item => item.automationOutcome === 'human_exception')
export const automationSummary = {
  scanned: 183,
  clean: 174,
  autoRouted: 4,
  suppressed: 2,
  human: 3,
  noTouchRate: 98.4,
} as const

export const tourSteps: DemoStep[] = [
  {
    eyebrow: '01 · Connect',
    title: 'Meet providers where their data already lives.',
    body: 'Encounter profiles a deidentified bulk export, then a Mastra agent proposes a versioned mapping. The full dataset stays in the deterministic data plane.',
    proof: 'CSV, JSON, JSONL, and XLSX supported today',
    view: 'ingestion',
  },
  {
    eyebrow: '02 · Reconstruct',
    title: 'Turn fragmented records into one clinical encounter.',
    body: 'Structured fields and narrative notes become an evidence-linked patient graph with explicit uncertainty, conflict, and source lineage.',
    proof: 'Every fact resolves to a document excerpt or source row',
    view: 'case',
  },
  {
    eyebrow: '03 · Reconcile',
    title: 'Find mismatches, not just high-value codes.',
    body: 'Governed rules compare what the chart supports with what was documented, coded, grouped, and charged—including unsupported capture and POA risk.',
    proof: 'Bi-directional revenue integrity by design',
    view: 'case',
  },
  {
    eyebrow: '04 · Simulate',
    title: 'Quantify the effect without letting the model touch payment logic.',
    body: 'A deterministic grouper boundary reproduces the current claim and simulates the reviewed candidate under the correct payer and effective date.',
    proof: '$8,420 deterministic demo impact on this encounter',
    view: 'case',
  },
  {
    eyebrow: '05 · Review',
    title: 'Ask a person one narrow question, then learn from the outcome.',
    body: 'The system suppresses duplicates, routes routine work, drafts the next action, and leaves only the unresolved coding or compliance judgment for a qualified reviewer.',
    proof: '3 of 183 synthetic encounters need a person',
    view: 'case',
  },
]
