import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import App from './App'
import { primaryReviewPacket } from './data'
import { parseReviewPacket } from './review-packet'

describe('pitch demo', () => {
  it('loads the engine-generated packet with human review controls intact', () => {
    expect(primaryReviewPacket.review_packet_schema_version).toBe('1.0.0')
    expect(primaryReviewPacket.controls.claim_mutation_allowed).toBe(false)
    expect(primaryReviewPacket.controls.human_review_required).toBe(true)
    expect(primaryReviewPacket.findings[0]?.estimated_impact_cents).toBe(842000)
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
    expect(screen.getByRole('heading', { name: 'Review queue' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /review top case/i }))
    expect(screen.getByRole('heading', { name: /stage 4 sacral pressure injury absent from claim/i })).toBeInTheDocument()
    expect(screen.getByText('DEMO-290')).toBeInTheDocument()
    expect(screen.getByText('+$8,420')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: /claim comparison/i }))
    expect(screen.getByText('L89.154')).toBeInTheDocument()
    expect(screen.getByText('$18,420')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /route to coding review/i }))
    expect(screen.getByText(/assigned to coding review/i)).toBeInTheDocument()
  })
})
