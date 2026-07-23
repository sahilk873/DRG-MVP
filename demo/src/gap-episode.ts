// Deterministic, render-only projections of the clinical_care_gap showcase.
//
// This module derives the longitudinal episode timeline and the care-gap worklist rows
// PURELY from the already-validated review packet + automation plan (see review-packet.ts,
// automation-plan.ts). It invents no authoritative value: every date, size, gap field, and
// worklist metric is read straight from grounded packet evidence/assertions/findings or from
// the engine-computed plan.metrics.gap_worklist. The demo renders engine output — it must not
// synthesize clinical facts. Care-gap findings are human-review-only; nothing here routes or
// closes a gap.

import type { AutomationPlan, FindingAutomation } from './automation-plan'
import type { ReviewPacket } from './review-packet'

type Finding = ReviewPacket['findings'][number]
type Assertion = ReviewPacket['assertions'][number]
type Evidence = ReviewPacket['evidence'][number]

// A single dated assessment point on the DFU episode timeline. Derived from a WoundAssessment
// ontology entity joined to its dated wound assertion (length_cm/width_cm) and the evidence
// timestamp that grounds it.
export interface EpisodeTimelinePoint {
  assessmentId: string
  label: string
  recordedAt: string | null
  dayOffset: number | null
  lengthCm: number | null
  widthCm: number | null
  areaCm2: number | null
  // Area change vs. the immediately prior timeline point (null for the first point).
  areaDeltaPct: number | null
  // True when this point shows stalled/worsening healing (no reduction or growth vs prior).
  stalled: boolean
  // Evidence excerpt + source locator that grounds this point, if any.
  excerpt: string | null
  documentId: string | null
}

// A care-gap lane row: one open/routed/closed/exception gap with its grounded action fields.
export interface GapLaneItem {
  findingId: string
  ruleId: string
  title: string
  gapDomain: string | null
  alertUrgency: string | null
  expectedAction: string | null
  recommendedAction: string | null
  clinicalImpact: string | null
  timingWindowDays: number | null
  gapStatus: string | null
  barrierCode: string | null
  closedAt: string | null
  tier: FindingAutomation['tier'] | null
  queue: FindingAutomation['queue'] | null
  reasonCodes: string[]
  confidence: number
  // Grounding: the evidence excerpts (with locators) this gap finding cites.
  evidence: Array<{ evidenceId: string; text: string; recordedAt: string; locator: string }>
  exceptionChecks: Array<{ exceptionType: string; status: string; evidenceId: string }>
}

const MS_PER_DAY = 1000 * 60 * 60 * 24

function areaOf(length: number | null, width: number | null): number | null {
  if (length == null || width == null) return null
  return Math.round(length * width * 100) / 100
}

