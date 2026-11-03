export class ApiError extends Error {
  status: number
  details?: unknown
  constructor(status: number, message: string, details?: unknown) { super(message); this.status = status; this.details = details }
}
let sessionRequests = new AbortController()
const sessionListeners = new Set<(error: ApiError) => void>()
export function cancelSessionRequests() {
  sessionRequests.abort()
  sessionRequests = new AbortController()
}
export function onSessionExpired(listener: (error: ApiError) => void) {
  sessionListeners.add(listener)
  return () => { sessionListeners.delete(listener) }
}
export function apiPath(path: string): string {
  const url = new URL(path, window.location.origin)
  if (url.origin !== window.location.origin || !url.pathname.startsWith('/api/v1/')) throw new Error('Invalid API resource.')
  return url.pathname + url.search
}
export async function api<Result>(path: string, options: RequestInit = {}): Promise<Result> {
  const localPath = apiPath(path)
  const signal = AbortSignal.any([sessionRequests.signal, AbortSignal.timeout(20000), ...(options.signal ? [options.signal] : [])])
  const headers = new Headers(options.headers)
  headers.set('Accept', 'application/json')
  if (options.body && !headers.has('Content-Type') && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (options.method && !['GET', 'HEAD'].includes(options.method)) {
    const token = document.cookie.split('; ').find(item => item.startsWith('csrftoken='))?.slice('csrftoken='.length)
    if (token) headers.set('X-CSRFToken', decodeURIComponent(token))
  }
  let response: Response
  try { response = await fetch(localPath, { ...options, headers, credentials: 'same-origin', cache: 'no-store', signal }); signal.throwIfAborted() }
  catch (error) { if (error instanceof DOMException && error.name === 'AbortError') throw error; throw new ApiError(0, 'The service could not be reached. Your changes have not been discarded.') }
  if (!response.ok) {
    const messages: Record<number, string> = { 400: 'Some details were not accepted. Review your entries before trying again.', 401: 'Please sign in to continue.', 403: 'Your session or access could not be verified. Please sign in again.', 404: 'This item is no longer available.', 409: 'This draft changed in another session. Choose which version to keep.', 429: 'Too many requests. Please wait a moment before retrying.' }
    let detail = ''
    let problemDetails: unknown
    try {
      const problem: unknown = await response.json()
      problemDetails = problem
      if (problem && typeof problem === 'object') {
        const details = 'errors' in problem ? problem.errors : problem
        if (details && typeof details === 'object' && 'detail' in details && typeof details.detail === 'string') detail = details.detail.slice(0, 1000)
        else if (response.status === 400 && details && typeof details === 'object') {
          const fields = Object.entries(details).flatMap(([field, value]) => {
            if (typeof value === 'string') return [`${field}: ${value}`]
            if (Array.isArray(value)) return value.flatMap(item => {
              if (typeof item === 'string') return [`${field}: ${item}`]
              if (item && typeof item === 'object' && 'message' in item && typeof item.message === 'string') return [`${field}: ${item.message}`]
              return []
            })
            return []
          })
          detail = fields.slice(0, 8).join(' ').slice(0, 1000)
        }
      }
    } catch {}
    const error = new ApiError(response.status, detail || messages[response.status] || 'The service could not complete this request. Please try again.', problemDetails)
    const expired = response.status === 401 || response.status === 403 && !localPath.startsWith('/api/v1/admin/')
    if (expired && !['/api/v1/auth/login', '/api/v1/auth/builder-login', '/api/v1/auth/me', '/api/v1/admin/console/login', '/api/v1/admin/console/session'].includes(localPath.split('?')[0])) {
      sessionListeners.forEach(listener => listener(error))
    }
    throw error
  }
  if (response.status === 204) return undefined as Result
  const text = await response.text()
  const result = (text ? JSON.parse(text) : undefined) as Result
  signal.throwIfAborted()
  return result
}
export const json = (value: unknown) => JSON.stringify(value)
export async function collection<Item>(path: string, signal?: AbortSignal, onPage?: (items: Item[]) => void): Promise<Item[]> {
  const all: Item[] = []
  let next: string | null = path
  const visited = new Set<string>()
  while (next) {
    const localPath: string = apiPath(next)
    if (visited.has(localPath)) throw new ApiError(0, 'The catalogue could not be loaded completely. Please retry.')
    visited.add(localPath)
    const page: Item[] | { results: Item[]; next: string | null } = await api(localPath, { signal })
    if (Array.isArray(page)) {
      all.push(...page)
      onPage?.([...all])
      return all
    }
    all.push(...page.results)
    next = page.next
    onPage?.([...all])
  }
  return all
}
