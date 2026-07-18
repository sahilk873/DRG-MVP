import { Check } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { GuidedTour } from './components/GuidedTour'
import { Shell } from './components/Shell'
import { CaseReview } from './views/CaseReview'
import { Governance } from './views/Governance'
import { Ingestion } from './views/Ingestion'
import { Overview } from './views/Overview'
import { ReviewQueue } from './views/ReviewQueue'
import type { ViewId } from './types'
import { humanOpportunities, primaryAutomationPlan, primaryReviewPacket } from './data'
import { BrowserDemoWorkflowGateway, type ReviewDecision, type ReviewerIdentity } from './workflow'

export default function App() {
  const [view, setView] = useState<ViewId>('overview')
  const [tourOpen, setTourOpen] = useState(false)
  const [tourStep, setTourStep] = useState(0)
  const [toast, setToast] = useState('')
  const [decisions, setDecisions] = useState<ReviewDecision[]>([])
  const workflowGateway = useMemo(() => new BrowserDemoWorkflowGateway(window.localStorage), [])
  const reviewer = useMemo<ReviewerIdentity>(() => ({
    actor_id: 'demo-coder-001',
    tenant_id: primaryReviewPacket.tenant.tenant_id,
    workspace_id: primaryReviewPacket.tenant.workspace_id,
    roles: ['coder'],
  }), [])

  const notify = useCallback((message: string) => setToast(message), [])

  useEffect(() => {
    let active = true
    workflowGateway.list(primaryReviewPacket, reviewer)
      .then(stored => { if (active) setDecisions(stored) })
      .catch(error => notify(error instanceof Error ? error.message : 'Unable to load review decisions'))
    return () => { active = false }
  }, [workflowGateway, reviewer, notify])

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(''), 3600)
    return () => window.clearTimeout(timer)
  }, [toast])

  const startTour = () => {
    setTourStep(0)
    setView('ingestion')
    setTourOpen(true)
  }

  const navigate = (nextView: ViewId) => {
    setView(nextView)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const recordDecision = useCallback((created: ReviewDecision) => {
    setDecisions(current => current.some(item => item.decision_id === created.decision_id) ? current : [...current, created])
  }, [])

  const resetDemoWorkflow = useCallback(async () => {
    try {
      await workflowGateway.reset(primaryReviewPacket, reviewer)
      setDecisions([])
      notify('Synthetic review workflow reset')
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Unable to reset the demo workflow')
    }
  }, [workflowGateway, reviewer, notify])

  const resolvedFindingIds = new Set(decisions.map(decision => decision.finding_id))
  const pendingReviewCount = humanOpportunities.filter(item => !resolvedFindingIds.has(item.id)).length

  return (
    <Shell activeView={view} onNavigate={navigate} onStartTour={startTour} reviewCount={pendingReviewCount}>
      {view === 'overview' && <Overview onNavigate={navigate} onStartTour={startTour} notify={notify} decisions={decisions} />}
      {view === 'queue' && <ReviewQueue onNavigate={navigate} notify={notify} decisions={decisions} onReset={resetDemoWorkflow} />}
      {view === 'case' && <CaseReview onNavigate={navigate} notify={notify} workflowGateway={workflowGateway} reviewer={reviewer} automationPlan={primaryAutomationPlan} decisions={decisions} onDecisionRecorded={recordDecision} />}
      {view === 'ingestion' && <Ingestion notify={notify} />}
      {view === 'governance' && <Governance />}
      {tourOpen && <GuidedTour step={tourStep} onClose={() => setTourOpen(false)} onStepChange={setTourStep} onNavigate={navigate} />}
      {toast && <div className="toast" role="status"><Check size={16} />{toast}</div>}
    </Shell>
  )
}
