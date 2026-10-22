import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, apiPath, cancelSessionRequests, onSessionExpired } from './api'
import { allocatedWeightPoints, calculatedRoomArea, configurationArea, configurationPrice, coordinates, defaults, draftSchema, inr, lowestPrice, newDraft, placesSchema, priceValue, projectPrice, resumeDraft, type Project } from './buyer'

afterEach(() => vi.unstubAllGlobals())
const valid = () => { const draft = newDraft(); draft.home.configurations = ['2BHK']; draft.budget.purchaseMaximum = 20000000; return draft }
describe('canonical requirements', () => {
  it('requires a home and positive budget', () => { expect(draftSchema.safeParse(newDraft()).success).toBe(false); expect(draftSchema.safeParse(valid()).success).toBe(true) })
  it('round-trips the same values between guided, summary and saved drafts', () => { const draft = valid(); draft.home.carpetAreaSqFt = { minimum: 650, preferred: 800 }; expect(resumeDraft(JSON.parse(JSON.stringify(draft)))).toEqual(draft) })
  it('rejects reversed area and budget ranges', () => { const draft = valid(); draft.home.carpetAreaSqFt = { minimum: 900, preferred: 700 }; expect(draftSchema.safeParse(draft).success).toBe(false); draft.home.carpetAreaSqFt = {}; draft.budget.allInMaximum = 10000000; expect(draftSchema.safeParse(draft).success).toBe(false) })
  it('rejects zero budgets, fractional INR, and zero or over-budget priorities', () => { const draft = valid(); for (const value of [0, -1, 2.5]) { draft.budget.purchaseMaximum = value; expect(draftSchema.safeParse(draft).success).toBe(false) } draft.budget.purchaseMaximum = 10000000; draft.weights.space = .25; expect(draftSchema.safeParse(draft).success).toBe(false); Object.keys(draft.weights).forEach(key => { draft.weights[key as keyof typeof draft.weights] = 0 }); expect(draftSchema.safeParse(draft).success).toBe(false) })
  it('reports the point total without changing a user allocation', () => {
    expect(allocatedWeightPoints(defaults)).toBe(100)
    expect(draftSchema.safeParse({ ...valid(), weights: { ...defaults, commute: .1 } }).success).toBe(true)
    const overweight = { ...defaults, space: .8 }
    expect(allocatedWeightPoints(overweight)).toBe(160)
    expect(resumeDraft({ weights: overweight }).weights).toEqual(overweight)
    expect(draftSchema.safeParse({ ...valid(), weights: overweight }).success).toBe(false)
  })
  it('rejects duplicate locality destinations', () => { const draft = valid(); draft.places = ['Powai', ' powai '].map((label, index) => ({ label, placeId: `manual:${index}`, precision: 'locality', frequencyPerWeek: 5, modes: ['transit'], maxOneWayMinutes: 60 })); expect(draftSchema.safeParse(draft).success).toBe(false) })
  it('preserves exact destinations when validating and resuming private profiles', () => {
    const draft = valid()
    draft.places = [{ placeId: 'manual:work', label: 'Work', precision: 'exact', latitude: 19.12, longitude: 72.9, frequencyPerWeek: 5, modes: ['transit'], maxOneWayMinutes: 60 }]
    expect(draftSchema.parse(draft).places).toEqual(draft.places)
    expect(resumeDraft(JSON.parse(JSON.stringify(draft))).places).toEqual(draft.places)
    expect(placesSchema.safeParse(draft.places).success).toBe(true)
    for (const latitude of [undefined, NaN, Infinity, 91]) expect(placesSchema.safeParse([{ ...draft.places[0], latitude }]).success).toBe(false)
    expect(placesSchema.safeParse([{ ...draft.places[0], longitude: -181 }]).success).toBe(false)
  })
  it('rejects partial coordinates and excessive or repeated destinations without requiring a complete home', () => {
    const place = { placeId: 'manual:work', label: 'Work', precision: 'locality', frequencyPerWeek: 5, modes: ['transit'], maxOneWayMinutes: 60 }
    expect(placesSchema.safeParse([place]).success).toBe(true)
    expect(placesSchema.safeParse([{ ...place, latitude: 19 }]).success).toBe(false)
    expect(placesSchema.safeParse([place, { ...place, label: 'Duplicate ID' }]).success).toBe(false)
    expect(placesSchema.safeParse(Array.from({ length: 11 }, (_, index) => ({ ...place, placeId: `manual:${index}`, label: `Place ${index}` }))).success).toBe(false)
  })
})
describe('honest map and price values', () => {
  it('formats backend-calculated room areas without using them as carpet area', () => {
    expect(calculatedRoomArea(438.44)).toBe('438.44 sq ft')
    for (const value of [null, undefined, NaN, Infinity, -1, 0]) expect(calculatedRoomArea(value)).toBe('Unknown')
    expect(configurationArea({ configurationType: '1BHK', carpetAreaMin: null, carpetAreaMax: null, priceMin: null, priceMax: null, bathrooms: null, roomRectangleSumSqFt: 438.44 })).toBe('Unknown')
  })
  it('does not invent pins for missing or invalid coordinates', () => { for (const latitude of [null, '', ' ', 'NaN', '91']) expect(coordinates({ latitude, longitude: '72.9' } as Project)).toBeNull(); expect(coordinates({ latitude: '19.2', longitude: '72.9' } as Project)).toEqual([19.2, 72.9]) })
  it('uses layout-derived prices without legacy configuration fallback', () => {
    expect(lowestPrice({ priceMin: null, configurations: [{ priceMin: '1000' }] } as Project)).toBeNull()
    expect(lowestPrice({ priceMin: '15000000.50' } as Project)).toBe(15000000.50)
  })
  it('formats min-max ranges and a single price', () => {
    expect(projectPrice({ priceMin: '15000000', priceMax: '20000000' })).toBe(`${inr(15000000)} - ${inr(20000000)}`)
    expect(projectPrice({ priceMin: '15000000', priceMax: '15000000' })).toBe(inr(15000000))
    expect(projectPrice({ priceMin: '15000000', priceMax: '20000000' }, true)).toBe('INR 1.50 Cr - INR 2.00 Cr')
    expect(projectPrice({ priceMin: '0.01', priceMax: '0.01' })).toContain('0.01')
  })
  it('preserves missing and invalid prices as contact to know', () => {
    for (const value of [null, undefined, '', ' ', 'NaN', 'Infinity', '0', '-1']) expect(priceValue(value)).toBeNull()
    expect(projectPrice({ priceMin: null, priceMax: null })).toBe('contact to know')
    expect(projectPrice({ priceMin: '20000000', priceMax: '15000000' })).toBe('contact to know')
    expect(inr(null)).toBe('contact to know')
  })
  it('formats layout-derived configuration area and price ranges without inventing missing data', () => {
    const configuration = { configurationType: '2BHK', carpetAreaMin: '700', carpetAreaMax: '850', priceMin: '15000000', priceMax: '18000000', bathrooms: 2 }
    expect(configurationArea(configuration)).toBe('700 - 850 sq ft')
    expect(configurationPrice(configuration)).toBe(`${inr(15000000)} - ${inr(18000000)}`)
    expect(configurationArea({ ...configuration, carpetAreaMin: null, carpetAreaMax: null })).toBe('Unknown')
    expect(configurationPrice({ ...configuration, priceMin: null, priceMax: null })).toBe('contact to know')
  })
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