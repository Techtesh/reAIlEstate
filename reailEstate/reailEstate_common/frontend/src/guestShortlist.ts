import { z } from 'zod'
import { draftSchema, priceValue, weightKeys, type Draft, type Match, type Project, type Snapshot } from './buyer'

export const guestCookieName = 'mmr-beta-shortlist'
const cookieLimit = 3500
const usedCookieName = 'mmr-beta-used'

export function guestTrialUsed() {
  return document.cookie.split('; ').some(item => item.startsWith(`${usedCookieName}=1`) || item.startsWith(`${guestCookieName}=`))
}
const componentSchema = z.object({ code: z.string(), weight: z.number().min(0).max(1), score: z.number().min(0).max(1).optional(), status: z.string() })
const snapshotSchema = z.object({
  id: z.string().startsWith('guest-'), status: z.literal('ready'), profileVersion: z.literal(1), algorithmVersion: z.literal('beta-catalogue-1.0'),
  generatedAt: z.string(), expiresAt: z.string(),
  items: z.array(z.object({ projectId: z.string(), rank: z.number().int().positive(), score: z.number().min(0).max(100), confidence: z.null(), components: z.array(componentSchema), reasonCodes: z.array(z.string()), freshness: z.object({ projectFactsAsOf: z.string() }) })).max(10),
  excludedSummary: z.array(z.object({ reasonCode: z.string(), count: z.number().int().nonnegative() })),
})
const trialSchema = z.object({ version: z.literal(1), query: draftSchema, snapshot: snapshotSchema, savedIds: z.array(z.string()).max(10) })
export type GuestTrial = z.infer<typeof trialSchema>

function exclusion(project: Project, query: Draft) {
  const home = query.home, layouts = project.configurations
  if (home.configurations.length && !layouts.some(layout => home.configurations.includes(layout.configurationType))) return 'CONFIGURATION_UNAVAILABLE'
  if (home.carpetAreaSqFt.minimum && !layouts.some(layout => Number(layout.carpetAreaMax) >= home.carpetAreaSqFt.minimum!)) return 'BELOW_HARD_CARPET_AREA'
  if (home.bathroomsMinimum && home.bathroomsIsHardConstraint && !layouts.some(layout => (layout.bathrooms ?? 0) >= home.bathroomsMinimum)) return 'INSUFFICIENT_BATHROOMS'
  const maximum = query.budget.allInMaximum ?? query.budget.purchaseMaximum, price = priceValue(project.priceMin)
  if (query.budget.isHardConstraint && maximum && price != null && price > maximum) return 'OVER_HARD_BUDGET'
  if (query.societyAmenities.some(amenity => amenity.importance === 'required' && !amenity.substituteAccepted && !project.amenities.includes(amenity.code))) return 'REQUIRED_AMENITY_UNAVAILABLE'
  return null
}

