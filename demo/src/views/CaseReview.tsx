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
  ShieldCheck,
  UserRoundCheck,
  X,
} from 'lucide-react'
import { useState, type CSSProperties, type ReactNode } from 'react'

import { opportunities, primaryInvestigation, primaryReviewPacket } from '../data'
import type { ReviewPacket } from '../review-packet'
import type { ViewId } from '../types'
import type { ReviewerIdentity, ReviewWorkflowGateway, ReviewDecision } from '../workflow'
import type { AutomationPlan } from '../automation-plan'

interface CaseReviewProps {
  onNavigate: (view: ViewId) => void
  notify: (message: string) => void
  workflowGateway: ReviewWorkflowGateway
  reviewer: ReviewerIdentity
  automationPlan: AutomationPlan
  decisions: ReviewDecision[]
  onDecisionRecorded: (decision: ReviewDecision) => void
}

type CaseTab = 'evidence' | 'graph' | 'claim' | 'investigation' | 'audit'

export function CaseReview({ onNavigate, notify, workflowGateway, reviewer, automationPlan, decisions, onDecisionRecorded }: CaseReviewProps) {
  const opportunity = opportunities[0]
  const packet = primaryReviewPacket
  const finding = packet.findings[0]
  if (!opportunity || !finding) throw new Error('The demo case requires an engine-generated finding')
  const automation = automationPlan.findings.find(item => item.finding_id === finding.finding_id)
  if (!automation) throw new Error('The demo case requires a deterministic automation decision')
  const [tab, setTab] = useState<CaseTab>('evidence')
  const [dismissOpen, setDismissOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const latestDecision = decisions.find(item => item.finding_id === finding.finding_id)
  const decision = latestDecision?.action === 'dismiss_with_reason' ? 'dismissed' : latestDecision ? 'routed' : 'open'
  const recommendedAction = automation.recommended_action

  const route = async () => {
    if (!recommendedAction) {
      notify('This finding does not have a governed routing action')
      return
    }
    setSubmitting(true)
    try {
      const created = await workflowGateway.submit(
        packet, automationPlan, reviewer, finding.finding_id, recommendedAction,
        'evidence_confirmed',
        'Evidence and POA confirmed; send recommendation to the governed coding workflow',
        `${packet.packet_id}:${finding.finding_id}:${recommendedAction.replaceAll('_', '-')}`,
      )
      onDecisionRecorded(created)
      notify(`Opportunity routed to ${automation.queue} review · governed decision recorded`)
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Unable to record review decision')
    } finally {
      setSubmitting(false)
    }
  }

  const dismiss = async (reasonCode: 'documentation_not_supported' | 'duplicate' | 'already_corrected', reason: string) => {
    setSubmitting(true)
    try {
      const created = await workflowGateway.submit(
        packet, automationPlan, reviewer, finding.finding_id, 'dismiss_with_reason', reasonCode, reason,
        `${packet.packet_id}:${finding.finding_id}:dismiss:${reason.toLowerCase().replaceAll(' ', '-')}`,
      )
      onDecisionRecorded(created)
      setDismissOpen(false)
      notify('Opportunity dismissed · governed decision recorded')
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Unable to record review decision')
    } finally {
      setSubmitting(false)
    }
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
          <p>{opportunity.encounterId} · {opportunity.facility} · Discharged {formatDate(packet.case.discharged_at)}</p>
        </div>
        <div className="case-header__actions">
          <button className="button button--quiet" onClick={() => setDismissOpen(true)} disabled={decision !== 'open' || submitting} type="button"><X size={16} /> Dismiss</button>
          <button className="button button--primary" onClick={route} disabled={decision !== 'open' || submitting} type="button">
            <UserRoundCheck size={17} /> {submitting ? 'Recording…' : decision === 'routed' ? `Sent to ${automation.queue}` : `Confirm & send to ${automation.queue}`}
          </button>
        </div>
      </header>

      {decision !== 'open' && (
        <div className={`decision-banner decision-banner--${decision}`}>
          {decision === 'routed' ? <Check size={17} /> : <Info size={17} />}
          <span>{decision === 'routed' ? `Recommendation sent to the governed ${automation.queue} workflow.` : 'Opportunity dismissed with a governed reason.'}</span>
          <span>{latestDecision ? ` ${latestDecision.actor_id} · ${latestDecision.reason}` : ''}</span>
        </div>
      )}

      {dismissOpen && decision === 'open' && <div className="decision-reasons" role="dialog" aria-modal="true" aria-label="Dismissal reason">
        <strong>Select a governed dismissal reason</strong>
        <button type="button" onClick={() => dismiss('documentation_not_supported', 'Documentation does not support the proposed change')}>Documentation does not support change</button>
        <button type="button" onClick={() => dismiss('duplicate', 'Duplicate opportunity')}>Duplicate opportunity</button>
        <button type="button" onClick={() => dismiss('already_corrected', 'Already corrected in the source workflow')}>Already corrected</button>
        <button type="button" onClick={() => setDismissOpen(false)}>Cancel</button>
      </div>}

      <section className="case-summary-grid">
        <div className="patient-summary">
          <span className="panel-kicker">Encounter context</span>
          <div className="patient-identity">
            <div className="patient-monogram">P1</div>
            <div><strong>{opportunity.patientId}</strong><span>Deidentified synthetic patient</span></div>
          </div>
          <dl>
            <div><dt>Encounter</dt><dd>{opportunity.encounterId}</dd></div>
            <div><dt>Admit</dt><dd>{formatDateTime(packet.case.admitted_at)}</dd></div>
            <div><dt>Discharge</dt><dd>{formatDateTime(packet.case.discharged_at)}</dd></div>
            <div><dt>Service</dt><dd>Medicine · Wound consult</dd></div>
            <div><dt>Payer</dt><dd>{String(packet.case.metadata.payer)} · Synthetic</dd></div>
          </dl>
        </div>

        <div className="finding-summary">
          <div className="finding-summary__top">
            <div>
              <span className="panel-kicker">Only decision needed · ~{automation.estimated_review_seconds} sec</span>
              <h2>Does the cited documentation support this coding recommendation?</h2>
            </div>
            <div className="confidence-ring" style={{ '--confidence': `${opportunity.confidence}%` } as CSSProperties}>
              <strong>{opportunity.confidence}</strong><span>%</span>
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
          <div className="recommended-action">
            <span>Prepared action</span>
            <strong>{String(automation.draft.title)}</strong>
            <p>{String(automation.draft.body)}</p>
            <small>Policy {automationPlan.policy.version} · deterministic quick confirmation · no claim mutation</small>
          </div>
        </div>

        <div className="impact-summary">
          <span className="panel-kicker">Illustrative simulation</span>
          <div className="drg-change">
            <div><span>Submitted</span><strong>{finding.current_drg}</strong><small>Deterministic demo grouper baseline</small></div>
            <ArrowRight size={22} />
            <div><span>Candidate</span><strong>{finding.simulated_drg}</strong><small>Review hypothesis after proposed code</small></div>
          </div>
          <div className="impact-amount"><CircleDollarSign size={19} /><span>Estimated net impact</span><strong>{finding.estimated_impact_cents == null ? 'Unavailable' : formatSignedCurrency(finding.estimated_impact_cents)}</strong></div>
          <small className="simulation-note">Synthetic illustration. Final result requires a licensed grouper, payer context, and coder approval.</small>
        </div>
      </section>

      <section className="case-workspace">
        <div className="case-tabs" role="tablist" aria-label="Encounter review details">
          <Tab active={tab === 'evidence'} icon={FileText} label="Evidence" count={packet.evidence.length} onClick={() => setTab('evidence')} />
          <Tab active={tab === 'graph'} icon={GitBranch} label="Encounter graph" onClick={() => setTab('graph')} />
          <Tab active={tab === 'claim'} icon={Code2} label="Claim comparison" onClick={() => setTab('claim')} />
          <Tab active={tab === 'investigation'} icon={ClipboardCheck} label="Investigation" onClick={() => setTab('investigation')} />
          <Tab active={tab === 'audit'} icon={History} label="Audit trail" onClick={() => setTab('audit')} />
        </div>
        <div className="case-tab-content">
          {tab === 'evidence' && <EvidenceTab packet={packet} />}
          {tab === 'graph' && <GraphTab packet={packet} />}
          {tab === 'claim' && <ClaimTab packet={packet} />}
          {tab === 'investigation' && <InvestigationTab />}
          {tab === 'audit' && <AuditTab packet={packet} decisions={decisions} automationPlan={automationPlan} />}
        </div>
      </section>
    </>
  )
}

function InvestigationTab() {
  const stages = [primaryInvestigation.clinicalPass, primaryInvestigation.reconciliation, primaryInvestigation.critic, primaryInvestigation.validation]
  return (
    <div className="investigation-layout">
      <div className="tab-section-heading">
        <div><span className="panel-kicker">Governed investigation run</span><h3>From chart evidence to one narrow decision</h3></div>
        <span className="evidence-complete"><ShieldCheck size={15} /> Claim mutation disabled</span>
      </div>
      <p className="investigation-intro">The model investigates the clinical and financial record, but deterministic checks decide whether a reviewer ever sees the result.</p>
      <ol className="investigation-timeline">
        {stages.map((stage, index) => <li key={stage.title}>
          <div className="investigation-step__number">{index + 1}</div>
          <div className="investigation-step__content">
            <div><span className="status-label status-label--ready-for-review"><i />{stage.status}</span><h4>{stage.title}</h4></div>
            <p>{stage.detail}</p><small>{stage.evidence}</small>
          </div>
        </li>)}
      </ol>
      <div className="investigation-guardrail"><ShieldCheck size={17} /><span><strong>Automation boundary:</strong> the reviewer can route or dismiss this finding; the demo never alters or submits a claim.</span></div>
    </div>
  )
}

function Tab({ active, icon: Icon, label, count, onClick }: { active: boolean; icon: typeof FileText; label: string; count?: number; onClick: () => void }) {
  return <button className={active ? 'case-tab case-tab--active' : 'case-tab'} onClick={onClick} role="tab" aria-selected={active} type="button"><Icon size={16} />{label}{count && <span>{count}</span>}</button>
}

function EvidenceTab({ packet }: { packet: ReviewPacket }) {
  const finding = packet.findings[0]
  const assertion = packet.assertions[0]
  if (!finding || !assertion) return null
  return (
    <div className="evidence-layout">
      <div className="evidence-list">
        <div className="tab-section-heading"><div><span className="panel-kicker">Source-grounded record</span><h3>Supporting evidence</h3></div><span className="evidence-complete"><ShieldCheck size={15} /> Lineage verified</span></div>
        {packet.evidence.map(item => <EvidenceItem
          key={item.evidence_id}
          type={item.source_locator ? 'Structured source record' : 'Clinical note excerpt'}
          source={item.source_locator ? `${item.source_locator.path} · row ${item.source_locator.row_number}` : `${titleCase(item.author_role)} · ${item.document_id}`}
          time={formatDateTime(item.recorded_at)}
          content={<>“{item.text}”</>}
          tags={item.source_locator ? ['Deterministic lineage', 'Source row', 'Fields linked'] : ['Exact excerpt', titleCase(item.author_role), 'Grounded']}
        />)}
      </div>
      <aside className="reasoning-panel">
        <span className="panel-kicker">Why this was surfaced</span>
        <h3>Governed rule trace</h3>
        <div className="rule-id"><BookOpenCheck size={16} /> {finding.rule_id}</div>
        <ol className="rule-trace">
          <li><Check size={14} /><span><strong>Clinical fact</strong>{assertion.concept.replaceAll('_', ' ')} is present and explicitly documented.</span></li>
          <li><Check size={14} /><span><strong>Required specificity</strong>Site is {String(assertion.attributes.site).replaceAll('_', ' ')}; stage is {String(assertion.attributes.stage)}.</span></li>
          <li><Check size={14} /><span><strong>Claim comparison</strong>{proposedDiagnosis(finding)} is not in submitted diagnoses.</span></li>
          <li><UserRoundCheck size={14} /><span><strong>Human control</strong>Confirm coding criteria and POA before any change.</span></li>
        </ol>
        <button className="text-button" type="button">View rule package <ChevronRight size={15} /></button>
      </aside>
    </div>
  )
}

function EvidenceItem({ type, source, time, content, tags }: { type: string; source: string; time: string; content: ReactNode; tags: string[] }) {
  return (
    <article className="evidence-item">
      <div className="evidence-item__icon"><FileText size={17} /></div>
      <div className="evidence-item__body">
        <div className="evidence-item__meta"><strong>{type}</strong><span>{source}</span><time>{time}</time></div>
        <blockquote>{content}</blockquote>
        <div className="evidence-tags">{tags.map(tag => <span className="evidence-tag" key={tag}>{tag}</span>)}</div>
      </div>
      <button className="icon-button" type="button" aria-label={`Open ${type}`}><Link2 size={16} /></button>
    </article>
  )
}

function GraphTab({ packet }: { packet: ReviewPacket }) {
  const finding = packet.findings[0]
  if (!finding) return null
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
        <GraphNode className="node-patient" eyebrow="Patient" title={packet.case.patient_id} type="structural" />
        <GraphNode className="node-encounter" eyebrow="Encounter" title={packet.case.encounter_id} type="structural" />
        <GraphNode className="node-claim" eyebrow="Claim" title={`Submitted · ${finding.current_drg}`} type="financial" />
        <GraphNode className="node-wound" eyebrow="Pressure injury" title={finding.subject_ids[0] ?? 'Clinical subject'} type="clinical" />
        <GraphNode className="node-location" eyebrow="Anatomical location" title="Sacrum" type="clinical" />
        <GraphNode className="node-stage" eyebrow="Pressure injury stage" title="Stage 4" type="clinical" />
        <GraphNode className="node-code" eyebrow="Missing diagnosis" title={proposedDiagnosis(finding)} type="financial" dashed />
      </div>
      <div className="graph-inspector"><Info size={16} /><span>Select a node to inspect its properties, terminology mapping, supporting evidence, and contradictions.</span></div>
    </div>
  )
}

