import {
  ArrowLeft,
  ArrowRight,
  BookOpenCheck,
  Check,
  ChevronRight,
  CircleDollarSign,
  ClipboardCheck,
  Code2,
  FileText,
  GitBranch,
  History,
  Info,
  Link2,
  MessageSquareText,
  ShieldCheck,
  UserRoundCheck,
  X,
} from 'lucide-react'
import { useState, type CSSProperties, type ReactNode } from 'react'

import { opportunities } from '../data'
import type { ViewId } from '../types'

interface CaseReviewProps {
  onNavigate: (view: ViewId) => void
  notify: (message: string) => void
}

type CaseTab = 'evidence' | 'graph' | 'claim' | 'audit'

export function CaseReview({ onNavigate, notify }: CaseReviewProps) {
  const opportunity = opportunities[0]
  const [tab, setTab] = useState<CaseTab>('evidence')
  const [decision, setDecision] = useState<'open' | 'routed' | 'dismissed'>('open')

  const route = () => {
    setDecision('routed')
    notify('Opportunity routed to coding review · audit entry created')
  }

  return (
    <>
      <button className="back-link" onClick={() => onNavigate('queue')} type="button"><ArrowLeft size={15} /> Review queue</button>
      <header className="case-header">
        <div>
          <div className="case-header__meta">
            <span className="status-label status-label--ready-for-review"><i />Ready for review</span>
            <span>{opportunity.id}</span>
            <span>Updated 12 min ago</span>
          </div>
          <h1>{opportunity.title}</h1>
          <p>{opportunity.encounterId} · Alpha Medical Center · Discharged Jun 5, 2026</p>
        </div>
        <div className="case-header__actions">
          <button className="button button--quiet" onClick={() => {
            setDecision('dismissed')
            notify('Opportunity dismissed · reason required before final save')
          }} type="button"><X size={16} /> Dismiss</button>
          <button className="button button--primary" onClick={route} disabled={decision === 'routed'} type="button">
            <UserRoundCheck size={17} /> {decision === 'routed' ? 'Routed to coding' : 'Route to coding review'}
          </button>
        </div>
      </header>

      {decision !== 'open' && (
        <div className={`decision-banner decision-banner--${decision}`}>
          {decision === 'routed' ? <Check size={17} /> : <Info size={17} />}
          <span>{decision === 'routed' ? 'This opportunity is now assigned to Coding Review.' : 'Dismissal started. Select a governed reason to complete the decision.'}</span>
          <button onClick={() => setDecision('open')} type="button">Undo</button>
        </div>
      )}

      <section className="case-summary-grid">
        <div className="patient-summary">
          <span className="panel-kicker">Encounter context</span>
          <div className="patient-identity">
            <div className="patient-monogram">P1</div>
            <div><strong>{opportunity.patientId}</strong><span>Deidentified synthetic patient</span></div>
          </div>
          <dl>
            <div><dt>Encounter</dt><dd>{opportunity.encounterId}</dd></div>
            <div><dt>Admit</dt><dd>Jun 1, 2026 · 08:00</dd></div>
            <div><dt>Discharge</dt><dd>Jun 5, 2026 · 16:00</dd></div>
            <div><dt>Service</dt><dd>Medicine · Wound consult</dd></div>
            <div><dt>Payer</dt><dd>Medicare FFS · Synthetic</dd></div>
          </dl>
        </div>

        <div className="finding-summary">
          <div className="finding-summary__top">
            <div>
              <span className="panel-kicker">System finding</span>
              <h2>Documentation supports a coding review</h2>
            </div>
            <div className="confidence-ring" style={{ '--confidence': '98%' } as CSSProperties}>
              <strong>98</strong><span>%</span>
            </div>
          </div>
          <p>
            An explicit wound assessment records a <strong>stage 4 sacral pressure injury</strong>. The corresponding diagnosis is absent from the claim extract.
          </p>
          <div className="finding-conditions">
            <span><Check size={13} /> Stage explicitly documented</span>
            <span><Check size={13} /> Sacral site documented</span>
            <span><Check size={13} /> Diagnosis absent from claim</span>
            <span><ShieldCheck size={13} /> POA requires reviewer confirmation</span>
          </div>
        </div>

        <div className="impact-summary">
          <span className="panel-kicker">Illustrative simulation</span>
          <div className="drg-change">
            <div><span>Submitted</span><strong>871</strong><small>Septicemia w/o MV &gt;96h w/o MCC</small></div>
            <ArrowRight size={22} />
            <div><span>Candidate</span><strong>870</strong><small>Septicemia w/o MV &gt;96h w MCC</small></div>
          </div>
          <div className="impact-amount"><CircleDollarSign size={19} /><span>Estimated net impact</span><strong>+$8,420</strong></div>
          <small className="simulation-note">Synthetic illustration. Final result requires a licensed grouper, payer context, and coder approval.</small>
        </div>
      </section>

      <section className="case-workspace">
        <div className="case-tabs" role="tablist" aria-label="Encounter review details">
          <Tab active={tab === 'evidence'} icon={FileText} label="Evidence" count={4} onClick={() => setTab('evidence')} />
          <Tab active={tab === 'graph'} icon={GitBranch} label="Encounter graph" onClick={() => setTab('graph')} />
          <Tab active={tab === 'claim'} icon={Code2} label="Claim comparison" onClick={() => setTab('claim')} />
          <Tab active={tab === 'audit'} icon={History} label="Audit trail" onClick={() => setTab('audit')} />
        </div>
        <div className="case-tab-content">
          {tab === 'evidence' && <EvidenceTab />}
          {tab === 'graph' && <GraphTab />}
          {tab === 'claim' && <ClaimTab />}
          {tab === 'audit' && <AuditTab />}
        </div>
      </section>
    </>
  )
}

