import { describe, expect, it } from 'vitest'

import { verifyPacketHashFromText } from './packet-hash'
// Raw fixture text (number tokens preserved) — this is the byte source the engine hashed.
import primaryPacketRaw from './fixtures/review-packet.json?raw'
import secondPacketRaw from './fixtures/review-packet-2.json?raw'

describe('in-browser packet-hash verification', () => {
  it('recomputes the primary 3.5.0 fixture hash and matches the stored provenance.packet_hash', async () => {
    const result = await verifyPacketHashFromText(primaryPacketRaw)
    const stored = JSON.parse(primaryPacketRaw).provenance.packet_hash
    expect(result.claimed).toBe(stored)
    expect(result.computed).toBe(stored)
    expect(result.ok).toBe(true)
  })

  it('recomputes the second 3.5.0 fixture hash and matches the stored provenance.packet_hash', async () => {
    const result = await verifyPacketHashFromText(secondPacketRaw)
    const stored = JSON.parse(secondPacketRaw).provenance.packet_hash
    expect(result.claimed).toBe(stored)
    expect(result.computed).toBe(stored)
    expect(result.ok).toBe(true)
  })

  it('preserves whole-number float tokens (1.0) so it agrees with Python json.dumps', async () => {
    // If the verifier collapsed 1.0 -> 1 the primary fixture (which contains
    // "confidence":1.0) would fail parity; the passing primary test already guards this,
    // but assert the token survives directly for a clear regression signal.
    expect(primaryPacketRaw).toContain('1.0')
    const result = await verifyPacketHashFromText(primaryPacketRaw)
    expect(result.ok).toBe(true)
  })

  it('fails closed when the packet body is tampered with', async () => {
    const parsed = JSON.parse(primaryPacketRaw)
    parsed.case.patient_id = `${parsed.case.patient_id}-tampered`
    const result = await verifyPacketHashFromText(JSON.stringify(parsed))
    expect(result.ok).toBe(false)
    expect(result.computed).not.toBe(result.claimed)
  })

  it('fails closed when provenance.packet_hash is missing', async () => {
    const parsed = JSON.parse(primaryPacketRaw)
    delete parsed.provenance.packet_hash
    const result = await verifyPacketHashFromText(JSON.stringify(parsed))
    expect(result.claimed).toBeNull()
    expect(result.ok).toBe(false)
  })

  it('rejects NaN/Infinity to mirror Python allow_nan=False', async () => {
    await expect(verifyPacketHashFromText('{"confidence":NaN}')).rejects.toThrow(/allow_nan/i)
  })
})
