import { AlertTriangle, Check } from 'lucide-react'

import type { EpisodeTimelinePoint } from '../gap-episode'

interface EpisodeTimelineProps {
  points: EpisodeTimelinePoint[]
  // finding subject_ids that anchor the gap — used to highlight the missed-reassessment window.
  highlightAssessmentIds?: string[]
}

// Longitudinal DFU episode: Day 0 / 7 / 14 / 28 wound-size trend, sourced entirely from the
// packet ontology entities + dated assertions + evidence timestamps. Stalled-healing points
// (no size reduction) and the highlighted missed-reassessment window are flagged visually; the
// area bar is scaled to the largest area in the episode. Nothing here is a clinical decision.
export function EpisodeTimeline({ points, highlightAssessmentIds = [] }: EpisodeTimelineProps) {
  const maxArea = points.reduce((max, point) => Math.max(max, point.areaCm2 ?? 0), 0) || 1
  const highlight = new Set(highlightAssessmentIds)

  return (
    <div className="episode-timeline" aria-label="Longitudinal wound episode timeline">
      <div className="episode-track">
        {points.map(point => {
          const heightPct = point.areaCm2 == null ? 0 : Math.max(8, Math.round((point.areaCm2 / maxArea) * 100))
          const flagged = highlight.has(point.assessmentId)
          return (
            <div
              className={`episode-point${point.stalled ? ' episode-point--stalled' : ''}${flagged ? ' episode-point--flagged' : ''}`}
              key={point.assessmentId}
            >
              <div className="episode-point__bar-track">
                <span className="episode-point__bar" style={{ height: `${heightPct}%` }} />
              </div>
              <strong className="episode-point__area">{point.areaCm2 == null ? '—' : `${point.areaCm2} cm²`}</strong>
              <span className="episode-point__size">
                {point.lengthCm == null || point.widthCm == null ? 'no measurement' : `${point.lengthCm} × ${point.widthCm} cm`}
              </span>
              <span className={`episode-point__trend${point.stalled ? ' episode-point__trend--stalled' : ''}`}>
                {point.areaDeltaPct == null
                  ? 'baseline'
                  : point.areaDeltaPct === 0
                    ? 'no change'
                    : `${point.areaDeltaPct > 0 ? '+' : ''}${point.areaDeltaPct}%`}
                {point.stalled ? <AlertTriangle size={11} /> : point.areaDeltaPct != null ? <Check size={11} /> : null}
              </span>
              <span className="episode-point__day">
                {point.dayOffset == null ? point.label : `Day ${point.dayOffset}`}
              </span>
              <time className="episode-point__date">{point.recordedAt ? formatDay(point.recordedAt) : ''}</time>
              {flagged && <span className="episode-point__flag">Missed reassessment</span>}
            </div>
          )
        })}
      </div>
      <div className="episode-legend">
        <span><i className="episode-dot episode-dot--healing" /> Healing (area down)</span>
        <span><i className="episode-dot episode-dot--stalled" /> Stalled / worsening</span>
        <span><i className="episode-dot episode-dot--flagged" /> Care-gap window</span>
      </div>
    </div>
  )
}

function formatDay(value: string) {
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(new Date(value))
}
