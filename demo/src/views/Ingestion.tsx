import {
  ArrowRight,
  Check,
  Database,
  FileJson,
  FileSpreadsheet,
  Fingerprint,
  Network,
  ScanSearch,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { useEffect, useState } from 'react'

interface IngestionProps {
  notify: (message: string) => void
}

const files = [
  ['encounters.csv', '183 rows'],
  ['notes.csv', '2,418 rows'],
  ['claims.csv', '183 rows'],
  ['diagnoses.csv', '1,206 rows'],
  ['charges.csv', '8,941 rows'],
  ['wound_assessments.csv', '317 rows'],
]

const stages = [
  'Inspecting file structure',
  'Drafting canonical mappings',
  'Testing deterministic transforms',
  'Preparing adapter for approval',
]

export function Ingestion({ notify }: IngestionProps) {
  const [stage, setStage] = useState(-1)

  useEffect(() => {
    if (stage < 0 || stage >= stages.length) return
    const timer = window.setTimeout(() => setStage(value => value + 1), 650)
    return () => window.clearTimeout(timer)
  }, [stage])

  useEffect(() => {
    if (stage === stages.length) notify('Draft adapter ready · deterministic validation passed')
  }, [notify, stage])

  const running = stage >= 0 && stage < stages.length
  const complete = stage === stages.length

  return (
    <>
      <header className="view-header">
        <div>
          <span className="eyebrow">Adaptive data onboarding</span>
          <h1>Bring the folder. We learn the shape.</h1>
          <p>Profile a provider export, propose a reusable mapping, and validate it before the full dataset enters the encounter pipeline.</p>
        </div>
        <button className="button button--primary" disabled={running} onClick={() => setStage(0)} type="button">
          <ScanSearch size={17} /> {running ? stages[stage] : complete ? 'Profile again' : 'Profile demo export'}
        </button>
      </header>

      <section className="ingestion-grid">
        <div className="section-card source-folder">
          <div className="section-heading">
            <div><span className="panel-kicker">Provider drop</span><h2>alpha-july-export/</h2></div>
            <span className="status-badge status-badge--healthy"><span /> Deidentified</span>
          </div>
          <div className="folder-summary">
            <Database size={22} />
            <div><strong>11,918 source records</strong><small>48.7 MB · received Jul 17, 2026</small></div>
          </div>
          <div className="file-list">
            {files.map(([name, rows]) => (
              <div className="file-row" key={name}>
                {name.includes('assessment') ? <FileSpreadsheet size={16} /> : <FileJson size={16} />}
                <span>{name}</span><small>{rows}</small><Check size={14} />
              </div>
            ))}
          </div>
          <div className="fingerprint-row"><Fingerprint size={16} /><span>Schema fingerprint</span><code>sha256:2c91…7ad4</code></div>
        </div>

        <div className="onboarding-flow">
          <div className="section-card onboarding-progress">
            <div className="section-heading">
              <div><span className="panel-kicker">Adapter factory</span><h2>Onboarding run</h2></div>
              <span className={running ? 'status-badge status-badge--running' : complete ? 'status-badge status-badge--healthy' : 'status-badge'}><span /> {running ? 'Learning' : complete ? 'Validated' : 'Ready'}</span>
            </div>
            <div className="onboarding-stages">
              {stages.map((label, index) => {
                const done = stage > index || complete
                const active = stage === index
                return <div className={active ? 'onboarding-stage onboarding-stage--active' : 'onboarding-stage'} key={label}>
                  <span>{done ? <Check size={13} /> : index + 1}</span>
                  <div><strong>{label}</strong><small>{index === 0 ? 'bounded metadata + representative sample' : index === 1 ? 'Mastra agent · declarative output only' : index === 2 ? 'row counts, types, keys, and ontology contract' : 'versioned human approval checkpoint'}</small></div>
                </div>
              })}
            </div>
          </div>

          <div className="section-card mapping-card">
            <div className="section-heading"><div><span className="panel-kicker">Draft adapter v1</span><h2>Canonical mappings</h2></div><span className="mapping-score">96% mapped</span></div>
            <div className="mapping-table">
              <div className="mapping-row mapping-row--header"><span>Provider field</span><span>Canonical concept</span><span>Method</span></div>
              <Mapping source="encounters.enc_id" target="Encounter.encounter_id" method="Direct" />
              <Mapping source="wound_assessments.stage" target="PressureInjury.stage" method="Value set" />
              <Mapping source="wound_assessments.site" target="AnatomicalLocation" method="Normalize" />
              <Mapping source="wound_assessments.poa" target="Assertion.present_on_admission" method="Value set" />
            </div>
            <button className="text-button" type="button">Inspect all 38 mappings <ArrowRight size={15} /></button>
          </div>
        </div>
      </section>

      <section className="data-boundary">
        <div className="boundary-heading"><span className="panel-kicker">Purpose-built trust boundary</span><h2>The agent learns the map. Deterministic software moves the data.</h2></div>
        <div className="boundary-lanes">
          <div className="boundary-card boundary-card--agent"><Sparkles size={19} /><div><span>Control plane</span><strong>Bounded profiling + mapping proposal</strong><small>The model sees field metadata and limited representative samples—not the unrestricted bulk dataset.</small></div></div>
          <ArrowRight className="boundary-arrow" size={20} />
          <div className="boundary-card"><ShieldCheck size={19} /><div><span>Approval gate</span><strong>Schema, mapping, and policy validation</strong><small>Unknown fields, drift, or invalid ontology relationships fail closed.</small></div></div>
          <ArrowRight className="boundary-arrow" size={20} />
          <div className="boundary-card"><Network size={19} /><div><span>Data plane</span><strong>Repeatable encounter transformation</strong><small>The approved adapter processes every row with source-level provenance.</small></div></div>
        </div>
      </section>
    </>
  )
}

function Mapping({ source, target, method }: { source: string; target: string; method: string }) {
  return <div className="mapping-row"><code>{source}</code><span><ArrowRight size={13} />{target}</span><b>{method}</b></div>
}
