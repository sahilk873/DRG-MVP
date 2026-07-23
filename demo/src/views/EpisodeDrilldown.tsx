import {
  ArrowLeft,
  ClipboardCheck,
  FileText,
  Link2,
  Microscope,
  ShieldCheck,
  Stethoscope,
  TrendingDown,
} from 'lucide-react'

import { EpisodeTimeline } from '../components/EpisodeTimeline'
import { gapEpisodeTimeline, gapLane, gapPrimary, gapReviewPacket } from '../data'
import { formatGapLabel, type GapLaneItem } from '../gap-episode'
import type { ViewId } from '../types'

interface EpisodeDrilldownProps {
  onNavigate: (view: ViewId) => void
}

// Episode drilldown for the diabetic-foot-ulcer episode. Mirrors the deck: a longitudinal
// size-trend timeline plus the anchor gap's Evidence → Expected → Actual → Impact →
// Recommended chain, every row backed by the grounded evidence excerpt + source locator that
// already lives in the packet. No claim is touched; a clinician owns the decision.
export function EpisodeDrilldown({ onNavigate }: EpisodeDrilldownProps) {
  const anchor = gapPrimary
  const packet = gapReviewPacket
  // The stalled-healing window is anchored on the gap's subject assessments.
  const anchorFinding = packet.findings.find(finding => finding.finding_id === anchor?.findingId)
  const highlightIds = anchorFinding?.subject_ids ?? []
  const groundingEvidence = anchor?.evidence[0] ?? null

  return (
    <>
      <button className="back-link" onClick={() => onNavigate('care_gaps')} type="button"><ArrowLeft size={15} /> Care gaps</button>
      <header className="case-header">
        <div>
          <div className="case-header__meta">
            <span className="status-label status-label--ready-for-review"><i />Analytics-only</span>
            <span>{packet.case.case_id}</span>
            <span>{packet.case.encounter_id}</span>
          </div>
          <h1>Diabetic foot ulcer episode</h1>
          <p>{formatGapLabel(String(packet.case.metadata.wound_type))} · admitted {formatDate(packet.case.admitted_at)} · {gapEpisodeTimeline.length} dated assessments</p>
        </div>
      </header>

      <div className="trust-strip" role="note" aria-label="Care-gap safety guarantees">
        <ShieldCheck size={15} />
        <span className={packet.controls.claim_mutation_allowed ? 'trust-flag trust-flag--warn' : 'trust-flag trust-flag--safe'}>
          Claim mutation: <strong>{packet.controls.claim_mutation_allowed ? 'ALLOWED' : 'BLOCKED'}</strong>
        </span>
        <span className="trust-flag trust-flag--safe">Clinician review: <strong>REQUIRED</strong></span>
        <span className="trust-flag">Care-gap route: <strong>gap-closure lane only</strong></span>
      </div>

      <section className="section-card episode-card">
        <div className="section-heading">
          <div><span className="panel-kicker">Longitudinal episode</span><h2>Wound size over the episode</h2></div>
          <span className="evidence-complete"><TrendingDown size={15} /> Stalled healing detected</span>
        </div>
        <p className="episode-intro">
          The ulcer failed to shrink between Day 7 and Day 14 despite standard care, then enlarged with signs of infection by Day 28.
          Each point is grounded in a dated wound assessment from the chart.
        </p>
        <EpisodeTimeline points={gapEpisodeTimeline} highlightAssessmentIds={highlightIds} />
      </section>

      {anchor && (
        <section className="section-card episode-chain-card">
          <div className="section-heading">
            <div><span className="panel-kicker">{anchor.ruleId} · {formatGapLabel(anchor.gapDomain)}</span><h2>Why this gap was surfaced</h2></div>
            <span className={`gap-urgency gap-urgency--${anchor.alertUrgency ?? 'routine'}`}>{formatGapLabel(anchor.alertUrgency)} alert</span>
          </div>
          <div className="episode-chain">
            <ChainRow icon={Microscope} label="Evidence" body={groundingEvidence ? `“${groundingEvidence.text}”` : anchor.title} source={groundingEvidence ? `${groundingEvidence.locator} · ${formatDateTime(groundingEvidence.recordedAt)}` : null} />
            <ChainRow icon={ClipboardCheck} label="Expected action" body={formatGapLabel(anchor.expectedAction)} source={anchor.timingWindowDays != null ? `within ${anchor.timingWindowDays} day${anchor.timingWindowDays === 1 ? '' : 's'} of the stalled assessment` : null} />
            <ChainRow icon={FileText} label="Actual action" body={anchor.gapStatus === 'exception' ? 'Documented exception on file' : 'None documented in the window'} source={anchor.gapStatus === 'exception' ? anchor.exceptionChecks.map(check => formatGapLabel(check.exceptionType)).join(', ') : 'no offsetting order or note found'} />
            <ChainRow icon={TrendingDown} label="Clinical impact" body={anchor.clinicalImpact ?? '—'} source={null} />
            <ChainRow icon={Stethoscope} label="Recommended next step" body={anchor.recommendedAction ?? '—'} source="clinician decides — analytics do not act" highlight />
          </div>
        </section>
      )}

      <section className="section-card episode-grounding-card">
        <div className="section-heading">
          <div><span className="panel-kicker">Source-grounded record</span><h2>Assessment evidence</h2></div>
          <span className="evidence-complete"><ShieldCheck size={15} /> Lineage verified</span>
        </div>
        <div className="episode-evidence-list">
          {packet.evidence.map(item => {
            const locator = item.source_locator
            const deepLink = locator.kind === 'structured_source_record'
              ? `${locator.path} · row ${locator.row_number}`
              : `${locator.document_id} · chars ${locator.char_start}–${locator.char_end}`
            return (
              <article className="evidence-item" key={item.evidence_id}>
                <div className="evidence-item__icon"><FileText size={17} /></div>
                <div className="evidence-item__body">
                  <div className="evidence-item__meta"><strong>Clinical note excerpt</strong><span>{item.document_id}</span><time>{formatDateTime(item.recorded_at)}</time></div>
                  <blockquote>“{item.text}”</blockquote>
                  <div className="evidence-tags"><span className="evidence-tag">Exact excerpt</span><span className="evidence-tag">Grounded</span></div>
                </div>
                <button className="icon-button" type="button" title={`Deep link: ${deepLink}`} aria-label={`Open excerpt at ${deepLink}`}><Link2 size={16} /></button>
              </article>
            )
          })}
        </div>
      </section>

      <div className="care-gap-boundary" role="note">
        <ShieldCheck size={17} />
        <div>
          <strong>This is a clinical alert, not a claim action.</strong>
          <span>The care-gap finding is routed to the care team; the demo never alters a claim, DRG, or payment. {gapLane.length} gap{gapLane.length === 1 ? '' : 's'} on this episode.</span>
        </div>
      </div>
    </>
  )
}

function ChainRow({ icon: Icon, label, body, source, highlight = false }: { icon: typeof Microscope; label: string; body: string; source: string | null; highlight?: boolean }) {
  return (
    <div className={`episode-chain-row${highlight ? ' episode-chain-row--highlight' : ''}`}>
      <span className="episode-chain-row__label"><Icon size={14} /> {label}</span>
      <div className="episode-chain-row__body">
        <strong>{body}</strong>
        {source && <small>{source}</small>}
      </div>
    </div>
  )
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(new Date(value))
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }).format(new Date(value))
}
