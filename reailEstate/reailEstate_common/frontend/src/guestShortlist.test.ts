import { describe, expect, it } from 'vitest'
import { newDraft, type Snapshot } from './buyer'
import { decodeGuestTrial, encodeGuestTrial, type GuestTrial } from './guestShortlist'

const query = () => ({ ...newDraft(), home: { ...newDraft().home, configurations: ['2BHK'] }, budget: { currency: 'INR' as const, purchaseMaximum: 20000000, isHardConstraint: true } })
const snapshot: Snapshot = {
  id: 'guest-test', status: 'ready', profileVersion: 1, algorithmVersion: 'match-1.0',
  generatedAt: '2026-10-07T00:00:00Z', expiresAt: '2026-10-08T00:00:00Z',
  items: [{ projectId: 'home', rank: 1, score: 71.2, confidence: .8,
    components: [{ code: 'COMMUTE', weight: .3, status: 'unavailable' }],
    reasonCodes: ['ALL_HARD_CONSTRAINTS_MET'], freshness: { projectFactsAsOf: '2026-10-05T00:00:00Z' } }],
  excludedSummary: [],
}

describe('guest report cookies', () => {
  it('restores the complete request and compact report', async () => {
    const draft = query(); draft.additionalNeeds = 'Mumbai / cafe'
    const trial: GuestTrial = { version: 2, query: draft, snapshot, savedIds: ['home'] }
    const encoded = await encodeGuestTrial(trial)
    expect(encoded.length).toBeLessThan(3500)
    expect(await decodeGuestTrial(encoded)).toEqual(trial)
  })
  it('rejects a report that cannot fit within bounded cookies', async () => {
    const longDetails = Array.from({ length: 1400 }, () => crypto.randomUUID()).join('')
    const large: Snapshot = { ...snapshot, items: [{ ...snapshot.items[0], components: [{ code: 'COMMUTE', weight: .3, status: 'available', destinations: [{ status: longDetails }] }] }] }
    await expect(encodeGuestTrial({ version: 2, query: query(), snapshot: large, savedIds: ['home'] })).rejects.toThrow('too large')
  })
  it('rejects damaged data and invalid requests', async () => {
    await expect(decodeGuestTrial('invalid-cookie')).rejects.toThrow()
    const invalid = query(); invalid.weights.budget = 2
    await expect(encodeGuestTrial({ version: 2, query: invalid, snapshot, savedIds: [] })).rejects.toThrow()
  })
})