function Tab({ active, icon: Icon, label, count, onClick }: { active: boolean; icon: typeof FileText; label: string; count?: number; onClick: () => void }) {
  return <button className={active ? 'case-tab case-tab--active' : 'case-tab'} onClick={onClick} role="tab" aria-selected={active} type="button"><Icon size={16} />{label}{count && <span>{count}</span>}</button>
}

function EvidenceTab() {
  return (
    <div className="evidence-layout">
      <div className="evidence-list">
        <div className="tab-section-heading"><div><span className="panel-kicker">Source-grounded record</span><h3>Supporting evidence</h3></div><span className="evidence-complete"><ShieldCheck size={15} /> Lineage verified</span></div>
        <EvidenceItem
          type="Structured assessment"
          source="wound_assessments.csv · row 1"
          time="Jun 1 · 14:00"
          content={<>WOUND_ID=WOUND-ALPHA-001; <mark>STAGE=IV</mark>; <mark>SITE=Sacrum</mark>; POA=Y</>}
          tags={['Explicit', 'Source row', 'Assessment ID linked']}
        />
        <EvidenceItem
          type="Wound consult"
          source="Physician · NOTE-ALPHA-001"
          time="Jun 1 · 14:30"
          content={<>“Stage 4 pressure injury of the <mark>sacral region</mark> with exposed muscle.”</>}
          tags={['Exact excerpt', 'Physician', 'No negation']}
        />
        <EvidenceItem
          type="Admission skin assessment"
          source="Registered nurse · NOTE-ALPHA-004"
          time="Jun 1 · 08:24"
          content={<>“Deep sacral wound noted on arrival; wound team consult requested.”</>}
          tags={['POA indicator', 'Needs reviewer confirmation']}
          caution
        />
      </div>
      <aside className="reasoning-panel">
        <span className="panel-kicker">Why this was surfaced</span>
        <h3>Governed rule trace</h3>
        <div className="rule-id"><BookOpenCheck size={16} /> WC-PI-OMITTED-001</div>
        <ol className="rule-trace">
          <li><Check size={14} /><span><strong>Clinical fact</strong>Pressure injury is present and explicitly documented.</span></li>
          <li><Check size={14} /><span><strong>Required specificity</strong>Site is sacral; stage is 4.</span></li>
          <li><Check size={14} /><span><strong>Claim comparison</strong>L89.154 is not in submitted diagnoses.</span></li>
          <li><UserRoundCheck size={14} /><span><strong>Human control</strong>Confirm coding criteria and POA before any change.</span></li>
        </ol>
        <button className="text-button" type="button">View rule package <ChevronRight size={15} /></button>
      </aside>
    </div>
  )
}

function EvidenceItem({ type, source, time, content, tags, caution = false }: { type: string; source: string; time: string; content: ReactNode; tags: string[]; caution?: boolean }) {
  return (
    <article className="evidence-item">
      <div className="evidence-item__icon">{caution ? <MessageSquareText size={17} /> : <FileText size={17} />}</div>
      <div className="evidence-item__body">
        <div className="evidence-item__meta"><strong>{type}</strong><span>{source}</span><time>{time}</time></div>
        <blockquote>{content}</blockquote>
        <div className="evidence-tags">{tags.map(tag => <span className={caution && tag.includes('review') ? 'evidence-tag evidence-tag--caution' : 'evidence-tag'} key={tag}>{tag}</span>)}</div>
      </div>
      <button className="icon-button" type="button" aria-label={`Open ${type}`}><Link2 size={16} /></button>
    </article>
  )
}