export function rankGuestProjects(query: Draft, catalogue: Project[], id = `guest-${crypto.randomUUID()}`, now = new Date()): Snapshot {
  draftSchema.parse(query)
  const excluded = new Map<string, number>(), items: Match[] = []
  for (const project of catalogue.filter(project => project.status === 'published')) {
    const failure = exclusion(project, query)
    if (failure) { excluded.set(failure, (excluded.get(failure) ?? 0) + 1); continue }
    const maximum = query.budget.allInMaximum ?? query.budget.purchaseMaximum, price = priceValue(project.priceMin)
    const areas = project.configurations.map(layout => Number(layout.carpetAreaMax)).filter(value => Number.isFinite(value) && value > 0)
    const preferred = query.home.carpetAreaSqFt.preferred
    const preferences = query.societyAmenities
    const amenityTotal = preferences.reduce((sum, amenity) => sum + (amenity.importance === 'required' ? 2 : 1), 0)
    const values: Partial<Record<typeof weightKeys[number], number>> = {
      ...(maximum && price != null ? { budget: price <= maximum ? Math.max(.5, 1 - (maximum - price) / maximum * .5) : Math.max(0, 1 - (price - maximum) / maximum) } : {}),
      ...(preferred && areas.length ? { space: Math.min(1, Math.max(...areas) / preferred) } : {}),
      ...(amenityTotal ? { societyAmenities: preferences.reduce((sum, amenity) => sum + (project.amenities.includes(amenity.code) ? amenity.importance === 'required' ? 2 : 1 : 0), 0) / amenityTotal } : {}),
    }
    const available = weightKeys.filter(key => values[key] != null && query.weights[key] > 0)
    const denominator = available.reduce((sum, key) => sum + query.weights[key], 0)
    if (!denominator) { excluded.set('NO_PUBLIC_SCORING_COMPONENTS', (excluded.get('NO_PUBLIC_SCORING_COMPONENTS') ?? 0) + 1); continue }
    const score = 100 * available.reduce((sum, key) => sum + query.weights[key] * values[key]!, 0) / denominator
    items.push({ projectId: project.id, rank: 0, score: Math.round(score * 100) / 100, confidence: null,
      components: weightKeys.map(key => ({ code: key.toUpperCase(), weight: query.weights[key], status: values[key] == null ? 'unavailable' : 'available', ...(values[key] == null ? {} : { score: values[key] }) })),
      reasonCodes: ['ALL_HARD_CONSTRAINTS_MET'], freshness: { projectFactsAsOf: project.updatedAt } })
  }
  items.sort((left, right) => right.score - left.score || left.projectId.localeCompare(right.projectId))
  return { id, status: 'ready', profileVersion: 1, algorithmVersion: 'beta-catalogue-1.0', generatedAt: now.toISOString(), expiresAt: new Date(now.getTime() + 24 * 60 * 60 * 1000).toISOString(),
    items: items.slice(0, 10).map((item, index) => ({ ...item, rank: index + 1 })), excludedSummary: [...excluded].sort(([left], [right]) => left.localeCompare(right)).map(([reasonCode, count]) => ({ reasonCode, count })) }
}

export async function encodeGuestTrial(trial: GuestTrial): Promise<string> {
  const text = JSON.stringify(trialSchema.parse(trial))
  const stream = new Blob([text]).stream().pipeThrough(new CompressionStream('gzip'))
  const bytes = new Uint8Array(await new Response(stream).arrayBuffer())
  const encoded = btoa(Array.from(bytes, byte => String.fromCharCode(byte)).join(''))
  if (encoded.length > cookieLimit) throw new Error('This query is too large for a browser cookie. Shorten additional notes or destinations, then try again.')
  return encoded
}

export async function decodeGuestTrial(encoded: string): Promise<GuestTrial> {
  if (encoded.length > cookieLimit) throw new Error('Invalid beta cookie.')
  const bytes = Uint8Array.from(atob(encoded), char => char.charCodeAt(0))
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))
  const reader = stream.getReader(), chunks: Uint8Array[] = []
  let size = 0
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      size += value.byteLength
      if (size > 50000) throw new Error('Invalid beta cookie.')
      chunks.push(value)
    }
  } finally { await reader.cancel() }
  const data = new Uint8Array(size)
  let offset = 0
  for (const chunk of chunks) { data.set(chunk, offset); offset += chunk.byteLength }
  return trialSchema.parse(JSON.parse(new TextDecoder().decode(data)))
}

function cookieValue() {
  return document.cookie.split('; ').find(item => item.startsWith(`${guestCookieName}=`))?.slice(guestCookieName.length + 1)
}

export async function readGuestTrial(): Promise<GuestTrial | null> {
  const value = cookieValue()
  if (!value) return null
  try { return await decodeGuestTrial(value) } catch { return null }
}

export async function saveGuestTrial(trial: GuestTrial, create = false) {
  const encoded = await encodeGuestTrial(trial)
  const write = () => {
    if (create && guestTrialUsed()) throw new Error('Your beta shortlist is already saved in this browser. Sign in to run another query.')
    document.cookie = `${guestCookieName}=${encoded}; Path=/; Max-Age=${90 * 24 * 60 * 60}; SameSite=Lax${location.protocol === 'https:' ? '; Secure' : ''}`
    if (cookieValue() !== encoded) throw new Error('Allow cookies in this browser to save your free beta shortlist.')
    if (create) document.cookie = `${usedCookieName}=1; Path=/; Max-Age=${90 * 24 * 60 * 60}; SameSite=Lax${location.protocol === 'https:' ? '; Secure' : ''}`
  }
  if (navigator.locks) await navigator.locks.request('mmr-beta-shortlist', write)
  else write()
}

export function deleteGuestTrial() {
  document.cookie = `${guestCookieName}=; Path=/; Max-Age=0; SameSite=Lax${location.protocol === 'https:' ? '; Secure' : ''}`
}
