import { describe, expect, it } from 'vitest'
import { inactiveProject, workspaceOrder } from './workspaceOrdering'

describe('workspace order', () => {
  const records = [
    { id: '1', name: 'Flat 1', inactive: true }, { id: '2', name: 'flat 10', inactive: false },
    { id: '3', name: 'Flat 2', inactive: false }, { id: '4', name: 'Flat 3', inactive: true },
  ]
  it('groups by activity before natural names in either direction', () => {
    expect(workspaceOrder(records, r => r.name, r => r.inactive).map(r => r.id)).toEqual(['3', '2', '1', '4'])
    expect(workspaceOrder(records, r => r.name, r => r.inactive, true).map(r => r.id)).toEqual(['2', '3', '4', '1'])
  })
  it('does not treat unpublished projects as inactive', () => {
    expect(inactiveProject({ status: 'needs_review' })).toBe(false)
    expect(inactiveProject({ status: 'ingesting' })).toBe(false)
    expect(inactiveProject({ status: 'retired' })).toBe(true)
  })
})
