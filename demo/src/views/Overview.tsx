import {
  ArrowRight,
  Check,
  CircleDollarSign,
  Clock3,
  FileCheck2,
  ScanSearch,
  ShieldCheck,
  Sparkles,
  TrendingUp,
} from 'lucide-react'
import { useEffect, useState } from 'react'

import { automationSummary, engineVersion, evaluationReport, humanOpportunities, impactSummary, packetHashShort, reviewerEffort } from '../data'
import type { ViewId } from '../types'
import type { ReviewDecision } from '../workflow'

interface OverviewProps {
  onNavigate: (view: ViewId) => void
  onOpenCase: (opportunityId: string) => void
  onStartTour: () => void
  notify: (message: string) => void
  decisions: ReviewDecision[]
}

const scanStages = ['Profiling source bundle', 'Reconstructing encounters', 'Validating evidence', 'Running governed rules']

export function Overview({ onNavigate, onOpenCase, onStartTour, notify, decisions }: OverviewProps) {
  const [scanStage, setScanStage] = useState(-1)
  const resolvedFindingIds = new Set(decisions.map(decision => decision.finding_id))
  const pendingHumanOpportunities = humanOpportunities.filter(item => !resolvedFindingIds.has(item.id))

  useEffect(() => {
    if (scanStage < 0 || scanStage >= scanStages.length) return
    const timer = window.setTimeout(() => setScanStage(value => value + 1), 720)
    return () => window.clearTimeout(timer)
  }, [scanStage])

  useEffect(() => {
    if (scanStage === scanStages.length) notify(`Scan complete · ${pendingHumanOpportunities.length} of ${automationSummary.scanned} encounters still need a person`)
  }, [notify, pendingHumanOpportunities.length, scanStage])

  const scanning = scanStage >= 0 && scanStage < scanStages.length

  return (
    <>
      <section className="hero-grid">
        <div className="hero-copy">
          <span className="eyebrow">Clinical revenue intelligence</span>
          <h1>Find the truth between the chart and the claim.</h1>
          <p>
            Encounter reconstructs each inpatient stay, handles routine discrepancies automatically, and asks your team only the few questions that require qualified judgment.
          </p>
          <div className="hero-actions">
            <button className="button button--primary" onClick={() => onNavigate('queue')} type="button">
              Open review queue <ArrowRight size={17} />
            </button>
            <button className="button button--quiet" onClick={onStartTour} type="button">
              <Sparkles size={16} /> Start guided demo
            </button>
          </div>
          <div className="trust-row">
            <span><Check size={14} /> Evidence linked</span>
            <span><Check size={14} /> Deterministic grouping</span>
            <span><Check size={14} /> {automationSummary.noTouchRate}% no-touch</span>
          </div>
        </div>

        <div className="scan-panel">
          <div className="scan-panel__header">
            <div>
              <span className="panel-kicker">Live workflow</span>
              <h3>Nightly encounter scan</h3>
            </div>
            <span className={scanning ? 'status-badge status-badge--running' : 'status-badge status-badge--healthy'}>
              <span /> {scanning ? 'Running' : scanStage === scanStages.length ? 'Complete' : 'Ready'}
            </span>
          </div>
          <div className="scan-source">
            <div className="source-icon"><FileCheck2 size={20} /></div>
            <div>
              <strong>Alpha Medical · July discharge batch</strong>
              <small>7 source files · 183 encounters · deidentified</small>
            </div>
          </div>
          <div className="scan-stages">
            {scanStages.map((stage, index) => {
              const complete = scanStage > index || scanStage === scanStages.length
              const active = scanStage === index
              return (
                <div className={active ? 'scan-stage scan-stage--active' : 'scan-stage'} key={stage}>
                  <span className={complete ? 'stage-marker stage-marker--complete' : active ? 'stage-marker stage-marker--active' : 'stage-marker'}>
                    {complete ? <Check size={12} /> : index + 1}
                  </span>
                  <span>{stage}</span>
                  {active && <small>processing</small>}
                  {complete && <small>verified</small>}
                </div>
              )
            })}
          </div>
          <button
            className="button button--scan"
            disabled={scanning}
            onClick={() => setScanStage(0)}
            type="button"
          >
            <ScanSearch size={17} /> {scanning ? scanStages[scanStage] : scanStage === scanStages.length ? 'Run again' : 'Run synthetic scan'}
          </button>
        </div>
      </section>

      <section className="metric-strip" aria-label="Synthetic demonstration metrics">
        <Metric icon={CircleDollarSign} label="Need a person" value={String(pendingHumanOpportunities.length)} change={`of ${automationSummary.scanned} encounters remaining`} />
        <Metric icon={ShieldCheck} label="Evidence coverage" value="100%" change="no uncited candidates" />
        <Metric icon={Clock3} label="No-touch rate" value={`${automationSummary.noTouchRate}%`} change="clean or handled automatically" />
        <Metric icon={TrendingUp} label="Backtest precision" value={`${(evaluationReport.metrics.precision * 100).toFixed(0)}%`} change={`${evaluationReport.label_count} synthetic gold labels`} />
      </section>

      <section className="dashboard-grid">
        <div className="section-card opportunity-preview">
          <div className="section-heading">
            <div>
              <span className="panel-kicker">Priority work</span>
              <h2>Review queue</h2>
            </div>
            <button className="text-button" onClick={() => onNavigate('queue')} type="button">View all <ArrowRight size={15} /></button>
          </div>
          <div className="compact-table">
            {pendingHumanOpportunities.map(item => (
              <button className="compact-row" key={item.id} onClick={() => onOpenCase(item.id)} type="button">
                <span className={`priority-line priority-line--${item.priority.toLowerCase()}`} />
                <div className="compact-row__main">
                  <span>{item.serviceLine} · {item.type}</span>
                  <strong>{item.title}</strong>
                  <small>{item.encounterId} · {item.evidenceCount} evidence items</small>
                </div>
                <div className="compact-row__impact">
                  <strong>{item.impact == null ? 'Unavailable' : item.impact ? `$${item.impact.toLocaleString()}` : 'Query'}</strong>
                  <small>{item.confidence}% confidence</small>
                </div>
                <ArrowRight size={16} />
              </button>
            ))}
            {!pendingHumanOpportunities.length && <div className="empty-state"><Check size={24} /><strong>Human queue cleared</strong><span>All synthetic exceptions have a governed decision.</span></div>}
          </div>
        </div>

        <div className="section-card impact-card">
          <div className="section-heading">
            <div>
              <span className="panel-kicker">Engine-verified</span>
              <h2>Deterministic case impact</h2>
            </div>
            <span className="trend-chip" title={`Reproducible from review packet ${packetHashShort}`}>hash-backed</span>
          </div>
          <div className="impact-total">
            <strong>{formatDollars(impactSummary.net_estimated_impact_cents)}</strong>
            <span>net synthetic opportunity · reproduced from the review packet, not hand-typed</span>
          </div>
          <div className="impact-breakdown">
            <ImpactStat label="Recoverable upside" value={formatDollars(impactSummary.positive_opportunity_cents)} />
            <ImpactStat label="Downside at risk" value={formatDollars(impactSummary.at_risk_cents)} />
            <ImpactStat label="Findings needing a person" value={String(impactSummary.findings_requiring_review)} />
            <ImpactStat label="Reviewer minutes saved" value={`~${Math.round(reviewerEffort.seconds_avoided_estimate / 60)}`} />
          </div>
          <p className="demo-caption">
            Synthetic demo grouper ({engineVersion}) · every figure is deterministic and reproducible from packet {packetHashShort}. Portfolio projections shown elsewhere are illustrative.
          </p>
        </div>
      </section>
    </>
  )
}

function formatDollars(cents: number): string {
  const sign = cents < 0 ? '-' : ''
  return `${sign}$${Math.abs(Math.round(cents / 100)).toLocaleString()}`
}

function ImpactStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="impact-stat">
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  )
}

function Metric({ icon: Icon, label, value, change }: { icon: typeof Clock3; label: string; value: string; change: string }) {
  return (
    <div className="metric">
      <Icon size={18} strokeWidth={1.7} />
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{change}</small>
    </div>
  )
}
