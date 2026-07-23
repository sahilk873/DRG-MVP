// In-browser packet-hash verification.
//
// Recomputes a review packet's `provenance.packet_hash` client-side and proves it
// matches the value the deterministic Python engine stored. This is a cross-language
// parity check against `audit.canonical_hash` / `review_packet.verify_review_packet_hash`
// in `src/revenue_integrity/`.
//
// Python canonicalization (the contract we must reproduce byte-for-byte):
//   json.dumps(payload, sort_keys=True, separators=(",", ":"),
//              ensure_ascii=False, allow_nan=False).encode("utf-8")
//   -> sha256 -> hexdigest
// with `provenance.packet_hash` removed before hashing.
//
// Two subtleties make a naive `JSON.stringify` diverge from Python and are handled here:
//   1. Float vs int: Python preserves numeric type, so a JSON `1.0` serializes back as
//      "1.0", whereas JavaScript's Number loses the distinction (`JSON.stringify(1.0)`
//      yields "1"). We therefore parse from the RAW packet text with a number-preserving
//      parser that keeps each number's exact source token.
//   2. Non-ASCII: Python uses ensure_ascii=False (raw UTF-8), which matches JavaScript's
//      default string serialization; both escape the same control/quote/backslash set.

// A JSON value whose numbers retain their exact source token so canonical
// serialization can reproduce Python's float/int representation exactly.
type PreservedNumber = { readonly __canonicalNumber: string }
type PreservedValue =
  | null
  | boolean
  | string
  | PreservedNumber
  | PreservedValue[]
  | { [key: string]: PreservedValue }

function isPreservedNumber(value: PreservedValue): value is PreservedNumber {
  return typeof value === 'object' && value !== null && '__canonicalNumber' in value
}

// Minimal recursive-descent JSON parser that preserves number source tokens. Standard
// JSON only (the packet fixtures are standard JSON); rejects NaN/Infinity to mirror
// Python's allow_nan=False.
function parsePreserving(text: string): PreservedValue {
  let index = 0

  const error = (message: string): never => {
    throw new Error(`packet-hash JSON parse error at index ${index}: ${message}`)
  }

  const skipWhitespace = (): void => {
    while (index < text.length) {
      const code = text.charCodeAt(index)
      // space, tab, newline, carriage return
      if (code === 0x20 || code === 0x09 || code === 0x0a || code === 0x0d) index++
      else break
    }
  }

  const parseString = (): string => {
    if (text[index] !== '"') error('expected string')
    const start = index
    index++
    while (index < text.length) {
      const char = text[index]
      if (char === '\\') {
        index += 2
        continue
      }
      if (char === '"') {
        index++
        // Re-decode the raw JSON string token via JSON.parse for exact escape semantics.
        return JSON.parse(text.slice(start, index)) as string
      }
      index++
    }
    return error('unterminated string')
  }

  const parseNumber = (): PreservedNumber => {
    const start = index
    if (text[index] === '-') index++
    while (index < text.length && /[0-9]/.test(text[index]!)) index++
    if (text[index] === '.') {
      index++
      while (index < text.length && /[0-9]/.test(text[index]!)) index++
    }
    if (text[index] === 'e' || text[index] === 'E') {
      index++
      if (text[index] === '+' || text[index] === '-') index++
      while (index < text.length && /[0-9]/.test(text[index]!)) index++
    }
    const token = text.slice(start, index)
    if (token === '' || token === '-') error('invalid number')
    return { __canonicalNumber: token }
  }

  const parseValue = (): PreservedValue => {
    skipWhitespace()
    const char = text[index]
    if (char === undefined) return error('unexpected end of input')
    if (char === '{') {
      index++
      const object: { [key: string]: PreservedValue } = {}
      skipWhitespace()
      if (text[index] === '}') {
        index++
        return object
      }
      for (;;) {
        skipWhitespace()
        const key = parseString()
        skipWhitespace()
        if (text[index] !== ':') error('expected ":"')
        index++
        object[key] = parseValue()
        skipWhitespace()
        const next = text[index]
        if (next === ',') {
          index++
          continue
        }
        if (next === '}') {
          index++
          return object
        }
        return error('expected "," or "}"')
      }
    }
    if (char === '[') {
      index++
      const array: PreservedValue[] = []
      skipWhitespace()
      if (text[index] === ']') {
        index++
        return array
      }
      for (;;) {
        array.push(parseValue())
        skipWhitespace()
        const next = text[index]
        if (next === ',') {
          index++
          continue
        }
        if (next === ']') {
          index++
          return array
        }
        return error('expected "," or "]"')
      }
    }
    if (char === '"') return parseString()
    if (char === 't') {
      if (text.slice(index, index + 4) !== 'true') error('invalid literal')
      index += 4
      return true
    }
    if (char === 'f') {
      if (text.slice(index, index + 5) !== 'false') error('invalid literal')
      index += 5
      return false
    }
    if (char === 'n') {
      if (text.slice(index, index + 4) !== 'null') error('invalid literal')
      index += 4
      return null
    }
    if (char === 'N' || char === 'I' || (char === '-' && text[index + 1] === 'I')) {
      return error('NaN/Infinity is not permitted (allow_nan=False)')
    }
    if (char === '-' || /[0-9]/.test(char)) return parseNumber()
    return error(`unexpected character ${JSON.stringify(char)}`)
  }

  const result = parseValue()
  skipWhitespace()
  if (index !== text.length) error('trailing content after JSON value')
  return result
}

