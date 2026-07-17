import { ArrowRight, ChevronDown, Download, Filter, Search } from 'lucide-react'
import { useMemo, useState } from 'react'

import { opportunities } from '../data'
import type { OpportunityStatus, ViewId } from '../types'

interface ReviewQueueProps {
  onNavigate: (view: ViewId) => void
  notify: (message: string) => void
}

const filters: Array<'All' | OpportunityStatus> = ['All', 'Ready for review', 'Needs documentation', 'In review', 'Cleared']

export function ReviewQueue({ onNavigate, notify }: ReviewQueueProps) {
  const [filter, setFilter] = useState<(typeof filters)[number]>('All')
  const [query, setQuery] = useState('')

  const visible = useMemo(() => opportunities.filter(item => {
    const matchesFilter = filter === 'All' || item.status === filter
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
          <span className="eyebrow">Human decision layer</span>
          <h1>Review queue</h1>
          <p>Evidence-complete opportunities ranked by urgency, confidence, and financial materiality.</p>
        </div>
        <div className="page-header__actions">
          <button className="button button--quiet" onClick={exportQueue} type="button"><Download size={16} /> Export queue</button>
          <button className="button button--dark" onClick={() => onNavigate('case')} type="button">Review top case <ArrowRight size={16} /></button>
        </div>
      </header>

      <section className="queue-toolbar">
        <div className="filter-tabs">
          {filters.map(item => (
            <button className={filter === item ? 'filter-tab filter-tab--active' : 'filter-tab'} onClick={() => setFilter(item)} key={item} type="button">
              {item}
              {item === 'All' && <span>{opportunities.length}</span>}
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
          <span>Age</span>
          <span />
        </div>
        {visible.length ? visible.map(item => (
          <button className="queue-row" key={item.id} onClick={() => onNavigate('case')} type="button">
            <div className="queue-opportunity">
              <span className={`priority-dot priority-dot--${item.priority.toLowerCase()}`} />
              <div>
                <span>{item.serviceLine} · {item.type}</span>
                <strong>{item.title}</strong>
                <small>{item.encounterId} · {item.facility}</small>
              </div>
            </div>
            <div><Status value={item.status} /></div>
            <div className="confidence-cell"><strong>{item.confidence}%</strong><span><i style={{ width: `${item.confidence}%` }} /></span></div>
            <div className="impact-cell"><strong>{item.impact ? `$${item.impact.toLocaleString()}` : '—'}</strong><small>{item.currentDrg !== item.simulatedDrg ? `${item.currentDrg} → ${item.simulatedDrg}` : 'No DRG change'}</small></div>
            <div className="age-cell">{item.age}</div>
            <ArrowRight className="row-arrow" size={17} />
          </button>
        )) : (
          <div className="empty-state"><Search size={24} /><strong>No opportunities found</strong><span>Try changing the queue filter or search term.</span></div>
        )}
        <div className="queue-footer"><span>Showing {visible.length} synthetic encounters</span><button type="button">25 per page <ChevronDown size={14} /></button></div>
      </section>
    </>
  )
}

function Status({ value }: { value: OpportunityStatus }) {
  const slug = value.toLowerCase().replaceAll(' ', '-').replace('documentation', 'docs')
  return <span className={`status-label status-label--${slug}`}><i />{value}</span>
}
