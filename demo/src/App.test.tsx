import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import App from './App'
import { primaryReviewPacket } from './data'
import { parseReviewPacket } from './review-packet'

describe('pitch demo', () => {
  beforeEach(() => window.localStorage.clear())

  it('loads the engine-generated packet with human review controls intact', () => {
    expect(primaryReviewPacket.review_packet_schema_version).toBe('3.5.0')
    expect(primaryReviewPacket.tenant.tenant_id).toBe('tenant-demo-alpha')
    expect(primaryReviewPacket.controls.claim_mutation_allowed).toBe(false)
    expect(primaryReviewPacket.controls.human_review_required).toBe(true)
    expect(primaryReviewPacket.findings[0]?.estimated_impact_cents).toBe(842000)
  })

  it('carries a deterministic, hash-covered ROI rollup in the packet', () => {
    const summary = primaryReviewPacket.impact_summary
    expect(summary.net_estimated_impact_cents).toBe(842000)
    expect(summary.positive_opportunity_cents).toBe(842000)
    expect(summary.currency).toBe('USD')
    // A 3.0.0-shaped packet without impact_summary must fail closed.
    const legacy = structuredClone(primaryReviewPacket) as unknown as Record<string, unknown>
    legacy.review_packet_schema_version = '3.0.0'
    delete legacy.impact_summary
    expect(() => parseReviewPacket(legacy)).toThrow()
  })

  it('renders the engine-derived impact figure on the overview, not a hardcoded literal', () => {
    render(<App />)
    // $8,420 is derived from impact_summary.net_estimated_impact_cents (842000 cents).
    expect(screen.getAllByText('$8,420').length).toBeGreaterThan(0)
    expect(screen.queryByText('$284,650')).not.toBeInTheDocument()
  })

  it('fails closed when a consumer attempts to enable claim mutation', () => {
    const unsafe = structuredClone(primaryReviewPacket) as unknown as Record<string, unknown>
    unsafe.controls = { ...primaryReviewPacket.controls, claim_mutation_allowed: true }
    expect(() => parseReviewPacket(unsafe)).toThrow()
  })

  it('moves through the guided product story and closes with Escape', async () => {
    const user = userEvent.setup()
    render(<App />)

    expect(screen.getByRole('heading', { name: /find the truth between the chart and the claim/i })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /start guided demo/i }))
    expect(screen.getByRole('dialog')).toHaveTextContent(/meet providers where their data already lives/i)
    await user.click(screen.getByRole('button', { name: /next/i }))
    expect(screen.getByRole('dialog')).toHaveTextContent(/turn fragmented records into one clinical encounter/i)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('navigates from the review queue to the deterministic case packet', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(within(screen.getByRole('navigation', { name: /product navigation/i })).getByRole('button', { name: /review queue/i }))
    expect(screen.getByRole('heading', { name: /4 decisions need a person/i })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /review top case/i }))
    expect(screen.getByRole('heading', { name: /stage 4 sacral pressure injury absent from claim/i })).toBeInTheDocument()
    expect(screen.getByText('DEMO-290')).toBeInTheDocument()
    expect(screen.getByText('+$8,420')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: /claim comparison/i }))
    expect(screen.getAllByText('L89.154').length).toBeGreaterThan(0)
    expect(screen.getByText('$18,420')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /confirm & send to coding/i }))
    expect(screen.getByText(/recommendation sent to the governed coding workflow/i)).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: /audit trail/i }))
    expect(screen.getAllByText(/evidence and poa confirmed/i)).not.toHaveLength(0)

    await user.click(within(screen.getByRole('navigation', { name: /product navigation/i })).getByRole('button', { name: /review queue/i }))
    expect(screen.getByRole('heading', { name: /3 decisions need a person/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /open stage 4 sacral pressure injury absent from claim/i })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /all/i }))
    expect(screen.getByText('Completed')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /reset demo/i }))
    expect(screen.getByRole('heading', { name: /4 decisions need a person/i })).toBeInTheDocument()
  })

  it('opens the hospital-acquired second case with its derivation trace and POA lineage', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(within(screen.getByRole('navigation', { name: /product navigation/i })).getByRole('button', { name: /review queue/i }))
    await user.click(screen.getByRole('button', { name: /open hospital-acquired stage 4 pressure injury absent from claim/i }))
    expect(screen.getByRole('heading', { name: /hospital-acquired stage 4 pressure injury absent from claim/i })).toBeInTheDocument()
    // Trust boundary strip is always visible and sourced from packet.controls.
    expect(screen.getByText(/claim mutation:/i)).toBeInTheDocument()
    expect(screen.getAllByText('BLOCKED').length).toBeGreaterThan(0)
    // The packet hash is independently re-verified in the browser and shown on the trust strip.
    expect(await screen.findByText('RE-VERIFIED')).toBeInTheDocument()
    // Derivation trace explains the DRG change on the claim tab.
    await user.click(screen.getByRole('tab', { name: /claim comparison/i }))
    expect(screen.getByRole('heading', { name: /why the drg changed/i })).toBeInTheDocument()
    expect(screen.getAllByText(/tier selection/i).length).toBeGreaterThan(0)
  })

  it('showcases the accuracy backtest and reproducible provenance in governance', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(within(screen.getByRole('navigation', { name: /product navigation/i })).getByRole('button', { name: /governance/i }))
    expect(screen.getByRole('heading', { name: /discovery accuracy/i })).toBeInTheDocument()
    // Precision/recall/F1 render from the signed eval fixture, not a hardcoded number.
    expect(screen.getAllByText('100.0%').length).toBeGreaterThan(0)
    // Provenance is derived from the real packet (current ontology version, not a stale literal).
    expect(screen.getByText(/1\.1\.0-draft/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /what the system will not do/i })).toBeInTheDocument()
  })

  it('renders the clinical care-gaps lens with engine-derived worklist and closure metrics', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(within(screen.getByRole('navigation', { name: /product navigation/i })).getByRole('button', { name: /^care gaps$/i }))
    expect(screen.getByRole('heading', { name: /close the gaps between the plan and the care/i })).toBeInTheDocument()
    // Worklist dashboard (gap_worklist): 2 open high-risk gaps (CG-INF-002 urgent + CG-DET-001
    // same-day deterioration), 8.0d avg expected window (mean of the [2, 14] rule-configured
    // timing windows — the intended action window, NOT observed lateness), same_day top reason.
    expect(screen.getByText('High-risk open gaps')).toBeInTheDocument()
    expect(screen.getByText('Avg expected window')).toBeInTheDocument()
    expect(screen.getAllByText('2').length).toBeGreaterThan(0)
    expect(screen.getByText('8.0d')).toBeInTheDocument()
    // Closure performance: 25% closed (1 of 4 gaps excepted), 0d median, top barrier surfaced.
    expect(screen.getByText('Gaps closed')).toBeInTheDocument()
    expect(screen.getByText('25%')).toBeInTheDocument()
    // Metrics are explicitly labelled illustrative/operational (is_estimate).
    expect(screen.getAllByText('illustrative').length).toBeGreaterThan(0)
    // The anchor urgent gap is listed in the care-gap lane.
    expect(screen.getByText(/no size reduction after two weeks/i)).toBeInTheDocument()
    // Care gaps can never touch the claim.
    expect(screen.getByText(/care gaps never touch the claim/i)).toBeInTheDocument()
  })

  it('drills into the DFU episode timeline with grounded evidence → recommended-action chain', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(within(screen.getByRole('navigation', { name: /product navigation/i })).getByRole('button', { name: /episode drilldown/i }))
    expect(screen.getByRole('heading', { name: /diabetic foot ulcer episode/i })).toBeInTheDocument()
    // Longitudinal timeline points render Day 0 and Day 14 with grounded sizes.
    expect(screen.getByText('Day 0')).toBeInTheDocument()
    expect(screen.getByText('Day 14')).toBeInTheDocument()
    // Stalled healing (no reduction) is flagged on the timeline.
    expect(screen.getAllByText(/no change/i).length).toBeGreaterThan(0)
    // The evidence → expected → actual → impact → recommended chain is present.
    expect(screen.getByText('Evidence')).toBeInTheDocument()
    expect(screen.getByText('Expected action')).toBeInTheDocument()
    expect(screen.getByText('Recommended next step')).toBeInTheDocument()
    // Grounded excerpt from Day 14 assessment (no size reduction).
    expect(screen.getAllByText(/ulcer remains 2\.4 x 1\.8 cm/i).length).toBeGreaterThan(0)
    // The trust strip proves claim mutation stays blocked on the care-gap lens.
    expect(screen.getAllByText('BLOCKED').length).toBeGreaterThan(0)
  })

  it('shows both governed rule packages — revenue and clinical care gap — without dropping existing assertions', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(within(screen.getByRole('navigation', { name: /product navigation/i })).getByRole('button', { name: /governance/i }))
    // Existing revenue governance content is intact.
    expect(screen.getByRole('heading', { name: /discovery accuracy/i })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /what the system will not do/i })).toBeInTheDocument()
    // Both rule packages are now shown additively.
    expect(screen.getByRole('heading', { name: /claim reconciliation/i })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /follow-through gaps/i })).toBeInTheDocument()
    expect(screen.getByText('wound-care-clinical-care-gap')).toBeInTheDocument()
    expect(screen.getByText(/never mutates a claim, drg, or payment/i)).toBeInTheDocument()
  })

  it('restores governed decisions from browser persistence across remounts', async () => {
    const user = userEvent.setup()
    const first = render(<App />)

    await user.click(within(screen.getByRole('navigation', { name: /product navigation/i })).getByRole('button', { name: /review queue/i }))
    await user.click(screen.getByRole('button', { name: /review top case/i }))
    await user.click(screen.getByRole('button', { name: /confirm & send to coding/i }))
    first.unmount()

    render(<App />)
    await user.click(within(screen.getByRole('navigation', { name: /product navigation/i })).getByRole('button', { name: /review queue/i }))
    expect(await screen.findByRole('heading', { name: /3 decisions need a person/i })).toBeInTheDocument()
  })
})