function GraphTab() {
  return (
    <div className="graph-layout">
      <div className="tab-section-heading"><div><span className="panel-kicker">Patient-specific ontology</span><h3>Encounter evidence graph</h3></div><div className="graph-legend"><span><i className="legend-dot legend-dot--structural" />Structural</span><span><i className="legend-dot legend-dot--clinical" />Clinical</span><span><i className="legend-dot legend-dot--financial" />Financial</span></div></div>
      <div className="ontology-canvas">
        <svg viewBox="0 0 920 430" preserveAspectRatio="none" aria-hidden="true">
          <path d="M156 96 C230 96 240 96 310 96" />
          <path d="M454 96 C530 96 530 96 604 96" />
          <path d="M382 126 C382 190 260 184 260 244" />
          <path d="M382 126 C382 190 502 184 502 244" />
          <path d="M260 282 C260 340 382 326 382 370" />
          <path d="M502 282 C502 340 382 326 382 370" />
          <path d="M748 126 C748 216 748 270 748 354" className="graph-line--financial" />
        </svg>
        <GraphNode className="node-patient" eyebrow="Patient" title="PAT-ALPHA-001" type="structural" />
        <GraphNode className="node-encounter" eyebrow="Encounter" title="ENC-ALPHA-001" type="structural" />
        <GraphNode className="node-claim" eyebrow="Claim" title="Submitted · DRG 871" type="financial" />
        <GraphNode className="node-wound" eyebrow="Pressure injury" title="WOUND-ALPHA-001" type="clinical" />
        <GraphNode className="node-location" eyebrow="Anatomical location" title="Sacrum" type="clinical" />
        <GraphNode className="node-stage" eyebrow="Pressure injury stage" title="Stage 4" type="clinical" />
        <GraphNode className="node-code" eyebrow="Missing diagnosis" title="L89.154" type="financial" dashed />
      </div>
      <div className="graph-inspector"><Info size={16} /><span>Select a node to inspect its properties, terminology mapping, supporting evidence, and contradictions.</span></div>
    </div>
  )
}

function GraphNode({ eyebrow, title, type, className, dashed = false }: { eyebrow: string; title: string; type: string; className: string; dashed?: boolean }) {
  return <button className={`graph-node graph-node--${type} ${className} ${dashed ? 'graph-node--dashed' : ''}`} type="button"><span>{eyebrow}</span><strong>{title}</strong></button>
}

function ClaimTab() {
  return (
    <div className="claim-layout">
      <div className="tab-section-heading"><div><span className="panel-kicker">Deterministic comparison</span><h3>Current claim vs. review hypothesis</h3></div><span className="evidence-complete"><ClipboardCheck size={15} /> Grouper boundary</span></div>
      <div className="claim-columns">
        <div className="claim-column">
          <div className="claim-column__header"><span>Current submitted claim</span><strong>MS-DRG 871</strong></div>
          <ClaimLine code="A41.9" label="Sepsis, unspecified organism" />
          <ClaimLine code="R65.20" label="Severe sepsis without septic shock" />
          <ClaimLine code="N17.9" label="Acute kidney failure, unspecified" />
          <div className="claim-payment"><span>Illustrative expected payment</span><strong>$12,000</strong></div>
        </div>
        <div className="claim-divider"><ArrowRight size={20} /></div>
        <div className="claim-column claim-column--candidate">
          <div className="claim-column__header"><span>Review hypothesis</span><strong>MS-DRG 870</strong></div>
          <ClaimLine code="A41.9" label="Sepsis, unspecified organism" />
          <ClaimLine code="R65.20" label="Severe sepsis without septic shock" />
          <ClaimLine code="N17.9" label="Acute kidney failure, unspecified" />
          <ClaimLine code="L89.154" label="Pressure ulcer of sacral region, stage 4" added />
          <div className="claim-payment claim-payment--candidate"><span>Illustrative expected payment</span><strong>$20,420</strong></div>
        </div>
      </div>
      <div className="claim-warning"><ShieldCheck size={17} /><div><strong>Simulation is not a coding decision.</strong><span>The proposed code remains a review hypothesis until a qualified coder confirms documentation, coding criteria, sequencing, and POA.</span></div></div>
    </div>
  )
}

function ClaimLine({ code, label, added = false }: { code: string; label: string; added?: boolean }) {
  return <div className={added ? 'claim-line claim-line--added' : 'claim-line'}><code>{code}</code><span>{label}</span>{added && <b>Proposed</b>}</div>
}

function AuditTab() {
  const events = [
    ['14:42', 'Rule engine', 'Opportunity WC-PI-OMITTED-001 materialized from validated assertion.'],
    ['14:42', 'Grouper service', 'Baseline and candidate grouping simulation completed.'],
    ['14:41', 'Ontology validator', '3 entities, 3 relations, and evidence lineage accepted.'],
    ['14:41', 'Mastra extraction', 'Narrative evidence grounded to exact source excerpt.'],
    ['14:40', 'Deterministic adapter', '6 resources transformed; schema fingerprint matched approved adapter.'],
  ]
  return (
    <div className="audit-layout">
      <div className="tab-section-heading"><div><span className="panel-kicker">Reproducible by design</span><h3>Case audit trail</h3></div><span className="evidence-complete"><ShieldCheck size={15} /> Hash chained</span></div>
      <div className="audit-timeline">
        {events.map(([time, actor, event]) => <div className="audit-event" key={time + actor}><time>{time}</time><span /><div><strong>{actor}</strong><p>{event}</p></div></div>)}
      </div>
    </div>
  )
}
