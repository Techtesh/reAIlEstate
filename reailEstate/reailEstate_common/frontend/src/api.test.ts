import { afterEach, expect, it, vi } from 'vitest'
import { api, ApiError } from './api'

afterEach(() => vi.unstubAllGlobals())

it('shows structured requirement validation messages to the user', async () => {
  vi.stubGlobal('window', { location: { origin: 'http://localhost' } })
  vi.stubGlobal('document', { cookie: '' })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ errors: {
    budget: [{ code: 'OUT_OF_RANGE', message: 'Enter positive budget amounts in INR.' }],
    'home.carpetAreaSqFt': [{ code: 'INVALID_RANGE', message: 'The maximum must be at least the minimum.' }],
  } }), { status: 400 })))
  const result = api('/api/v1/requirement-profiles/test/', { method: 'PATCH', body: '{}' })
  await expect(result).rejects.toBeInstanceOf(ApiError)
  await expect(result).rejects.toThrow('budget: Enter positive budget amounts in INR. home.carpetAreaSqFt: The maximum must be at least the minimum.')
})
