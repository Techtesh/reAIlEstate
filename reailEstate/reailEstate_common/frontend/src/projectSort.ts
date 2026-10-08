import { lowestPrice, type Match, type Project } from './buyer'

const months = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']

export function possessionSortKey(value: string | null | undefined): number {
  if (!value) return Infinity
  const text = value.trim()
  let year: number, month: number, day: number
  let match = /^(\d{4})-(\d{1,2})-(\d{1,2})/.exec(text)
  if (match) { year = Number(match[1]); month = Number(match[2]); day = Number(match[3]) }
  else if ((match = /^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$/.exec(text))) {
    day = Number(match[1]); month = Number(match[2]); year = Number(match[3])
  } else if ((match = /^(?:Q([1-4])\s*[- ]?\s*)(20\d{2})$/i.exec(text))) {
    year = Number(match[2]); month = Number(match[1]) * 3; day = new Date(Date.UTC(year, month, 0)).getUTCDate()
  } else if ((match = /^([A-Za-z]+)[\s-]+(20\d{2})$/.exec(text))) {
    month = months.indexOf(match[1].slice(0, 3).toLowerCase()) + 1
    year = Number(match[2]); day = new Date(Date.UTC(year, month, 0)).getUTCDate()
  } else if ((match = /^(20\d{2})$/.exec(text))) {
    year = Number(match[1]); month = 12; day = 31
  } else return Infinity
  const timestamp = Date.UTC(year, month - 1, day)
  const date = new Date(timestamp)
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day ? timestamp : Infinity
}

export function sortProjects(projects: Project[], sort: string, routeMatches: Match[], latestMatches: Match[]): Project[] {
  const scores = new Map(latestMatches.map(item => [item.projectId, Number(item.score)]))
  const ranks = new Map(routeMatches.map(item => [item.projectId, item.rank]))
  const byName = (left: Project, right: Project) => left.submittedName.localeCompare(right.submittedName, 'en-IN', { numeric: true })
  return [...projects].sort((left, right) => {
    if (sort === 'price') return (lowestPrice(left) ?? Infinity) - (lowestPrice(right) ?? Infinity) || byName(left, right)
    if (sort === 'possession') return possessionSortKey(left.possessionDate) - possessionSortKey(right.possessionDate) || byName(left, right)
    if (sort === 'score') return (scores.get(right.id) ?? -Infinity) - (scores.get(left.id) ?? -Infinity) || byName(left, right)
    if (sort === 'match') return (ranks.get(left.id) ?? Infinity) - (ranks.get(right.id) ?? Infinity) || byName(left, right)
    return byName(left, right)
  })
}
