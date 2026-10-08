import { z } from 'zod'
import { draftSchema, type Snapshot } from './buyer'

export const guestCookieName = 'mmr-beta-shortlist'
const usedCookieName = 'mmr-beta-used'
const maxChunks = 10
const chunkSize = 3500
const reportAge = 90 * 24 * 60 * 60
const markerAge = 400 * 24 * 60 * 60
const componentSchema = z.object({ code: z.string(), weight: z.number(), score: z.number().optional(), status: z.string(), destinations: z.array(z.unknown()).optional() })
const snapshotSchema = z.object({
  id: z.string().startsWith('guest-'), status: z.literal('ready'), profileVersion: z.number().int().positive(), algorithmVersion: z.literal('match-1.0'),
  generatedAt: z.string(), expiresAt: z.string(),
  items: z.array(z.object({ projectId: z.string(), rank: z.number().int().positive(), score: z.number(), confidence: z.number().nullable(), components: z.array(componentSchema), reasonCodes: z.array(z.string()), freshness: z.object({ projectFactsAsOf: z.string() }) })).max(10),
  excludedSummary: z.array(z.object({ reasonCode: z.string(), count: z.number().int().nonnegative() })),
})
const trialSchema = z.object({ version: z.literal(2), query: draftSchema, snapshot: snapshotSchema, savedIds: z.array(z.string()).max(10) })
export type GuestTrial = { version: 2; query: z.infer<typeof draftSchema>; snapshot: Snapshot; savedIds: string[] }

function cookie(name: string) { return document.cookie.split('; ').find(item => item.startsWith(`${name}=`))?.slice(name.length + 1) }
function write(name: string, value: string, maxAge: number) {
  document.cookie = `${name}=${value}; Path=/; Max-Age=${maxAge}; SameSite=Lax${location.protocol === 'https:' ? '; Secure' : ''}`
}
function clear(name: string) { write(name, '', 0) }
function renewMarker() { if (cookie(usedCookieName) === '1') write(usedCookieName, '1', markerAge) }
export function hasGuestTrialCookie() { return !!cookie(guestCookieName) }
export function guestTrialUsed() {
  renewMarker()
  return cookie(usedCookieName) === '1' || !!cookie(guestCookieName)
}
export async function encodeGuestTrial(trial: GuestTrial): Promise<string> {
  const source = JSON.stringify(trialSchema.parse(trial))
  const bytes = new Uint8Array(await new Response(new Blob([source]).stream().pipeThrough(new CompressionStream('gzip'))).arrayBuffer())
  const encoded = btoa(Array.from(bytes, byte => String.fromCharCode(byte)).join(''))
  if (encoded.length > maxChunks * chunkSize) throw new Error('This report is too large for browser cookies. Shorten optional notes or destinations, then try again.')
  return encoded
}
export async function decodeGuestTrial(encoded: string): Promise<GuestTrial> {
  if (encoded.length > maxChunks * chunkSize) throw new Error('Saved match data is too large.')
  const bytes = Uint8Array.from(atob(encoded), char => char.charCodeAt(0))
  const reader = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip')).getReader()
  const chunks: Uint8Array[] = []
  let size = 0
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      size += value.byteLength
      if (size > 100000) throw new Error('Saved match data is too large.')
      chunks.push(value)
    }
  } finally { await reader.cancel() }
  const data = new Uint8Array(size)
  let offset = 0
  for (const chunk of chunks) { data.set(chunk, offset); offset += chunk.byteLength }
  return trialSchema.parse(JSON.parse(new TextDecoder().decode(data))) as GuestTrial
}
export async function readGuestTrial(): Promise<GuestTrial | null> {
  const count = Number(cookie(guestCookieName))
  if (!cookie(guestCookieName)) return null
  if (!Number.isInteger(count) || count < 1 || count > maxChunks) throw new Error('Saved match cookies are damaged. Delete browser data and try again.')
  const encoded = Array.from({ length: count }, (_, index) => cookie(`${guestCookieName}-${index}`))
  if (encoded.some(value => !value)) throw new Error('Saved match cookies are incomplete. Delete browser data and try again.')
  try { return await decodeGuestTrial(encoded.join('')) }
  catch { throw new Error('Saved match cookies are damaged. Delete browser data and try again.') }
}
export async function saveGuestTrial(trial: GuestTrial, create = false) {
  const encoded = await encodeGuestTrial(trial)
  const chunks = encoded.match(new RegExp(`.{1,${chunkSize}}`, 'g')) ?? []
  const save = () => {
    if (create && guestTrialUsed()) throw new Error('Your free report has already been saved in this browser. Sign in to run another match.')
    for (let index = 0; index < maxChunks; index++) clear(`${guestCookieName}-${index}`)
    clear(guestCookieName)
    try {
      chunks.forEach((chunk, index) => {
        write(`${guestCookieName}-${index}`, chunk, reportAge)
        if (cookie(`${guestCookieName}-${index}`) !== chunk) throw new Error('Allow cookies in this browser to save your match report.')
      })
      write(guestCookieName, String(chunks.length), reportAge)
      if (cookie(guestCookieName) !== String(chunks.length)) throw new Error('Allow cookies in this browser to save your match report.')
      if (create) {
        write(usedCookieName, '1', markerAge)
        if (cookie(usedCookieName) !== '1') throw new Error('Allow cookies in this browser to save your free report.')
      }
    } catch (error) {
      clear(guestCookieName)
      for (let index = 0; index < maxChunks; index++) clear(`${guestCookieName}-${index}`)
      throw error
    }
  }
  if (navigator.locks) await navigator.locks.request('mmr-beta-shortlist', save)
  else save()
}
export function deleteGuestTrial() {
  clear(guestCookieName)
  for (let index = 0; index < maxChunks; index++) clear(`${guestCookieName}-${index}`)
}
