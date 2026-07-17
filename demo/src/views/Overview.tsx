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

import { automationSummary, humanOpportunities } from '../data'
import type { ViewId } from '../types'

interface OverviewProps {
  onNavigate: (view: ViewId) => void
  onStartTour: () => void
  notify: (message: string) => void
}

const scanStages = ['Profiling source bundle', 'Reconstructing encounters', 'Validating evidence', 'Running governed rules']

export function Overview({ onNavigate, onStartTour, notify }: OverviewProps) {
  const [scanStage, setScanStage] = useState(-1)

  useEffect(() => {
    if (scanStage < 0 || scanStage >= scanStages.length) return
    const timer = window.setTimeout(() => setScanStage(value => value + 1), 720)
    return () => window.clearTimeout(timer)
  }, [scanStage])

  useEffect(() => {
    if (scanStage === scanStages.length) notify(`Scan complete · ${automationSummary.human} of ${automationSummary.scanned} encounters need a person`)
  }, [notify, scanStage])

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
        <Metric icon={CircleDollarSign} label="Need a person" value={String(automationSummary.human)} change={`of ${automationSummary.scanned} encounters`} />
        <Metric icon={ShieldCheck} label="Evidence coverage" value="100%" change="no uncited candidates" />
        <Metric icon={Clock3} label="No-touch rate" value={`${automationSummary.noTouchRate}%`} change="clean or handled automatically" />
        <Metric icon={TrendingUp} label="Precision target" value=">95%" change="prospective validation gate" />
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
            {humanOpportunities.map(item => (
              <button className="compact-row" key={item.id} onClick={() => onNavigate('case')} type="button">
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
          </div>
        </div>

        <div className="section-card impact-card">
          <div className="section-heading">
            <div>
              <span className="panel-kicker">30-day trend</span>
              <h2>Validated impact</h2>
            </div>
            <span className="trend-chip">+18.4%</span>
          </div>
          <div className="impact-total"><strong>$284,650</strong><span>synthetic accepted opportunity value</span></div>
          <svg className="sparkline" viewBox="0 0 500 130" role="img" aria-label="Validated impact increasing over 30 days">
            <path className="sparkline-grid" d="M0 25H500M0 65H500M0 105H500" />
            <path className="sparkline-area" d="M0 106 C40 99 54 105 82 89 S130 97 161 74 S211 81 247 61 S294 71 330 44 S390 54 423 29 S467 35 500 17 V130 H0 Z" />
            <path className="sparkline-line" d="M0 106 C40 99 54 105 82 89 S130 97 161 74 S211 81 247 61 S294 71 330 44 S390 54 423 29 S467 35 500 17" />
            <circle cx="500" cy="17" r="5" />
          </svg>
          <div className="impact-legend"><span>Jun 18</span><span>Jul 17</span></div>
          <p className="demo-caption">Illustrative product data for pitch demonstration only.</p>
        </div>
      </section>
    </>
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
