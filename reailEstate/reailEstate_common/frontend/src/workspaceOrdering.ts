const names = new Intl.Collator('en-IN', { numeric: true, sensitivity: 'base' })

export function workspaceOrder<T extends { id: string }>(items: T[], name: (item: T) => string, inactive: (item: T) => boolean, descending = false): T[] {
  return [...items].sort((a, b) => Number(inactive(a)) - Number(inactive(b)) ||
    (descending ? -1 : 1) * names.compare(name(a), name(b)) || a.id.localeCompare(b.id))
}

export const inactiveProject = (project: { status: string }) => ['retired', 'merged', 'inactive', 'archived'].includes(project.status)