// Serialize a preserved value with Python's canonical form: keys sorted, no whitespace
// (separators=(",", ":")), strings escaped with JavaScript's default (== Python
// ensure_ascii=False for the escape set), numbers emitted from their exact source token.
function canonicalize(value: PreservedValue): string {
  if (value === null) return 'null'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'string') return JSON.stringify(value)
  if (isPreservedNumber(value)) return value.__canonicalNumber
  if (Array.isArray(value)) return `[${value.map(canonicalize).join(',')}]`
  const keys = Object.keys(value).sort()
  return `{${keys.map(key => `${JSON.stringify(key)}:${canonicalize(value[key] as PreservedValue)}`).join(',')}}`
}

async function sha256Hex(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text)
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest))
    .map(byte => byte.toString(16).padStart(2, '0'))
    .join('')
}

export interface PacketHashVerification {
  ok: boolean
  claimed: string | null
  computed: string
}

// Recompute the canonical hash of a review packet from its RAW JSON text, matching
// Python's `audit.canonical_hash`. The packet's `provenance.packet_hash` is removed
// before hashing (mirroring `verify_review_packet_hash`), then the result is compared
// to the stored value. Operating on raw text preserves float tokens (e.g. "1.0") that a
// parsed Number would collapse, guaranteeing byte-exact cross-language parity.
export async function verifyPacketHashFromText(rawJson: string): Promise<PacketHashVerification> {
  const parsed = parsePreserving(rawJson)
  if (Array.isArray(parsed) || parsed === null || typeof parsed !== 'object' || isPreservedNumber(parsed)) {
    throw new Error('packet-hash: packet must be a JSON object')
  }
  const provenance = parsed.provenance
  let claimed: string | null = null
  if (provenance && typeof provenance === 'object' && !Array.isArray(provenance) && !isPreservedNumber(provenance)) {
    const stored = (provenance as { [key: string]: PreservedValue }).packet_hash
    claimed = typeof stored === 'string' ? stored : null
    const stripped: { [key: string]: PreservedValue } = {}
    for (const [key, entry] of Object.entries(provenance as { [key: string]: PreservedValue })) {
      if (key !== 'packet_hash') stripped[key] = entry
    }
    parsed.provenance = stripped
  }
  const computed = await sha256Hex(canonicalize(parsed))
  return { ok: claimed !== null && computed === claimed, claimed, computed }
}
