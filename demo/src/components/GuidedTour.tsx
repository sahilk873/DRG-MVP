import { ArrowLeft, ArrowRight, Check, X } from 'lucide-react'
import { useEffect } from 'react'

import { tourSteps } from '../data'

interface GuidedTourProps {
  step: number
  onStepChange: (step: number) => void
  onClose: () => void
  onNavigate: (view: (typeof tourSteps)[number]['view']) => void
}

export function GuidedTour({ step, onStepChange, onClose, onNavigate }: GuidedTourProps) {
  const active = tourSteps[step]
  const isLast = step === tourSteps.length - 1

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [onClose])

  const advance = () => {
    if (isLast) {
      onClose()
      return
    }
    const next = step + 1
    onStepChange(next)
    onNavigate(tourSteps[next].view)
  }

  return (
    <div className="tour-overlay" role="dialog" aria-modal="true" aria-labelledby="tour-title">
      <button className="tour-backdrop" onClick={onClose} aria-label="Close guided demo" />
      <section className="tour-modal">
        <div className="tour-progress" aria-label={`Step ${step + 1} of ${tourSteps.length}`}>
          {tourSteps.map((item, index) => (
            <button
              className={index === step ? 'tour-dot tour-dot--active' : index < step ? 'tour-dot tour-dot--done' : 'tour-dot'}
              key={item.eyebrow}
              onClick={() => {
                onStepChange(index)
                onNavigate(item.view)
              }}
              aria-label={`Go to ${item.eyebrow}`}
              type="button"
            >
              {index < step && <Check size={11} />}
            </button>
          ))}
        </div>
        <button className="icon-button tour-close" onClick={onClose} aria-label="Close guided demo" type="button">
          <X size={18} />
        </button>
        <span className="eyebrow">{active.eyebrow}</span>
        <h2 id="tour-title">{active.title}</h2>
        <p>{active.body}</p>
        <div className="tour-proof"><Check size={16} /> {active.proof}</div>
        <div className="tour-actions">
          <button
            className="button button--quiet"
            disabled={step === 0}
            onClick={() => {
              const previous = step - 1
              onStepChange(previous)
              onNavigate(tourSteps[previous].view)
            }}
            type="button"
          >
            <ArrowLeft size={16} /> Back
          </button>
          <span>{step + 1} / {tourSteps.length}</span>
          <button className="button button--dark" onClick={advance} type="button">
            {isLast ? 'Finish demo' : 'Next'} {!isLast && <ArrowRight size={16} />}
          </button>
        </div>
      </section>
    </div>
  )
}
