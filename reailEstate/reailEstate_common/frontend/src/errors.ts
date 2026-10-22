export const errorMessage = (error: unknown, fallback = 'This action could not be completed.') =>
  error instanceof Error ? error.message : fallback

export const isAbortError = (error: unknown) =>
  error instanceof DOMException && error.name === 'AbortError'
