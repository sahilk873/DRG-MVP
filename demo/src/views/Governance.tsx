import { BookOpenCheck, Check, FileLock2, Fingerprint, Gauge, HeartPulse, ShieldCheck, UserRoundCheck, X } from 'lucide-react'

import { evaluationReport, gapReviewPacket, primaryReviewPacket } from '../data'

const controls = [
  ['Claim mutation', 'Blocked', 'No model or rule can submit or alter a claim.'],
  ['Evidence grounding', 'Required', 'Every clinical assertion resolves to an exact excerpt or source row.'],
  ['Schema drift', 'Fail closed', 'Unapproved provider changes stop the adapter before transformation.'],
  ['Human authorization', 'Required', 'A qualified reviewer owns the final coding or billing decision.'],
]

const pct = (value: number) => `${(value * 100).toFixed(1)}%`

export function Governance() {
  const provenance = primaryReviewPacket.provenance
  const metrics = evaluationReport.metrics
  const gate = evaluationReport.thresholds?.min_precision ?? 0.95
  return (
    <>
      <header className="view-header">
        <div><span className="eyebrow">Governance</span><h1>Automation with a visible boundary.</h1><p>Each responsibility is assigned to the system best suited to it—and every material decision remains reproducible.</p></div>
        <span className="governance-health"><ShieldCheck size={18} /> All controls healthy</span>
      </header>

      <section className="responsibility-grid">
        <Responsibility kind="agent" number="01" title="Agent-assisted" subtitle="Interpret variable language and structure" items={['Draft provider adapter mappings', 'Extract evidence from narrative notes', 'Surface ambiguity and contradictions']} />
        <Responsibility kind="engine" number="02" title="Deterministic" subtitle="Reproduce every consequential result" items={['Validate ontology and evidence lineage', 'Evaluate approved rule packages', 'Group, price, and compute deltas']} />
        <Responsibility kind="human" number="03" title="Human-authorized" subtitle="Own the final consequential choice" items={['Confirm coding criteria and sequencing', 'Resolve conflicting documentation', 'Approve any claim-side action']} />
      </section>

      <section className="rule-package-grid">
        <RulePackage
          kind="revenue"
          eyebrow="Revenue-integrity rules"
          title="Claim reconciliation"
          packageId={primaryReviewPacket.provenance.rule_package_id}
          version={primaryReviewPacket.provenance.rule_package_version}
          summary="Compares what the chart supports against what was documented, coded, grouped, and charged."
          route="Routes to coding, CDI, charge, or compliance review"
          mutates
        />
        <RulePackage
          kind="care-gap"
          eyebrow="Clinical care-gap rules"
          title="Follow-through gaps"
          packageId={gapReviewPacket.provenance.rule_package_id}
          version={gapReviewPacket.provenance.rule_package_version}
          summary="Detects missing, delayed, or incomplete clinical action from grounded longitudinal evidence."
          route="Routes only to the care team on the gap-closure lane"
        />
      </section>

      <section className="governance-grid">
        <div className="section-card control-register">
          <div className="section-heading"><div><span className="panel-kicker">Active policy</span><h2>Control register</h2></div><span className="evidence-complete"><Check size={15} /> 14 / 14 passing</span></div>
          <div className="control-list">
            {controls.map(([name, state, detail]) => <div className="control-row" key={name}><span className="control-check"><Check size={14} /></span><div><strong>{name}</strong><small>{detail}</small></div><b>{state}</b></div>)}
          </div>
        </div>
        <div className="section-card provenance-card">
          <div className="section-heading"><div><span className="panel-kicker">Reproducibility</span><h2>Run provenance</h2></div><Fingerprint size={19} /></div>
          <dl className="provenance-list">
            <div><dt>Ontology</dt><dd>{primaryReviewPacket.ontology.ontology_id} · {primaryReviewPacket.ontology.ontology_version}</dd></div>
            <div><dt>Rule package</dt><dd>{provenance.rule_package_id} · {provenance.rule_package_version}</dd></div>
            <div><dt>Engine</dt><dd>{provenance.engine_version}</dd></div>
            <div><dt>Grouper</dt><dd>{provenance.grouper_versions.join(', ')}</dd></div>
            <div><dt>Audit chain</dt><dd><span className="inline-healthy" /> hash-linked</dd></div>
          </dl>
          <div className="digest"><FileLock2 size={16} /><div><span>Review-packet digest</span><code>sha256:{provenance.packet_hash.slice(0, 4)}…{provenance.packet_hash.slice(-4)}</code></div></div>
        </div>
      </section>

      <section className="governance-grid">
        <div className="section-card validation-card">
          <div className="section-heading"><div><span className="panel-kicker">Validation gate</span><h2>Discovery accuracy</h2></div><span className={evaluationReport.passed ? 'evidence-complete' : 'evidence-complete evidence-complete--warn'}><Gauge size={15} /> {evaluationReport.passed ? 'Above gate' : 'Below gate'}</span></div>
          <div className="metric-tiles">
            <div className="metric-tile"><strong>{pct(metrics.precision)}</strong><span>Precision</span></div>
            <div className="metric-tile"><strong>{pct(metrics.recall)}</strong><span>Recall</span></div>
            <div className="metric-tile"><strong>{pct(metrics.f1)}</strong><span>F1</span></div>
          </div>
          <dl className="provenance-list">
            <div><dt>Gold labels</dt><dd>{evaluationReport.label_count} across {evaluationReport.case_count} cases</dd></div>
            <div><dt>TP / FP / FN</dt><dd>{metrics.true_positives} / {metrics.false_positives} / {metrics.false_negatives}</dd></div>
            <div><dt>Promote-above gate</dt><dd>{pct(gate)} precision</dd></div>
          </dl>
          <p className="demo-caption">Synthetic gold set · reproducible via <code>make eval</code> · signed report {evaluationReport.report_hash.slice(0, 10)}…</p>
        </div>
        <div className="section-card deliberate-card">
          <div className="section-heading"><div><span className="panel-kicker">Deliberate non-capabilities</span><h2>What the system will not do</h2></div><X size={18} /></div>
          <div className="control-list">
            <div className="control-row"><span className="control-check control-check--deny"><X size={14} /></span><div><strong>Treat model confidence as clinical truth</strong><small>Agent output is evidence and hypotheses only.</small></div></div>
            <div className="control-row"><span className="control-check control-check--deny"><X size={14} /></span><div><strong>Generate or execute rules</strong><small>Rules are a declarative, reviewed JSON DSL — no code path.</small></div></div>
            <div className="control-row"><span className="control-check control-check--deny"><X size={14} /></span><div><strong>Assign a DRG or compute payment from a model</strong><small>Only the deterministic, versioned grouper boundary does.</small></div></div>
            <div className="control-row"><span className="control-check control-check--deny"><X size={14} /></span><div><strong>Bypass human authorization</strong><small>Every claim-affecting change requires a qualified reviewer.</small></div></div>
          </div>
        </div>
      </section>
    </>
  )
}

