import { Check, FileLock2, Fingerprint, ShieldCheck, UserRoundCheck, X } from 'lucide-react'

const controls = [
  ['Claim mutation', 'Blocked', 'No model or rule can submit or alter a claim.'],
  ['Evidence grounding', 'Required', 'Every clinical assertion resolves to an exact excerpt or source row.'],
  ['Schema drift', 'Fail closed', 'Unapproved provider changes stop the adapter before transformation.'],
  ['Human authorization', 'Required', 'A qualified reviewer owns the final coding or billing decision.'],
]

export function Governance() {
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
            <div><dt>Ontology</dt><dd>wound-care · 1.0.0</dd></div>
            <div><dt>Rule package</dt><dd>wound-care-v1 · approved</dd></div>
            <div><dt>Adapter</dt><dd>alpha-medical · 1.0.0</dd></div>
            <div><dt>Model route</dt><dd>provider-agnostic · recorded</dd></div>
            <div><dt>Audit chain</dt><dd><span className="inline-healthy" /> verified</dd></div>
          </dl>
          <div className="digest"><FileLock2 size={16} /><div><span>Run manifest digest</span><code>sha256:47f2…9b10</code></div></div>
        </div>
      </section>

      <div className="governance-note"><X size={16} /><span><strong>Deliberate non-capability:</strong> Encounter never treats model confidence as clinical truth, generates executable rules, or bypasses reviewer authorization.</span></div>
    </>
  )
}

function Responsibility({ kind, number, title, subtitle, items }: { kind: string; number: string; title: string; subtitle: string; items: string[] }) {
  return <article className={`responsibility responsibility--${kind}`}><div className="responsibility__top"><span>{number}</span>{kind === 'human' ? <UserRoundCheck size={20} /> : kind === 'engine' ? <Fingerprint size={20} /> : <ShieldCheck size={20} />}</div><h2>{title}</h2><p>{subtitle}</p><ul>{items.map(item => <li key={item}><Check size={14} />{item}</li>)}</ul></article>
}
