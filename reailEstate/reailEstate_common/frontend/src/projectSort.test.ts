import { describe, expect, it } from 'vitest'
import { possessionSortKey, sortProjects } from './projectSort'
import type { Match, Project } from './buyer'

const project = (id: string, name: string, possessionDate: string | null = null) =>
  ({ id, submittedName: name, possessionDate, priceMin: null }) as Project
const match = (projectId: string, score: number) => ({ projectId, score }) as Match

describe('buyer project sorting', () => {
  it('sorts reviewed dates soonest first and leaves missing dates last', () => {
    const projects = [project('a', 'Alpha'), project('b', 'Beta', 'Q4 2028'), project('c', 'Charlie', '2027-07-01')]
    expect(sortProjects(projects, 'possession', [], []).map(item => item.id)).toEqual(['c', 'b', 'a'])
    expect(possessionSortKey('31/02/2028')).toBe(Infinity)
  })

  it('sorts by latest report scores while missing scores stay last', () => {
    const projects = [project('a', 'Alpha'), project('b', 'Beta'), project('c', 'Charlie')]
    expect(sortProjects(projects, 'score', [], [match('c', 81), match('a', 93)]).map(item => item.id)).toEqual(['a', 'c', 'b'])
    expect(sortProjects(projects, 'complete', [], []).map(item => item.id)).toEqual(['a', 'b', 'c'])
  })
})
