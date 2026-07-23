import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CalendarClock,
  CheckCircle2,
  ClipboardList,
  HeartPulse,
  ShieldCheck,
  Stethoscope,
  Timer,
} from 'lucide-react'

import { gapLane, gapReviewPacket, gapWorklist } from '../data'
import { formatGapLabel, type GapLaneItem } from '../gap-episode'
import type { ViewId } from '../types'

interface CareGapsProps {
  onNavigate: (view: ViewId) => void
  onOpenEpisode: () => void
}

// Clinical Care Gaps lens. Same product, second lens: the deterministic engine also identifies
// gaps in clinical follow-through (missing / delayed / incomplete action) from grounded
// evidence. This surface renders the engine's illustrative gap worklist (open high-risk gaps,
// avg delay, top alert reason) and closure performance (closed %, median time, top barrier),
// then lists each gap in a care-gap lane. Gaps are HUMAN-REVIEW-ONLY: a clinician decides;
// routing/closure rides the separate gap-closure lane, never the revenue review workflow.
export function CareGaps({ onNavigate, onOpenEpisode }: CareGapsProps) {
  const closedPct = Math.round(gapWorklist.gaps_closed_pct * 100)
  return (
    <>
      <header className="view-header">
        <div>
          <span className="eyebrow">Clinical care gaps · second lens</span>
          <h1>Close the gaps between the plan and the care.</h1>
          <p>
            The same deterministic engine that reconciles the claim also finds where clinical follow-through stalled —
            missing, delayed, or incomplete action — grounded in the chart. A clinician always decides; the system only surfaces.
          </p>
        </div>
        <span className="governance-health"><HeartPulse size={18} /> Analytics-only · review required</span>
      </header>

      <section className="care-gap-dashboards">
        <div className="section-card care-gap-panel">
          <div className="section-heading">
            <div><span className="panel-kicker">Care gap dashboard</span><h2>Open worklist</h2></div>
            <span className="trend-chip" title="Operational rollup, illustrative — computed by the engine, not for clinical decision-making">illustrative</span>
          </div>
          <div className="care-gap-metrics">
            <GapMetric icon={AlertTriangle} tone="urgent" value={String(gapWorklist.open_high_risk_gaps)} label="High-risk open gaps" detail="need clinician attention" />
            <GapMetric icon={Timer} value={`${gapWorklist.avg_expected_window_days.toFixed(1)}d`} label="Avg expected window" detail="rule-configured action window" />
            <GapMetric icon={Activity} value={formatGapLabel(gapWorklist.top_alert_reason)} label="Top alert reason" detail={`across ${gapWorklist.total_gaps} gaps`} />
          </div>
          <p className="demo-caption">Illustrative operational metric ({gapWorklist.is_estimate ? 'estimate' : 'measured'}) · derived from the engine automation plan, not a clinical determination.</p>
        </div>

        <div className="section-card care-gap-panel">
          <div className="section-heading">
            <div><span className="panel-kicker">Closure performance</span><h2>Gap resolution</h2></div>
            <span className="trend-chip" title="Operational rollup, illustrative">illustrative</span>
          </div>
          <div className="care-gap-metrics">
            <GapMetric icon={CheckCircle2} tone="good" value={`${closedPct}%`} label="Gaps closed" detail="resolved or excepted" />
            <GapMetric icon={CalendarClock} value={`${gapWorklist.median_closure_days}d`} label="Median closed-gap window" detail="rule-configured window on closed gaps" />
            <GapMetric icon={ClipboardList} value={formatGapLabel(gapWorklist.top_barrier)} label="Top barrier" detail="documented closure blocker" />
          </div>
          <p className="demo-caption">Illustrative operational metric ({gapWorklist.is_estimate ? 'estimate' : 'measured'}) · closure decisions are made by clinicians on the gap-closure lane.</p>
        </div>
      </section>

      <section className="section-card care-gap-lane-card">
        <div className="section-heading">
          <div><span className="panel-kicker">Care-gap lane · {gapReviewPacket.provenance.rule_package_id}</span><h2>Grounded gap findings</h2></div>
          <button className="button button--dark" onClick={onOpenEpisode} type="button">Open episode drilldown <ArrowRight size={16} /></button>
        </div>
        <div className="care-gap-lane">
          {gapLane.map(item => (
            <GapLaneRow key={item.findingId} item={item} onOpenEpisode={onOpenEpisode} />
          ))}
        </div>
      </section>

      <div className="care-gap-boundary" role="note">
        <ShieldCheck size={17} />
        <div>
          <strong>Care gaps never touch the claim.</strong>
          <span>Gap findings are routed to the care team or closed with evidence on a separate gap-closure lane — they can never mutate a claim, assign a DRG, or move through the revenue review workflow. <button className="text-button" onClick={() => onNavigate('governance')} type="button">See governance</button></span>
        </div>
      </div>
    </>
  )
}

function GapLaneRow({ item, onOpenEpisode }: { item: GapLaneItem; onOpenEpisode: () => void }) {
  return (
    <button className="care-gap-row" onClick={onOpenEpisode} type="button" aria-label={`Open episode for ${item.title}`}>
      <span className={`gap-urgency gap-urgency--${item.alertUrgency ?? 'routine'}`}>{formatGapLabel(item.alertUrgency)}</span>
      <div className="care-gap-row__main">
        <span className="care-gap-row__rule"><Stethoscope size={12} /> {item.ruleId} · {formatGapLabel(item.gapDomain)}</span>
        <strong>{item.title}</strong>
        <small>Expected: {formatGapLabel(item.expectedAction)}{item.timingWindowDays != null ? ` within ${item.timingWindowDays}d` : ''} · {item.confidence}% confidence</small>
      </div>
      <span className={`gap-status gap-status--${item.gapStatus ?? 'open'}`}>{formatGapLabel(item.gapStatus)}</span>
      <ArrowRight size={16} />
    </button>
  )
}

function GapMetric({ icon: Icon, value, label, detail, tone }: { icon: typeof Activity; value: string; label: string; detail: string; tone?: 'urgent' | 'good' }) {
  return (
    <div className={`care-gap-metric${tone ? ` care-gap-metric--${tone}` : ''}`}>
      <Icon size={17} strokeWidth={1.7} />
      <strong>{value}</strong>
      <span>{label}</span>
      <small>{detail}</small>
    </div>
  )
}