function GraphNode({ eyebrow, title, type, className, dashed = false }: { eyebrow: string; title: string; type: string; className: string; dashed?: boolean }) {
  return <button className={`graph-node graph-node--${type} ${className} ${dashed ? 'graph-node--dashed' : ''}`} type="button"><span>{eyebrow}</span><strong>{title}</strong></button>
}

function ClaimTab({ packet }: { packet: ReviewPacket }) {
  const finding = packet.findings[0]
  if (!finding) return null
  const currentPayment = packet.case.claim.allowed_amount_cents ?? 0
  const candidatePayment = finding.estimated_impact_cents == null
    ? null
    : currentPayment + finding.estimated_impact_cents
  const addedDiagnosis = proposedDiagnosis(finding)
  return (
    <div className="claim-layout">
      <div className="tab-section-heading"><div><span className="panel-kicker">Deterministic comparison</span><h3>Current claim vs. review hypothesis</h3></div><span className="evidence-complete"><ClipboardCheck size={15} /> Grouper boundary</span></div>
      <div className="claim-columns">
        <div className="claim-column">
          <div className="claim-column__header"><span>Current submitted claim</span><strong>{finding.current_drg}</strong></div>
          {packet.case.claim.diagnoses.map(code => <ClaimLine code={code} label={diagnosisLabel(code)} key={code} />)}
          <div className="claim-payment"><span>Demo grouper payment</span><strong>{formatCurrency(currentPayment)}</strong></div>
        </div>
        <div className="claim-divider"><ArrowRight size={20} /></div>
        <div className="claim-column claim-column--candidate">
          <div className="claim-column__header"><span>Review hypothesis</span><strong>{finding.simulated_drg}</strong></div>
          {packet.case.claim.diagnoses.map(code => <ClaimLine code={code} label={diagnosisLabel(code)} key={code} />)}
          <ClaimLine code={addedDiagnosis} label={diagnosisLabel(addedDiagnosis)} added />
          <div className="claim-payment claim-payment--candidate"><span>Demo grouper payment</span><strong>{candidatePayment == null ? 'Unavailable' : formatCurrency(candidatePayment)}</strong></div>
        </div>
      </div>
      <div className="claim-warning"><ShieldCheck size={17} /><div><strong>Simulation is not a coding decision.</strong><span>The proposed code remains a review hypothesis until a qualified coder confirms documentation, coding criteria, sequencing, and POA.</span></div></div>
    </div>
  )
}

