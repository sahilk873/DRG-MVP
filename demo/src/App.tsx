import { Check } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { GuidedTour } from './components/GuidedTour'
import { Shell } from './components/Shell'
import { CaseReview } from './views/CaseReview'
import { Governance } from './views/Governance'
import { Ingestion } from './views/Ingestion'
import { Overview } from './views/Overview'
import { ReviewQueue } from './views/ReviewQueue'
import type { ViewId } from './types'

export default function App() {
  const [view, setView] = useState<ViewId>('overview')
  const [tourOpen, setTourOpen] = useState(false)
  const [tourStep, setTourStep] = useState(0)
  const [toast, setToast] = useState('')

  const notify = useCallback((message: string) => setToast(message), [])

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

  return (
    <Shell activeView={view} onNavigate={navigate} onStartTour={startTour}>
      {view === 'overview' && <Overview onNavigate={navigate} onStartTour={startTour} notify={notify} />}
      {view === 'queue' && <ReviewQueue onNavigate={navigate} notify={notify} />}
      {view === 'case' && <CaseReview onNavigate={navigate} notify={notify} />}
      {view === 'ingestion' && <Ingestion notify={notify} />}
      {view === 'governance' && <Governance />}
      {tourOpen && <GuidedTour step={tourStep} onClose={() => setTourOpen(false)} onStepChange={setTourStep} onNavigate={navigate} />}
      {toast && <div className="toast" role="status"><Check size={16} />{toast}</div>}
    </Shell>
  )
}
