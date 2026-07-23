import type { DemoStep, Opportunity } from './types'
import reviewPacketFixture from './fixtures/review-packet.json'
import reviewPacketRaw from './fixtures/review-packet.json?raw'
import automationPlanFixture from './fixtures/automation-plan.json'
import reviewPacketFixture2 from './fixtures/review-packet-2.json'
import reviewPacketRaw2 from './fixtures/review-packet-2.json?raw'
import automationPlanFixture2 from './fixtures/automation-plan-2.json'
import reviewPacketGapFixture from './fixtures/review-packet-gap.json'
import reviewPacketGapRaw from './fixtures/review-packet-gap.json?raw'
import automationPlanGapFixture from './fixtures/automation-plan-gap.json'
import evaluationMetricsFixture from './fixtures/evaluation-metrics.json'
import { parseReviewPacket, type ReviewPacket } from './review-packet'
import { parseAutomationPlan, type AutomationPlan } from './automation-plan'
import { parseEvaluationReport } from './evaluation'
import { buildEpisodeTimeline, buildGapLane, primaryGap } from './gap-episode'

function assertLinked(packet: ReviewPacket, plan: AutomationPlan): void {
  if (
    plan.packet.packet_id !== packet.packet_id
    || plan.packet.packet_hash !== packet.provenance.packet_hash
  ) throw new Error('The demo automation plan must reference the exact review packet')
}

export const primaryReviewPacket = parseReviewPacket(reviewPacketFixture)
export const primaryAutomationPlan = parseAutomationPlan(automationPlanFixture)
assertLinked(primaryReviewPacket, primaryAutomationPlan)

export const secondReviewPacket = parseReviewPacket(reviewPacketFixture2)
export const secondAutomationPlan = parseAutomationPlan(automationPlanFixture2)
assertLinked(secondReviewPacket, secondAutomationPlan)

// BOTH-LENS showcase: the shipped clinical_care_gap package against the synthetic
// diabetic-foot-ulcer episode. Same versioned contracts as revenue integrity (review packet
// 3.5.0 + automation plan 1.3.0), so it validates through the exact same parsers. The plan
// exercises every gap tier — an escalated urgent gap (CG-INF-002), a routine auto-routed gap
// (CG-DFU-001), and a confirmed-exception suppressed gap (CG-DFU-002) — with a non-empty
// gap_worklist. Views that render the episode timeline / care-gap lane land in C5; here we
// only prove the fixtures exist, validate, and stay hash-linked.
export const gapReviewPacket = parseReviewPacket(reviewPacketGapFixture)
export const gapAutomationPlan = parseAutomationPlan(automationPlanGapFixture)
assertLinked(gapReviewPacket, gapAutomationPlan)

// C5 render-only projections of the clinical_care_gap showcase (episode → detection →
// validation → routing → closure). Every field is derived from the validated gap packet/plan;
// no authoritative value is invented here. The worklist metrics are the engine-computed,
// illustrative gap operational rollup (is_estimate).
export const gapWorklist = gapAutomationPlan.metrics.gap_worklist
export const gapLane = buildGapLane(gapReviewPacket, gapAutomationPlan)
export const gapEpisodeTimeline = buildEpisodeTimeline(gapReviewPacket)
export const gapPrimary = primaryGap(gapLane)
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

const secondFinding = secondReviewPacket.findings[0]
const secondAutomationItem = secondAutomationPlan.findings.find(item => item.finding_id === secondFinding?.finding_id)
if (!secondFinding || !secondAutomationItem) throw new Error('The second demo artifact must contain a finding')

export const secondOpportunity: Opportunity = {
  id: secondFinding.finding_id,
  patientId: secondReviewPacket.case.patient_id,
  encounterId: secondReviewPacket.case.encounter_id,
  facility: 'Demo Hospital',
  serviceLine: 'Wound care',
  type: 'Coding',
  title: 'Hospital-acquired stage 4 pressure injury absent from claim',
  summary: secondFinding.rationale,
  status: 'Ready for review',
  confidence: Math.round(secondFinding.confidence * 100),
  currentDrg: secondFinding.current_drg,
  simulatedDrg: secondFinding.simulated_drg,
  impact: secondFinding.estimated_impact_cents == null ? null : secondFinding.estimated_impact_cents / 100,
  age: '27 min',
  evidenceCount: secondReviewPacket.evidence.length,
  priority: 'High',
  automationOutcome: 'human_exception',
  automationTier: secondAutomationItem.tier,
  estimatedReviewSeconds: secondAutomationItem.estimated_review_seconds,
  relatedFindingIds: secondAutomationItem.related_finding_ids,
  packetBacked: true,
}

// Registry of packet-backed cases the reviewer UI can open, keyed by opportunity id.
export interface PacketCase {
  packet: ReviewPacket
  plan: AutomationPlan
  // Raw fixture text (number tokens preserved) used for in-browser hash re-verification.
  packetRaw: string
}
// Stable id under which the clinical_care_gap showcase packet is registered. The escalated
// urgent gap (CG-INF-002) is the highest-priority gap finding and anchors the case.
export const gapShowcaseId = 'CASE-DFU-EPISODE-001'
export const packetCases: Record<string, PacketCase> = {
  [primaryOpportunity.id]: { packet: primaryReviewPacket, plan: primaryAutomationPlan, packetRaw: reviewPacketRaw },
  [secondOpportunity.id]: { packet: secondReviewPacket, plan: secondAutomationPlan, packetRaw: reviewPacketRaw2 },
  [gapShowcaseId]: { packet: gapReviewPacket, plan: gapAutomationPlan, packetRaw: reviewPacketGapRaw },
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
  secondOpportunity,
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

// Deterministic, hash-backed figures projected straight from the engine-generated
// review packet + automation plan. These are reproducible from the packet hash and are
// the honest source for the demo's headline impact/effort numbers.
export const impactSummary = primaryReviewPacket.impact_summary
export const reviewerEffort = primaryAutomationPlan.metrics.reviewer_effort
export const packetHashShort = primaryReviewPacket.provenance.packet_hash.slice(0, 12)
export const engineVersion = primaryReviewPacket.provenance.engine_version

// Signed accuracy backtest produced by the same harness as `make eval`.
export const evaluationReport = parseEvaluationReport(evaluationMetricsFixture)

// Illustrative portfolio projection for the pitch narrative (NOT engine-derived). The
// noTouchRate here is a demo aggregate; the deterministic per-case rate lives in
// reviewerEffort.no_touch_rate above.
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
    body: 'Governed rules — driven entirely by the ontology and its POA and DRG-severity concepts — compare what the chart supports with what was documented, coded, grouped, and charged, including unsupported capture and present-on-admission risk.',
    proof: 'Two governed rule packages; POA and severity are first-class',
    view: 'case',
  },
  {
    eyebrow: '04 · Simulate',
    title: 'Quantify the effect without letting the model touch payment logic.',
    body: 'A deterministic, versioned grouper boundary reproduces the current claim and simulates the reviewed candidate, and shows a step-by-step derivation trace — severity, tier, pricing — so a reviewer sees exactly why the DRG changed.',
    proof: 'Hash-covered derivation trace on every finding',
    view: 'case',
  },
  {
    eyebrow: '05 · Review',
    title: 'Ask a person one narrow question, then learn from the outcome.',
    body: 'The system suppresses duplicates, routes routine work, drafts the next action, and leaves only the unresolved coding or compliance judgment for a qualified reviewer — behind an always-visible boundary where the model can never mutate a claim, DRG, or payment.',
    proof: 'Claim mutation architecturally blocked; human authorization required',
    view: 'case',
  },
]