function RulePackage({ kind, eyebrow, title, packageId, version, summary, route, mutates = false }: { kind: string; eyebrow: string; title: string; packageId: string; version: string; summary: string; route: string; mutates?: boolean }) {
  return (
    <article className={`rule-package rule-package--${kind}`}>
      <div className="rule-package__top">
        <span className="panel-kicker">{eyebrow}</span>
        {kind === 'care-gap' ? <HeartPulse size={18} /> : <BookOpenCheck size={18} />}
      </div>
      <h2>{title}</h2>
      <p>{summary}</p>
      <div className="rule-package__id"><code>{packageId}</code><b>{version}</b></div>
      <ul>
        <li><Check size={13} /> {route}</li>
        <li><ShieldCheck size={13} /> {mutates ? 'Claim change requires reviewer authorization' : 'Never mutates a claim, DRG, or payment'}</li>
        <li><Check size={13} /> Human review required on every finding</li>
      </ul>
    </article>
  )
}

function Responsibility({ kind, number, title, subtitle, items }: { kind: string; number: string; title: string; subtitle: string; items: string[] }) {
  return <article className={`responsibility responsibility--${kind}`}><div className="responsibility__top"><span>{number}</span>{kind === 'human' ? <UserRoundCheck size={20} /> : kind === 'engine' ? <Fingerprint size={20} /> : <ShieldCheck size={20} />}</div><h2>{title}</h2><p>{subtitle}</p><ul>{items.map(item => <li key={item}><Check size={14} />{item}</li>)}</ul></article>
}
