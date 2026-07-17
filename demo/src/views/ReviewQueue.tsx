import { ArrowRight, ChevronDown, Download, Filter, Search } from 'lucide-react'
import { useMemo, useState } from 'react'

import { humanOpportunities, opportunities } from '../data'
import type { AutomationOutcome, ViewId } from '../types'

interface ReviewQueueProps {
  onNavigate: (view: ViewId) => void
  notify: (message: string) => void
}

const filters: Array<{ label: string; value: 'all' | AutomationOutcome }> = [
  { label: 'Needs me', value: 'human_exception' },
  { label: 'Auto-handled', value: 'auto_routed' },
  { label: 'Suppressed', value: 'suppressed' },
  { label: 'All', value: 'all' },
]

export function ReviewQueue({ onNavigate, notify }: ReviewQueueProps) {
  const [filter, setFilter] = useState<(typeof filters)[number]['value']>('human_exception')
  const [query, setQuery] = useState('')

  const visible = useMemo(() => opportunities.filter(item => {
    const matchesFilter = filter === 'all' || item.automationOutcome === filter
    const searchable = `${item.title} ${item.encounterId} ${item.serviceLine} ${item.type}`.toLowerCase()
    return matchesFilter && searchable.includes(query.toLowerCase())
  }), [filter, query])

  const exportQueue = () => {
    const header = ['opportunity_id', 'encounter_id', 'workflow', 'status', 'confidence', 'illustrative_impact']
    const rows = visible.map(item => [item.id, item.encounterId, item.type, item.status, item.confidence, item.impact])
    const csv = [header, ...rows].map(row => row.map(value => `"${String(value).replaceAll('"', '""')}"`).join(',')).join('\n')
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
    const link = document.createElement('a')
    link.href = url
    link.download = 'encounter-synthetic-review-queue.csv'
    link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 0)
    notify(`Exported ${visible.length} synthetic opportunities`)
  }

  return (
    <>
      <header className="page-header">
        <div>
          <span className="eyebrow">Residual exception layer</span>
          <h1>{humanOpportunities.length} decisions need a person</h1>
          <p>Everything else was cleared, consolidated, enriched, or routed automatically by deterministic policy.</p>
        </div>
        <div className="page-header__actions">
          <button className="button button--quiet" onClick={exportQueue} type="button"><Download size={16} /> Export queue</button>
          <button className="button button--dark" onClick={() => onNavigate('case')} type="button">Review top case <ArrowRight size={16} /></button>
        </div>
      </header>

      <section className="queue-toolbar">
        <div className="filter-tabs">
          {filters.map(item => (
            <button className={filter === item.value ? 'filter-tab filter-tab--active' : 'filter-tab'} onClick={() => setFilter(item.value)} key={item.value} type="button">
              {item.label}
              {item.value === 'human_exception' && <span>{humanOpportunities.length}</span>}
            </button>
          ))}
        </div>
        <div className="queue-tools">
          <label className="search-field">
            <Search size={16} />
            <input value={query} onChange={event => setQuery(event.target.value)} placeholder="Search encounters" />
          </label>
          <button className="icon-button icon-button--border" type="button" aria-label="Filter queue"><Filter size={17} /></button>
        </div>
      </section>

      <section className="queue-table-card">
        <div className="queue-table-header">
          <span>Opportunity</span>
          <span>Workflow</span>
          <span>Confidence</span>
          <span>Potential impact</span>
          <span>Human time</span>
          <span />
        </div>
        {visible.length ? visible.map(item => (
          <div className="queue-row" key={item.id}>
            <div className="queue-opportunity">
              <span className={`priority-dot priority-dot--${item.priority.toLowerCase()}`} />
              <div>
                <span>{item.serviceLine} · {item.type}</span>
                <strong>{item.title}</strong>
                <small>{item.encounterId} · {item.facility}</small>
                {item.relatedFindingIds.length > 0 && <small className="consolidation-badge">{item.relatedFindingIds.length + 1} findings consolidated</small>}
              </div>
            </div>
            <div><Disposition outcome={item.automationOutcome} tier={item.automationTier} /></div>
            <div className="confidence-cell"><strong>{item.confidence}%</strong><span><i style={{ width: `${item.confidence}%` }} /></span></div>
            <div className="impact-cell"><strong>{item.impact == null ? 'Unavailable' : item.impact ? `$${item.impact.toLocaleString()}` : '—'}</strong><small>{item.currentDrg !== item.simulatedDrg ? `${item.currentDrg} → ${item.simulatedDrg}` : 'No DRG change'}</small></div>
            <div className="age-cell">{item.estimatedReviewSeconds ? `~${item.estimatedReviewSeconds} sec` : 'No review'}</div>
            {item.packetBacked ? <button className="row-open" onClick={() => onNavigate('case')} type="button" aria-label={`Open ${item.title}`}><ArrowRight size={17} /></button> : <span className="row-arrow"><ArrowRight size={17} /></span>}
          </div>
        )) : (
          <div className="empty-state"><Search size={24} /><strong>No opportunities found</strong><span>Try changing the queue filter or search term.</span></div>
        )}
        <div className="queue-footer"><span>Showing {visible.length} synthetic encounters</span><button type="button">25 per page <ChevronDown size={14} /></button></div>
      </section>
    </>
  )
}

function Disposition({ outcome, tier }: { outcome: AutomationOutcome; tier: string }) {
  const labels: Record<AutomationOutcome, string> = {
    human_exception: tier === 'quick_confirm' ? 'Quick confirmation' : 'Focused review',
    auto_routed: 'Auto-routed', suppressed: 'Suppressed', needs_enrichment: 'Enriching',
  }
  return <span className={`disposition-chip disposition-chip--${outcome}`}>{labels[outcome]}</span>
}