function numeric(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function evidenceLocator(evidence: Evidence): string {
  const locator = evidence.source_locator
  return locator.kind === 'structured_source_record'
    ? `${locator.path} · row ${locator.row_number}`
    : `${locator.document_id} · chars ${locator.char_start}–${locator.char_end}`
}

// Build the ordered episode timeline from WoundAssessment entities + dated assertions +
// evidence timestamps. Points are ordered by evidence date, and day offsets are measured from
// the earliest assessment. Area deltas and the stalled flag are computed deterministically.
export function buildEpisodeTimeline(packet: ReviewPacket): EpisodeTimelinePoint[] {
  const assertionBySubject = new Map<string, Assertion>()
  for (const assertion of packet.assertions) {
    if (assertion.concept === 'wound_assessment') assertionBySubject.set(assertion.subject_id, assertion)
  }
  const evidenceById = new Map<string, Evidence>(packet.evidence.map(item => [item.evidence_id, item]))

  const raw = packet.ontology.entities
    .filter(entity => entity.entity_type === 'WoundAssessment')
    .map(entity => {
      const assertion = assertionBySubject.get(entity.entity_id)
      const evidence = assertion?.evidence_ids
        .map(id => evidenceById.get(id))
        .find((item): item is Evidence => item != null)
      const lengthCm = assertion ? numeric(assertion.attributes.length_cm) : null
      const widthCm = assertion ? numeric(assertion.attributes.width_cm) : null
      return {
        assessmentId: entity.entity_id,
        label: entity.label,
        recordedAt: evidence?.recorded_at ?? null,
        lengthCm,
        widthCm,
        areaCm2: areaOf(lengthCm, widthCm),
        excerpt: evidence?.text ?? null,
        documentId: evidence?.document_id ?? null,
      }
    })
    .sort((a, b) => {
      if (a.recordedAt && b.recordedAt) return a.recordedAt.localeCompare(b.recordedAt)
      return a.assessmentId.localeCompare(b.assessmentId)
    })

  const firstDate = raw.find(item => item.recordedAt)?.recordedAt ?? null
  const baseMs = firstDate ? Date.parse(firstDate) : NaN

  return raw.map((item, index) => {
    const dayOffset = item.recordedAt && Number.isFinite(baseMs)
      ? Math.round((Date.parse(item.recordedAt) - baseMs) / MS_PER_DAY)
      : null
    const prior = raw[index - 1]
    const areaDeltaPct = prior?.areaCm2 != null && item.areaCm2 != null && prior.areaCm2 > 0
      ? Math.round(((item.areaCm2 - prior.areaCm2) / prior.areaCm2) * 1000) / 10
      : null
    // Stalled = area did not shrink versus the prior assessment (no healing or worsening).
    const stalled = prior?.areaCm2 != null && item.areaCm2 != null ? item.areaCm2 >= prior.areaCm2 : false
    return {
      assessmentId: item.assessmentId,
      label: item.label,
      recordedAt: item.recordedAt,
      dayOffset,
      lengthCm: item.lengthCm,
      widthCm: item.widthCm,
      areaCm2: item.areaCm2,
      areaDeltaPct,
      stalled,
      excerpt: item.excerpt,
      documentId: item.documentId,
    }
  })
}

// Project the care-gap findings into lane rows, joined to their automation-plan tier/queue.
// Highest priority first (mirrors plan.review_now ordering).
export function buildGapLane(packet: ReviewPacket, plan: AutomationPlan): GapLaneItem[] {
  const evidenceById = new Map<string, Evidence>(packet.evidence.map(item => [item.evidence_id, item]))
  const automationByFinding = new Map<string, FindingAutomation>(
    plan.findings.map(item => [item.finding_id, item]),
  )
  // Only clinical_care_gap findings carry gap fields; keep exactly those.
  const gapFindings = packet.findings.filter(finding => finding.gap_domain != null)

  const rows = gapFindings.map<GapLaneItem>(finding => {
    const automation = automationByFinding.get(finding.finding_id)
    return {
      findingId: finding.finding_id,
      ruleId: finding.rule_id,
      title: finding.title,
      gapDomain: finding.gap_domain ?? null,
      alertUrgency: finding.alert_urgency ?? null,
      expectedAction: finding.expected_action ?? null,
      recommendedAction: finding.recommended_action ?? null,
      clinicalImpact: finding.clinical_impact ?? null,
      timingWindowDays: finding.timing_window_days ?? null,
      gapStatus: finding.gap_status ?? null,
      barrierCode: finding.barrier_code ?? null,
      closedAt: finding.closed_at ?? null,
      tier: automation?.tier ?? null,
      queue: automation?.queue ?? null,
      reasonCodes: automation?.reason_codes ?? [],
      confidence: Math.round(finding.confidence * 100),
      evidence: finding.evidence_ids
        .map(id => evidenceById.get(id))
        .filter((item): item is Evidence => item != null)
        .map(item => ({
          evidenceId: item.evidence_id,
          text: item.text,
          recordedAt: item.recorded_at,
          locator: evidenceLocator(item),
        })),
      exceptionChecks: (finding.exception_checks ?? []).map(check => ({
        exceptionType: check.exception_type,
        status: check.status,
        evidenceId: check.evidence_id,
      })),
    }
  })

  const priorityByFinding = new Map<string, number>(
    plan.findings.map(item => [item.finding_id, item.priority_score]),
  )
  return rows.sort((a, b) => (priorityByFinding.get(b.findingId) ?? 0) - (priorityByFinding.get(a.findingId) ?? 0))
}

// The single highest-priority open/escalated gap — the episode's anchor finding for drilldown.
export function primaryGap(lane: GapLaneItem[]): GapLaneItem | null {
  return lane.find(item => item.gapStatus === 'open') ?? lane[0] ?? null
}

export function formatGapLabel(value: string | null): string {
  if (!value) return '—'
  return value.replaceAll('_', ' ').replace(/\b\w/g, character => character.toUpperCase())
}
