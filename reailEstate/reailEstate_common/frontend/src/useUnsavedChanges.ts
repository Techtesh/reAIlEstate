import { useEffect, useRef, useSyncExternalStore } from 'react'
import { useBlocker } from 'react-router-dom'

const sources = new Set<symbol>()
const listeners = new Set<() => void>()
export const hasUnsavedChanges = () => sources.size > 0
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener) } }
const notify = () => listeners.forEach(listener => listener())

// Several independent editors can be mounted together; the router needs one blocker.
export function useUnsavedChanges(dirty: boolean) {
  const source = useRef(Symbol())
  useEffect(() => {
    if (!dirty) return
    const key = source.current
    sources.add(key); notify()
    return () => { sources.delete(key); notify() }
  }, [dirty])
}

export function useProtectedNavigation() {
  const dirty = useSyncExternalStore(subscribe, hasUnsavedChanges, () => false)
  const blocker = useBlocker(dirty)
  useEffect(() => {
    if (blocker.state !== 'blocked') return
    if (window.confirm('Discard unsaved entries and leave this view? An in-flight request may still finish on the server.')) blocker.proceed()
    else blocker.reset()
  }, [blocker])
  useEffect(() => {
    if (!dirty) return
    const unload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', unload)
    return () => window.removeEventListener('beforeunload', unload)
  }, [dirty])
}
