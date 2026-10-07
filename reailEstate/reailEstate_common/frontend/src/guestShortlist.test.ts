import { describe, expect, it } from 'vitest'
import { newDraft, type Draft, type Project } from './buyer'
import { decodeGuestTrial, encodeGuestTrial, rankGuestProjects, type GuestTrial } from './guestShortlist'

const query = (): Draft => ({ ...newDraft(), home: { ...newDraft().home, configurations: ['2BHK'], carpetAreaSqFt: { preferred: 800 } }, budget: { currency: 'INR' as const, purchaseMaximum: 20000000, isHardConstraint: true } })
const project = (id: string, changes: Partial<Project> = {}): Project => ({ id, submittedName: 'Test home', status: 'published', locality: 'Powai', mahaReraId: '', verificationStatus: 'unavailable', verificationCheckedAt: null, updatedAt: '2026-10-05T00:00:00Z', priceMin: '15000000', priceMax: null, configurations: [{ configurationType: '2BHK', carpetAreaMin: '700', carpetAreaMax: '800', priceMin: '15000000', priceMax: null, bathrooms: 2 }], amenities: ['gym'], ...changes })

describe('guest beta shortlist', () => {
  it('excludes unpublished projects and preserves hard budget/configuration constraints', () => {
    const snapshot = rankGuestProjects(query(), [project('available'), project('unpublished', { status: 'needs_review' }), project('over-budget', { priceMin: '30000000' }), project('wrong-layout', { configurations: [] })], 'guest-test')
    expect(snapshot.items.map(item => item.projectId)).toEqual(['available'])
    expect(snapshot.excludedSummary).toEqual([{ reasonCode: 'CONFIGURATION_UNAVAILABLE', count: 1 }, { reasonCode: 'OVER_HARD_BUDGET', count: 1 }])
  })
  it('reports unavailable commute and confidence without inventing a score', () => {
    const snapshot = rankGuestProjects(query(), [project('home')], 'guest-test')
    expect(snapshot.items[0].confidence).toBeNull()
    expect(snapshot.items[0].components.find(item => item.code === 'COMMUTE')?.status).toBe('unavailable')
    const unavailable = query(); unavailable.weights = { commute: 1, budget: 0, space: 0, societyAmenities: 0, nearbyAmenities: 0, microMarket: 0, projectConfidence: 0 }
    expect(rankGuestProjects(unavailable, [project('home')], 'guest-test').items).toEqual([])
  })
  it('enforces required amenities and minimum area/bathrooms', () => {
    const draft = query(); draft.societyAmenities = [{ code: 'pool', importance: 'required', substituteAccepted: false }]
    expect(rankGuestProjects(draft, [project('home')], 'guest-test').excludedSummary[0].reasonCode).toBe('REQUIRED_AMENITY_UNAVAILABLE')
    draft.societyAmenities = []; draft.home.carpetAreaSqFt.minimum = 900; draft.home.carpetAreaSqFt.preferred = 900
    expect(rankGuestProjects(draft, [project('home')], 'guest-test').excludedSummary[0].reasonCode).toBe('BELOW_HARD_CARPET_AREA')
    draft.home.carpetAreaSqFt.minimum = undefined; draft.home.bathroomsMinimum = 3
    expect(rankGuestProjects(draft, [project('home')], 'guest-test').excludedSummary[0].reasonCode).toBe('INSUFFICIENT_BATHROOMS')
  })
  it('caps results at ten and sorts ties deterministically', () => {
    const snapshot = rankGuestProjects(query(), Array.from({ length: 14 }, (_, index) => project(String(index).padStart(2, '0'))).reverse(), 'guest-test')
    expect(snapshot.items).toHaveLength(10)
    expect(snapshot.items[0].projectId).toBe('00')
    expect(snapshot.items[9].rank).toBe(10)
  })
  it('restores query, results and shortlist IDs from a small compressed Unicode cookie', async () => {
    const draft = query(); draft.additionalNeeds = 'मुंबई / café'
    const snapshot = rankGuestProjects(draft, Array.from({ length: 10 }, (_, index) => project(`home-${index}`)), 'guest-test') as GuestTrial['snapshot']
    const trial: GuestTrial = { version: 1, query: draft, snapshot, savedIds: snapshot.items.map(item => item.projectId) }
    const encoded = await encodeGuestTrial(trial)
    expect(encoded.length).toBeLessThan(3500)
    expect(await decodeGuestTrial(encoded)).toEqual(trial)
  })
  it('rejects malformed cookies and invalid queries', async () => {
    await expect(decodeGuestTrial('invalid-cookie')).rejects.toThrow()
    const draft = query(); draft.weights.budget = 2
    expect(() => rankGuestProjects(draft, [project('home')], 'guest-test')).toThrow()
  })
})