function ClaimLine({ code, label, added = false }: { code: string; label: string; added?: boolean }) {
  return <div className={added ? 'claim-line claim-line--added' : 'claim-line'}><code>{code}</code><span>{label}</span>{added && <b>Proposed</b>}</div>
}

function AuditTab({ packet, decisions, automationPlan }: { packet: ReviewPacket; decisions: ReviewDecision[]; automationPlan: AutomationPlan }) {
  const finding = packet.findings[0]
  if (!finding) return null
  const events = [
    ...decisions.map(decision => [formatTime(decision.decided_at), decision.actor_id, `${decision.action.replaceAll('_', ' ')} · ${decision.reason}`]),
    ['12:00', 'Rule engine', `${finding.rule_id} materialized from validated assertion.`],
    ['12:00', 'Grouper boundary', `${finding.grouper_version} completed baseline and candidate simulation.`],
    ['11:59', 'Ontology validator', `${packet.ontology.entities.length} entities, ${packet.ontology.relations.length} relations, and evidence lineage accepted.`],
    ['11:59', 'Mastra extraction', `${packet.evidence.length} narrative excerpt grounded to the source document.`],
    ['11:58', 'Review packet', `Contract ${packet.review_packet_schema_version} created · ${packet.provenance.record_hash.slice(0, 12)}…`],
    ['11:58', 'Automation policy', `Exception policy ${automationPlan.policy.version} prepared the one-click reviewer action.`],
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

function formatDate(value: string) {
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(new Date(value))
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }).format(new Date(value))
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat('en-US', { hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}

function formatCurrency(cents: number) {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(cents / 100)
}

function formatSignedCurrency(cents: number) {
  const amount = formatCurrency(Math.abs(cents))
  return `${cents >= 0 ? '+' : '-'}${amount}`
}

function proposedDiagnosis(finding: ReviewPacket['findings'][number]) {
  const diagnoses = finding.proposed_change.add_diagnoses
  return Array.isArray(diagnoses) && typeof diagnoses[0] === 'string' ? diagnoses[0] : 'Review required'
}

function diagnosisLabel(code: string) {
  const labels: Record<string, string> = {
    'A41.9': 'Sepsis, unspecified organism',
    'L89.154': 'Pressure ulcer of sacral region, stage 4',
  }
  return labels[code] ?? 'Submitted diagnosis'
}

function titleCase(value: string) {
  return value.replaceAll('_', ' ').replace(/\b\w/g, character => character.toUpperCase())
}
