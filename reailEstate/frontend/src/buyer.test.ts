import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, apiPath, cancelSessionRequests, onSessionExpired } from './api'
import { coordinates, draftSchema, lowestPrice, newDraft, resumeDraft, type Project } from './buyer'

afterEach(() => vi.unstubAllGlobals())
const valid = () => { const draft = newDraft(); draft.home.configurations = ['2BHK']; draft.budget.purchaseMaximum = 20000000; return draft }
describe('canonical requirements', () => {
  it('requires a home and positive budget', () => { expect(draftSchema.safeParse(newDraft()).success).toBe(false); expect(draftSchema.safeParse(valid()).success).toBe(true) })
  it('round-trips the same values between guided, summary and saved drafts', () => { const draft = valid(); draft.home.carpetAreaSqFt = { minimum: 650, preferred: 800 }; expect(resumeDraft(JSON.parse(JSON.stringify(draft)))).toEqual(draft) })
  it('rejects reversed area and budget ranges', () => { const draft = valid(); draft.home.carpetAreaSqFt = { minimum: 900, preferred: 700 }; expect(draftSchema.safeParse(draft).success).toBe(false); draft.home.carpetAreaSqFt = {}; draft.budget.allInMaximum = 10000000; expect(draftSchema.safeParse(draft).success).toBe(false) })
  it('rejects zero budgets, fractional INR and all-zero weights', () => { const draft = valid(); for (const value of [0, -1, 2.5]) { draft.budget.purchaseMaximum = value; expect(draftSchema.safeParse(draft).success).toBe(false) } draft.budget.purchaseMaximum = 10000000; Object.keys(draft.weights).forEach(key => { draft.weights[key as keyof typeof draft.weights] = 0 }); expect(draftSchema.safeParse(draft).success).toBe(false) })
  it('rejects duplicate locality destinations', () => { const draft = valid(); draft.places = ['Powai', ' powai '].map((label, index) => ({ label, placeId: `manual:${index}`, precision: 'locality', frequencyPerWeek: 5, modes: ['transit'], maxOneWayMinutes: 60 })); expect(draftSchema.safeParse(draft).success).toBe(false) })
})
describe('honest map and price values', () => {
  it('does not invent pins for missing or invalid coordinates', () => { for (const latitude of [null, '', ' ', 'NaN', '91']) expect(coordinates({ latitude, longitude: '72.9' } as Project)).toBeNull(); expect(coordinates({ latitude: '19.2', longitude: '72.9' } as Project)).toEqual([19.2, 72.9]) })
  it('preserves unknown prices', () => { expect(lowestPrice({ configurations: [{ priceMin: null }] } as Project)).toBeNull(); expect(lowestPrice({ configurations: [{ priceMin: '15000000' }, { priceMin: '16000000' }] } as Project)).toBe(15000000) })
})
describe('API boundary', () => {
  it('reports expired sessions from read requests', async () => {
    vi.stubGlobal('window', { location: { origin: 'http://127.0.0.1:5173' } })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 403 })))
    const listener = vi.fn(), unsubscribe = onSessionExpired(listener)
    try { await expect(api('/api/v1/projects/')).rejects.toThrow('Please sign in again.'); expect(listener).toHaveBeenCalledOnce() } finally { unsubscribe() }
  })
  it('discards delayed responses from a cleared session', async () => {
    vi.stubGlobal('window', { location: { origin: 'http://127.0.0.1:5173' } })
    let respond!: (response: Response) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { respond = resolve })))
    const pending = api('/api/v1/projects/')
    cancelSessionRequests()
    respond(new Response('{"private":"stale"}'))
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
  })
  it('rejects external and non-API pagination URLs', () => { vi.stubGlobal('window', { location: { origin: 'http://127.0.0.1:5173' } }); expect(apiPath('/api/v1/projects/?cursor=opaque')).toBe('/api/v1/projects/?cursor=opaque'); expect(() => apiPath('https://evil.test/api/v1/projects/')).toThrow(); expect(() => apiPath('/admin/')).toThrow() })
  it('does not surface raw backend errors', async () => { vi.stubGlobal('window', { location: { origin: 'http://127.0.0.1:5173' } }); vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('private internal content', { status: 500 }))); await expect(api('/api/v1/projects/')).rejects.toThrow('The service could not complete this request.') })
})