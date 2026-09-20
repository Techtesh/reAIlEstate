import { test as base, expect, type Page } from '@playwright/test'
import type { Draft, MapConnection, MapLine, MapStation, Profile, Project, Shortlist, Snapshot } from '../src/buyer'
import type { GalleryItem } from '../src/ProjectGallery'

const test = base.extend<{ clientErrors: string[] }>({
  clientErrors: [async ({ page }, use) => {
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => { if (/violates.*content security policy|refused to.*content security policy/i.test(message.text())) errors.push(message.text()) })
    await use(errors)
    expect(errors).toEqual([])
  }, { auto: true }],
})

const projectIds = ['a0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000002', 'a0000000-0000-4000-8000-000000000003']
const profileId = 'b0000000-0000-4000-8000-000000000001'
const snapshotId = 'c0000000-0000-4000-8000-000000000001'
const fixtures: Project[] = ['Fixture Lakes', 'Fixture Gardens', 'Fixture Unmapped'].map((name, index) => ({
  id: projectIds[index], submittedName: name, locality: index === 1 ? 'Thane' : 'Powai', status: 'published', mahaReraId: `P12345678${index}`,
  verificationStatus: index === 1 ? 'unavailable' : 'verified', verificationCheckedAt: index === 1 ? null : '2026-09-15T10:00:00Z', updatedAt: '2026-09-15T10:00:00Z',
  latitude: index === 2 ? null : String(19.12 + index * .10), longitude: index === 2 ? null : String(72.91 + index * .06),
  priceMin: index === 2 ? null : String(15000000 + index * 1000000),
  priceMax: index === 2 ? null : String(18000000 + index * 1000000),
  configurations: [{ configurationType: '2BHK', carpetAreaMin: '700', carpetAreaMax: '850', priceMin: index === 2 ? null : String(15000000 + index * 1000000), priceMax: null, bathrooms: 2 }], amenities: ['gym'], facts: [],
}))
const snapshot: Snapshot = { id: snapshotId, status: 'ready', profileVersion: 2, algorithmVersion: 'match-1.0', generatedAt: '2026-09-17T10:00:00Z', expiresAt: '2099-09-18T10:00:00Z', excludedSummary: [], items: fixtures.map((project, index) => ({ projectId: project.id, rank: index + 1, score: 80 - index * 10, confidence: .8, components: [{ code: 'BUDGET', score: .8, weight: .25, status: 'available' }, { code: 'SPACE', score: 0, weight: .2, status: 'available' }, { code: 'COMMUTE', weight: .3, status: 'unavailable' }], reasonCodes: ['ALL_HARD_CONSTRAINTS_MET'], freshness: { projectFactsAsOf: '2026-09-15T10:00:00Z' } })) }

async function mockApi(page: Page, signedIn = true) {
  const state = { signedIn, profile: null as Profile | null, patches: [] as Draft[], consents: 0, submitKeys: [] as string[], conflict: false, empty: false, pending: false, stationError: false, catalogueError: false, requests: [] as string[] }
  let shortlists: Shortlist[] = []
  await page.route('https://tile.openstreetmap.org/**', route => route.fulfill({ status: 503, body: '' }))
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method()
    state.requests.push(`${method} ${path}`)
    const body = request.postData() ? request.postDataJSON() : {}
    const respond = (data: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) })
    if (path === '/api/v1/auth/login') {
      if (method === 'POST') state.signedIn = true
      return route.fulfill({ status: 200, contentType: 'application/json', headers: { 'Set-Cookie': 'csrftoken=test-only-csrf; Path=/; SameSite=Lax' }, body: JSON.stringify(method === 'POST' ? { id: 42, username: 'fixture-buyer', email: '' } : { status: 'ready' }) })
    }
    if (path === '/api/v1/projects/') {
      if (state.catalogueError) return respond({}, 503)
      return respond({ results: fixtures.filter(project => (!url.searchParams.has('locality') || project.locality === url.searchParams.get('locality')) && (!url.searchParams.has('mahaReraStatus') || project.verificationStatus === url.searchParams.get('mahaReraStatus'))), next: null })
    }
    if (path.endsWith('/gallery')) return respond({ results: [], next: null, access: fixtures.some(project => path.includes(project.id)) ? 'published' : 'restricted' })
    if (path.startsWith('/api/v1/projects/')) return respond(fixtures.find(project => path.includes(project.id)))
    if (path === '/api/v1/map-stations') {
      if (state.stationError) return respond({}, 503)
      const stations: MapStation[] = [
        { id: 'metro-operational', name: 'Fixture Metro', kind: 'metro', line: 'Line 1', location: { latitude: 19.15, longitude: 72.95 }, serviceStatus: 'operational', source: 'authority', asOf: '2026-09-01', sourceUrl: 'https://mmrda.maharashtra.gov.in/' },
        { id: 'metro-candidate', name: 'Added <b>Metro</b>', kind: 'metro', line: 'Line 2', location: { latitude: 19.18, longitude: 72.94 }, serviceStatus: 'unverified', source: 'user_candidate', asOf: null, sourceUrl: null },
        { id: 'railway', name: 'Fixture Railway', kind: 'railway', line: '', location: { latitude: 19.17, longitude: 72.96 }, serviceStatus: 'unverified', source: 'saved_reference', asOf: null, sourceUrl: null },
      ]
      return respond({ stations, warnings: [] })
    }
    if (!state.signedIn) return respond({ code: 'ACCESS_DENIED' }, 403)
    if (path === '/api/v1/auth/me') return respond({ id: 42, username: 'fixture-buyer', email: '', isStaff: false })
    if (path === '/api/v1/auth/logout') { state.signedIn = false; return route.fulfill({ status: 204 }) }
    if (path === '/api/v1/amenities') return respond([{ taxonomyCode: 'gym', displayName: 'Gym', category: 'society' }])
    if (path === '/api/v1/consents/') { state.consents += 1; return respond({ id: 'consent-test' }, 201) }
    if (path === '/api/v1/requirement-profiles/') {
      if (method === 'POST') { state.profile = { id: profileId, version: 1, status: 'draft', sections: {}, updatedAt: '2026-09-17T10:00:00Z' }; return respond(state.profile, 201) }
      return respond(state.profile ? [state.profile] : [])
    }
    if (path.endsWith(':submit')) { expect(request.headers()['if-match']).toBe(`W/"${state.profile?.version}"`); state.submitKeys.push(request.headers()['idempotency-key']); return respond({ recommendationRequestId: snapshotId, operationId: 'operation-test' }, 202) }
    if (path === `/api/v1/requirement-profiles/${profileId}/`) {
      if (method === 'PATCH') {
        if (state.conflict) { state.profile = { ...state.profile!, version: state.profile!.version + 1 }; return respond({ code: 'VERSION_CONFLICT' }, 409) }
        expect(request.headers()['if-match']).toBe(`W/"${state.profile?.version}"`)
        state.patches.push(body)
        state.profile = { ...state.profile!, version: state.profile!.version + 1, sections: body }
      }
      return respond(state.profile)
    }
    if (path.startsWith('/api/v1/recommendation-requests/')) return respond({ ...snapshot, status: state.pending ? 'requested' : 'ready', items: state.empty ? [] : snapshot.items, excludedSummary: state.empty ? [{ reasonCode: 'OVER_HARD_BUDGET', count: 3 }] : [] })
    if (path.startsWith('/api/v1/operations/')) return respond({ status: state.pending ? 'pending' : 'completed' })
    if (path === '/api/v1/shortlists/') {
      if (method === 'POST') { const list = { id: 'shortlist-test', name: body.name, entries: [] }; shortlists.push(list); return respond(list, 201) }
      return respond({ results: shortlists, next: null })
    }
    if (path === '/api/v1/shortlists/shortlist-test/projects') {
      if (method === 'DELETE') { shortlists[0].entries = shortlists[0].entries.filter(entry => entry.project !== body.projectId); return route.fulfill({ status: 204 }) }
      shortlists[0].entries.push({ project: body.projectId, note: '' }); return respond(shortlists[0], 201)
    }
    if (path === '/api/v1/project-comparisons') return respond({ projects: fixtures.filter(project => body.projectIds.includes(project.id)) })
    return respond({ code: 'UNEXPECTED_TEST_REQUEST' }, 500)
  })
  return state
}

async function addCoordinateDestination(page: Page, label = 'Work campus') {
  await page.getByRole('button', { name: 'Add destination', exact: true }).click()
  await page.getByLabel('Destination locality', { exact: true }).fill(label)
  await page.getByText('Set map location', { exact: true }).click()
  await page.getByLabel('Location entry method').selectOption('coordinates')
  await page.getByLabel('Latitude', { exact: true }).fill('19.3')
  await page.getByLabel('Longitude', { exact: true }).fill('73.01')
  await page.getByRole('button', { name: 'Use coordinates', exact: true }).click()
}

test('project names lead compact cards and provisional map pins are clearly labelled', async ({ page }, info) => {
  await mockApi(page, false)
  await page.route('**/api/v1/projects/', route => route.fulfill({ json: { results: fixtures.map((project, index) => ({ ...project, locationStatus: index === 0 ? 'provisional' : 'published' })), next: null } }))
  await page.goto('/browse')
  const card = page.locator(`#project-${projectIds[0]}`)
  await expect(card.getByRole('heading', { name: 'Fixture Lakes', level: 2 })).toBeVisible()
  const heading = await card.locator('.project-heading').boundingBox()
  const price = await card.locator('.price-row').boundingBox()
  expect(heading!.y + heading!.height).toBeLessThanOrEqual(price!.y)
  expect((await page.locator('.browse-heading').boundingBox())!.height).toBeLessThan(76)
  await expect(page.locator('.price-pin.provisional')).toHaveCount(1)
  await expect(card).toContainText('Provisional map location - not reviewed')
  await expect(page.locator('.map-data-status')).toContainText('development preview, not reviewed')
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 })
    await page.getByRole('button', { name: 'Feed view', exact: true }).click()
    await noOverflow(page)
    expect((await page.locator('.browse-heading').boundingBox())!.height).toBeLessThan(160)
    await page.screenshot({ path: info.outputPath(`compact-projects-${width}.png`), fullPage: true })
  }
})

test('daily commute coordinates save privately, reload by profile, and appear only on shortlist with inverse colours', async ({ page }, info) => {
  const state = await mockApi(page)
  await page.goto('/browse')
  await page.getByRole('button', { name: 'Shortlist Fixture Lakes', exact: true }).click()
  await page.getByRole('link', { name: /^Shortlist/ }).click()
  await page.locator('.shortlist-commute > summary').click()
  await addCoordinateDestination(page)
  await expect(page.locator('.commute-pin')).toHaveCount(1)
  await expect(page.locator('.commute-pin')).toHaveAttribute('title', 'Daily commute 1: Work campus')
  await page.getByRole('checkbox', { name: /I consent to saving my requirements/ }).check()
  await page.getByRole('button', { name: 'Save destinations', exact: true }).click()
  await expect.poll(() => state.patches.length).toBe(1)
  expect(state.patches[0].home.configurations).toEqual([])
  expect(state.patches[0].places[0]).toMatchObject({ label: 'Work campus', precision: 'exact', latitude: 19.3, longitude: 73.01 })
  await expect(page.locator('.shortlist-commute')).toContainText('Saved privately | version 2')
  expect(state.consents).toBe(1)
  for (const colorScheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme })
    await expect(page.locator('.commute-pin')).toHaveCSS('background-color', colorScheme === 'light' ? 'rgb(23, 23, 23)' : 'rgb(255, 255, 255)')
    await expect(page.locator('.price-pin.shortlisted')).toHaveCSS('background-color', 'rgb(138, 40, 79)')
    await page.screenshot({ path: info.outputPath(`commute-${colorScheme}.png`), fullPage: true })
  }
  expect(await page.evaluate(() => JSON.stringify({ session: { ...sessionStorage }, local: { ...localStorage } }))).not.toContain('Work campus')
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 1000 })
    await page.getByRole('button', { name: 'Map view', exact: true }).click()
    await expect(page.locator('.commute-pin')).toBeVisible()
    await noOverflow(page)
  }
  await page.getByRole('link', { name: 'Browse', exact: true }).click()
  await expect(page.locator('.commute-pin')).toHaveCount(0)
  await page.getByRole('link', { name: 'Discover', exact: true }).click()
  await expect(page.locator('.commute-pin')).toHaveCount(0)
  await page.getByRole('link', { name: /^Shortlist/ }).click()
  await expect(page.locator('.commute-pin')).toHaveCount(0)
  await page.getByRole('button', { name: 'Map view', exact: true }).click()
  await expect(page.locator('.commute-pin')).toHaveCount(1)
  await page.reload()
  await page.locator('.shortlist-commute > summary').click()
  await page.getByLabel('Saved requirement profile').selectOption(profileId)
  await page.getByRole('button', { name: 'Load requirements', exact: true }).click()
  await page.getByRole('button', { name: 'Map view', exact: true }).click()
  await expect(page.locator('.commute-pin')).toHaveCount(1)
  expect(state.requests.some(request => request.includes('commute-locations'))).toBe(false)
  await page.getByRole('button', { name: 'Sign out', exact: true }).click()
  await expect(page.locator('.commute-pin')).toHaveCount(0)
  await expect(page.locator('.shortlist-commute')).toHaveCount(0)
})

test('daily commute supports Google search, share links, Plus Codes and explicit provider failures', async ({ page }) => {
  await mockApi(page)
  const lookups: { path: string; body: Record<string, string> }[] = []
  await page.route('**/api/v1/commute-locations/**', async route => {
    const request = route.request(), url = new URL(request.url()), body = request.postDataJSON()
    expect(request.method()).toBe('POST')
    expect(url.search).toBe('')
    expect(request.headers()['x-csrftoken']).toBe('test-only-csrf')
    lookups.push({ path: url.pathname, body })
    if (body.query === 'provider unavailable') return route.fulfill({ status: 503, json: {} })
    const result = { placeId: `google:${lookups.length}`, label: `Resolved destination ${lookups.length}`, precision: 'exact', latitude: 19.1 + lookups.length / 100, longitude: 72.9 }
    return route.fulfill({ json: url.pathname.endsWith('/search') ? { results: [result] } : result })
  })
  await page.goto('/shortlist')
  await page.locator('.shortlist-commute > summary').click()
  for (const [index, method, query] of [[1, 'search', 'Office Powai'], [2, 'share', 'https://maps.app.goo.gl/fixture'], [3, 'share', '7JFJ+5C Powai']] as const) {
    await page.getByRole('button', { name: 'Add destination', exact: true }).click()
    const destination = page.getByRole('group', { name: `Destination ${index}`, exact: true })
    await destination.getByLabel('Destination locality', { exact: true }).fill(`Place ${index}`)
    await destination.getByText('Set map location', { exact: true }).click()
    await destination.getByLabel('Location entry method').selectOption(method)
    await destination.getByLabel(method === 'share' ? 'Google Maps share link or Plus Code' : 'Search Google Maps', { exact: true }).fill(query)
    await destination.getByRole('button', { name: 'Look up location', exact: true }).click()
    if (index !== 2) await destination.getByRole('button', { name: `Use Resolved destination ${index}`, exact: true }).click()
    await expect(page.locator('.commute-pin')).toHaveCount(index)
  }
  expect(lookups.map(item => item.path)).toEqual(['/api/v1/commute-locations/search', '/api/v1/commute-locations/resolve', '/api/v1/commute-locations/search'])
  await page.getByRole('button', { name: 'Add destination', exact: true }).click()
  const destination = page.getByRole('group', { name: 'Destination 4', exact: true })
  await destination.getByText('Set map location', { exact: true }).click()
  await destination.getByLabel('Search Google Maps', { exact: true }).fill('provider unavailable')
  await destination.getByRole('button', { name: 'Look up location', exact: true }).click()
  await expect(destination.getByRole('alert')).toContainText('Google Maps lookup is unavailable')
  await expect(page.locator('.commute-pin')).toHaveCount(3)
  await destination.getByLabel('Location entry method').selectOption('coordinates')
  await destination.getByLabel('Latitude', { exact: true }).fill('19.2')
  await destination.getByLabel('Longitude', { exact: true }).fill('72.9')
  await destination.getByRole('button', { name: 'Use coordinates', exact: true }).click()
  await expect(page.locator('.commute-pin')).toHaveCount(4)
})

test('daily commute save conflicts are surfaced on shortlist without overwriting edits', async ({ page }) => {
  const state = await mockApi(page)
  state.conflict = true
  await page.goto('/shortlist')
  await page.locator('.shortlist-commute > summary').click()
  await addCoordinateDestination(page)
  await page.getByRole('checkbox', { name: /I consent to saving my requirements/ }).check()
  await page.getByRole('button', { name: 'Save destinations', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Another session changed this draft' })).toBeVisible()
  await expect(page.locator('.commute-pin')).toHaveCount(1)
  expect(state.patches).toEqual([])
  await page.getByRole('button', { name: 'Use saved version', exact: true }).click()
  await expect(page.locator('.commute-pin')).toHaveCount(0)
})

test('daily commute lookup expiry clears private markers and editor state', async ({ page }) => {
  await mockApi(page)
  await page.route('**/api/v1/commute-locations/search', route => route.fulfill({ status: 403, json: {} }))
  await page.goto('/shortlist')
  await page.locator('.shortlist-commute > summary').click()
  await addCoordinateDestination(page)
  await page.getByText('Change map location', { exact: true }).click()
  await page.getByLabel('Search Google Maps', { exact: true }).fill('Private office')
  await page.getByRole('button', { name: 'Look up location', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.locator('.commute-pin')).toHaveCount(0)
  await expect(page.locator('.shortlist-commute')).toHaveCount(0)
  expect(await page.evaluate(() => JSON.stringify({ ...sessionStorage }))).not.toContain('Private office')
})

test('daily commute discards stale searches and prevents duplicate map destinations', async ({ page }) => {
  await mockApi(page)
  let release: (() => void) | undefined
  await page.route('**/api/v1/commute-locations/search', async route => {
    const query = route.request().postDataJSON().query
    if (query === 'First office') await new Promise<void>(resolve => { release = resolve })
    return route.fulfill({ json: { results: [{ placeId: 'google:same-office', label: query, precision: 'exact', latitude: 19.15, longitude: 72.95 }] } })
  })
  await page.goto('/shortlist')
  await page.locator('.shortlist-commute > summary').click()
  await page.getByRole('button', { name: 'Add destination', exact: true }).click()
  const first = page.getByRole('group', { name: 'Destination 1', exact: true })
  await first.getByText('Set map location', { exact: true }).click()
  await first.getByLabel('Search Google Maps', { exact: true }).fill('First office')
  await first.getByRole('button', { name: 'Look up location', exact: true }).click()
  await expect.poll(() => !!release).toBe(true)
  await first.getByLabel('Search Google Maps', { exact: true }).fill('Second office')
  release?.()
  await first.getByRole('button', { name: 'Look up location', exact: true }).click()
  await expect(first.getByRole('button', { name: 'Use First office', exact: true })).toHaveCount(0)
  await first.getByRole('button', { name: 'Use Second office', exact: true }).click()
  await expect(page.locator('.commute-pin')).toHaveAttribute('title', 'Daily commute 1: Second office')
  await page.getByRole('button', { name: 'Add destination', exact: true }).click()
  const second = page.getByRole('group', { name: 'Destination 2', exact: true })
  await second.getByText('Set map location', { exact: true }).click()
  await second.getByLabel('Search Google Maps', { exact: true }).fill('Same office')
  await second.getByRole('button', { name: 'Look up location', exact: true }).click()
  await second.getByRole('button', { name: 'Use Same office', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('That destination is already listed')
  await expect(page.locator('.commute-pin')).toHaveCount(1)
})

test('requested metro connections bridge only explicit branch endpoints and respect filters', async ({ page }) => {
  await mockApi(page, false)
  const names = [['Miragaon', 'Line 9'], ['Dahisar East', 'Line 9'], ['Gundavali', 'Line 7'], ['Airport Colony', 'Line 7A'], ['Kasarvadavali', 'Line 4'], ['Gowniwada', 'Line 4A']]
  const stations: MapStation[] = names.map(([name, line], index) => ({ id: `endpoint-${index}`, name, line, kind: 'metro', location: { latitude: 19.1 + index / 100, longitude: 72.9 }, serviceStatus: 'unverified', source: 'user_candidate', asOf: null, sourceUrl: null }))
  const connections: MapConnection[] = [0, 2, 4].map(index => ({ id: `connection-${index}`, kind: 'metro', name: stations[index + 1].line, lineNames: [...new Set([stations[index].line, stations[index + 1].line])], stationIds: [stations[index].id, stations[index + 1].id], geometryStatus: 'schematic_unverified', source: 'user_requested' }))
  const lines: MapLine[] = [{ id: 'not-an-authorized-branch', kind: 'metro', name: 'Line 7', stationIds: ['endpoint-2', 'endpoint-3'], geometryStatus: 'schematic_unverified', source: 'user_candidate' }]
  await page.route('**/api/v1/map-stations', route => route.fulfill({ json: { stations, connections, lines, warnings: [] } }))
  await page.goto('/browse')
  await expect(page.locator('.transit-line')).toHaveCount(3)
  await expect(page.locator('.transit-connection')).toHaveCount(3)
  for (const line of ['7', '7a', '4', '4a', '9']) {
    await page.getByLabel('Transit line', { exact: true }).selectOption(`metro:line ${line}`)
    await expect(page.locator('.transit-line')).toHaveCount(1)
    await expect(page.locator('.station-pin')).toHaveCount(2)
  }
  await page.getByRole('checkbox', { name: 'Unverified stations', exact: true }).uncheck()
  await expect(page.locator('.transit-line')).toHaveCount(0)
  await expect(page.locator('.station-pin')).toHaveCount(0)
  await page.getByRole('checkbox', { name: 'Unverified stations', exact: true }).check()
  await page.getByRole('checkbox', { name: /^Metro/ }).uncheck()
  await expect(page.locator('.transit-line')).toHaveCount(0)
})
async function noOverflow(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true) }
test('project picture loads on cards and failures restore placeholders', async ({ page }, info) => {
  await mockApi(page, false)
  await page.goto('/browse')
  const gallery = await mockGallery(page)
  await page.route('**/api/v1/projects/', route => route.fulfill({ json: {
    results: fixtures.map((project, index) => ({ ...project,
      projectPic: index === 0 ? '/api/v1/media/floor-plan-assets/image-one' : null })), next: null,
  } }))
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 })
    await page.reload()
    await page.getByRole('button', { name: 'Feed view', exact: true }).click()
    const card = page.locator(`#project-${projectIds[0]}`)
    await expect(card.locator('.project-picture')).toBeVisible()
    await expect.poll(() => card.locator('.project-picture').evaluate(image => (image as HTMLImageElement).naturalWidth)).toBe(800)
    await expect(page.locator(`#project-${projectIds[1]} .project-open span`)).toHaveText(fixtures[1].submittedName)
    await noOverflow(page)
    await page.screenshot({ path: info.outputPath(`project-picture-${width}.png`), fullPage: true })
  }
  gallery.imageFail = true
  await page.reload()
  await page.getByRole('button', { name: 'Feed view', exact: true }).click()
  await expect(page.locator(`#project-${projectIds[0]} .project-open span`)).toHaveText(fixtures[0].submittedName)
  await expect(page.locator('.project-picture')).toHaveCount(0)
})

async function mockGallery(page: Page) {
  const image = await page.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = 800; canvas.height = 600
    const context = canvas.getContext('2d')!
    context.fillStyle = '#ffffff'; context.fillRect(0, 0, 800, 600)
    context.lineWidth = 8; context.strokeStyle = '#1f3931'; context.font = '22px sans-serif'
    const rooms = ['Living', 'Bedroom 1', 'Bedroom 2', 'Kitchen', 'Bath', 'Balcony']
    rooms.forEach((room, index) => {
      const left = 40 + (index % 3) * 240, top = 40 + Math.floor(index / 3) * 260
      context.fillStyle = index % 2 ? '#e0eaf5' : '#d9e9d7'; context.fillRect(left, top, 225, 245)
      context.strokeRect(left, top, 225, 245); context.fillStyle = '#1f3931'; context.fillText(room, left + 14, top + 40)
      context.fillText('12 x 10 ft', left + 14, top + 75)
    })
    return canvas.toDataURL('image/png').split(',')[1]
  })
  const items: GalleryItem[] = [
    { id: 'image-one', url: '/api/v1/media/floor-plan-assets/image-one', title: 'East <b>Residence</b>', category: 'floor-plans', width: 800, height: 600, reviewState: 'approved', layouts: [{ id: 'layout-one', name: 'East <b>Residence</b>', configuration: '2BHK', carpetAreaSqFt: '780', price: '15000000.50', roomCount: 4, bathrooms: 2, balconies: 0, orientation: 'East', reviewState: 'approved', rooms: [{ room: 'Living', lengthFt: 12, widthFt: 10, areaSqFt: 120 }] }] },
    { id: 'image-two', url: '/api/v1/media/floor-plan-assets/image-two', title: 'West Residence', category: 'layouts', width: 800, height: 600, reviewState: 'approved', layouts: [] },
  ]
  const state = { fail: false, imageFail: false, empty: false, access: 'published', project: undefined as Project | undefined, items, pages: [] as string[] }
  await page.route('**/api/v1/projects/*/gallery*', route => {
    const url = new URL(route.request().url()); state.pages.push(url.search)
    const cursor = url.searchParams.get('cursor'), index = cursor === 'second' ? 1 : Number(cursor ?? 0)
    return route.fulfill({ status: state.fail ? 503 : 200, contentType: 'application/json', body: JSON.stringify({ access: state.access, project: state.access === 'restricted' ? undefined : state.project, results: state.empty || state.access === 'restricted' ? [] : [state.items[index]], next: !state.empty && state.access !== 'restricted' && index + 1 < state.items.length ? `${url.origin}${url.pathname}?cursor=${index === 0 ? 'second' : index + 1}` : null }) })
  })
  await page.route('**/api/v1/media/floor-plan-assets/*', route => route.fulfill({ status: state.imageFail ? 503 : 200, contentType: 'image/png', body: state.imageFail ? Buffer.from('') : Buffer.from(image, 'base64') }))
  return state
}
async function fillRequirements(page: Page) {
  await page.goto('/requirements')
  await page.getByRole('checkbox', { name: '2 BHK', exact: true }).check()
  await page.getByRole('button', { name: 'Summary', exact: true }).click()
  await page.getByLabel('Purchase maximum', { exact: true }).fill('20000000')
  await page.getByLabel('All-in maximum', { exact: true }).fill('22000000')
}

test('guest draft survives mode switches but does not persist sensitive fields; login submits a ranked score sheet', async ({ page }) => {
  const state = await mockApi(page, false)
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message))
  await fillRequirements(page)
  await page.getByRole('button', { name: 'Add destination', exact: true }).click()
  await page.getByLabel('Destination locality', { exact: true }).fill('Private fixture locality')
  await page.getByRole('button', { name: 'Guided', exact: true }).click()
  await page.getByRole('button', { name: 'Summary', exact: true }).click()
  await expect(page.getByLabel('Purchase maximum', { exact: true })).toHaveValue('20000000')
  await expect(page.getByLabel('Destination locality', { exact: true })).toHaveValue('Private fixture locality')
  const storage = await page.evaluate(() => JSON.stringify({ ...sessionStorage, ...localStorage }))
  expect(storage).not.toContain('20000000'); expect(storage).not.toContain('Private fixture locality')
  expect(state.patches).toHaveLength(0)
  await page.getByRole('checkbox', { name: /I agree to save/ }).check()
  await page.getByRole('button', { name: 'Sign in & find matches' }).click()
  await page.getByLabel('Username', { exact: true }).fill('fixture-buyer')
  await page.getByLabel('Password', { exact: true }).fill('fixture-only-password')
  await page.getByRole('dialog').getByRole('button', { name: 'Sign in as Buyer', exact: true }).click()
  await page.getByRole('button', { name: 'Find my matches', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your project matches' })).toBeVisible()
  expect(state.consents).toBe(1); expect(state.patches.length).toBeGreaterThan(0)
  expect(state.submitKeys[0]).toBeTruthy()
  await page.getByRole('button', { name: 'Score sheet', exact: true }).click()
  await expect(page.getByRole('table')).toContainText('Unknown')
  await expect(page.getByRole('table')).toContainText('0%')
  await noOverflow(page)
  expect(errors).toEqual([])
})

test('map/feed selection, server filters, shortlist persistence and comparison', async ({ page }, info) => {
  await mockApi(page)
  await page.goto(`/matches/${snapshotId}`)
  await expect(page.locator('.project-card')).toHaveCount(3)
  await expect(page.locator('.price-pin')).toHaveCount(2)
  await expect(page.locator('.price-pin[title="Fixture Gardens"]')).toHaveText('Fixture Gardens')
  await page.locator('.price-pin[title="Fixture Gardens"]').click()
  await expect(page.locator(`#project-${projectIds[1]}`)).toHaveClass(/selected/)
  await expect(page.locator('.map-preview')).toContainText('Fixture Gardens')
  await page.getByRole('button', { name: 'Shortlist Fixture Lakes', exact: true }).click()
  await page.getByRole('checkbox', { name: 'Compare', exact: true }).nth(0).check()
  await page.getByRole('checkbox', { name: 'Compare', exact: true }).nth(1).check()
  await page.getByRole('button', { name: 'Compare projects', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('Fixture Gardens')
  await page.keyboard.press('Escape')
  await page.getByLabel('Locality', { exact: true }).selectOption('Powai')
  await expect(page.locator('.project-card')).toHaveCount(2)
  await expect(page.locator('.price-pin')).toHaveCount(1)
  await noOverflow(page)
  await page.screenshot({ path: info.outputPath('desktop-map-feed.png'), fullPage: true })
  await page.getByRole('link', { name: 'Shortlist 1', exact: true }).click()
  await expect(page.locator('.project-card')).toHaveCount(1)
  await page.reload()
  await expect(page.locator('.project-card')).toHaveCount(1)
  await page.getByRole('button', { name: 'Remove Fixture Lakes', exact: true }).click()
  await expect(page.locator('.project-card')).toHaveCount(0)
})

test('map project names render as text and long names stay within the marker', async ({ page }) => {
  await mockApi(page)
  const name = 'A very long project name <b>not markup</b> with multiple buildings and gardens'
  await page.route(/\/api\/v1\/projects\/(?:\?.*)?$/, route => route.fulfill({
    json: { results: [{ ...fixtures[0], submittedName: name }], next: null },
  }))
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/browse')
  await page.getByRole('button', { name: 'Map view', exact: true }).click()
  const pin = page.locator('.price-pin')
  await expect(pin).toHaveCount(1)
  await expect(pin).toHaveAttribute('title', name)
  await expect(pin).toHaveText(name)
  await expect(pin.locator('b')).toHaveCount(0)
  const label = pin.locator('span')
  await expect(label).toHaveCSS('text-overflow', 'ellipsis')
  const bounds = await label.boundingBox(), pinBounds = await pin.boundingBox()
  expect(bounds).not.toBeNull()
  expect(pinBounds).not.toBeNull()
  expect(bounds!.width).toBeLessThanOrEqual(pinBounds!.width)
  await noOverflow(page)
})

test('mobile map selection, details, and summary do not overflow', async ({ page }, info) => {
  await mockApi(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/matches/${snapshotId}`)
  await expect(page.locator('.project-card')).toHaveCount(3)
  await page.getByRole('button', { name: 'Map view', exact: true }).click()
  await expect(page.locator('.project-map')).toBeVisible()
  await page.locator('.price-pin[title="Fixture Lakes"]').click()
  await page.getByRole('button', { name: 'View project', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('Unknown')
  await page.keyboard.press('Escape')
  await noOverflow(page)
  await page.screenshot({ path: info.outputPath('mobile-map.png'), fullPage: true })
  await fillRequirements(page)
  await noOverflow(page)
  await page.screenshot({ path: info.outputPath('mobile-requirements.png'), fullPage: true })
  await page.setViewportSize({ width: 320, height: 800 })
  await noOverflow(page)
})

test('layout-derived prices appear in cards, details and comparisons while map pins show project names', async ({ page }) => {
  await mockApi(page)
  await page.goto('/browse')
  const range = '\u20b91,50,00,000 - \u20b91,80,00,000'
  await expect(page.locator(`#project-${projectIds[0]} .price-row`)).toContainText(range)
  await expect(page.locator(`#project-${projectIds[2]} .price-row`)).toContainText('contact to know')
  await expect(page.locator('.price-pin[title="Fixture Lakes"]')).toHaveText('Fixture Lakes')
  await page.getByLabel('Sort by').selectOption('price')
  await expect(page.locator('.project-card').first()).toHaveAttribute('id', `project-${projectIds[0]}`)
  await expect(page.locator('.project-card').last()).toHaveAttribute('id', `project-${projectIds[2]}`)
  await page.getByRole('button', { name: 'View Fixture Lakes details and media', exact: true }).click()
  await expect(page.getByRole('dialog').locator('.detail-price')).toHaveText(range)
  await page.keyboard.press('Escape')
  await page.getByRole('checkbox', { name: 'Compare', exact: true }).nth(0).check()
  await page.getByRole('checkbox', { name: 'Compare', exact: true }).nth(2).check()
  await page.getByRole('button', { name: 'Compare projects', exact: true }).click()
  const row = page.getByRole('row').filter({ has: page.getByRole('rowheader', { name: 'Price range', exact: true }) })
  await expect(row).toContainText(range)
  await expect(row).toContainText('contact to know')
})

test('project gallery loads paginated media, layouts and published data on desktop and mobile', async ({ page }, info) => {
  const apiState = await mockApi(page, false)
  const galleryState = await mockGallery(page)
  await page.goto('/browse')
  const opener = page.getByRole('button', { name: 'View Fixture Lakes details and media', exact: true })
  await opener.click()
  const dialog = page.getByRole('dialog'), gallery = dialog.getByRole('region', { name: 'Project media' })
  await expect(gallery.locator('.gallery-toolbar').getByRole('status')).toHaveText('1 of 2')
  expect(galleryState.pages).toContain('?cursor=second')
  await expect(gallery.getByRole('button', { name: 'Previous image', exact: true })).toBeDisabled()
  await expect(gallery.locator('.gallery-image img')).toHaveJSProperty('naturalWidth', 800)
  await expect(gallery.locator('.gallery-caption b')).toHaveCount(0)
  await expect(gallery.getByRole('row', { name: 'Living 12 10 120' })).toBeVisible()
  await expect(gallery.locator('.layout-specs')).toContainText('780 sq ft')
  await expect(gallery.locator('.layout-specs')).toContainText('Total price (INR)\u20b91,50,00,000.5')
  await expect(gallery.locator('.layout-specs')).toContainText('Balconies0')
  await expect(dialog.locator('.project-amenities')).toContainText('gym')
  await expect(dialog).toContainText('Available layouts')
  const configurations = dialog.getByRole('table').filter({ hasText: 'Carpet area' })
  await expect(configurations.getByRole('columnheader', { name: 'Price', exact: true })).toBeVisible()
  await expect(configurations.getByRole('row').filter({ hasText: '2BHK' })).toContainText('₹1,50,00,000')
  await gallery.getByRole('button', { name: 'Next image', exact: true }).click()
  await expect(gallery.locator('.gallery-caption')).toContainText('West Residence')
  await expect(gallery.getByRole('button', { name: 'Next image', exact: true })).toBeDisabled()
  await expect(gallery.getByRole('link', { name: 'Open full-size image' })).toHaveAttribute('href', '/api/v1/media/floor-plan-assets/image-two')
  await gallery.getByLabel('Media type', { exact: true }).selectOption('floor-plans')
  await expect(gallery.locator('.gallery-toolbar').getByRole('status')).toHaveText('1 of 1')
  await gallery.getByLabel('Media type', { exact: true }).selectOption('all')
  await gallery.getByRole('button', { name: 'Image 2: West Residence', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(gallery.locator('.gallery-caption')).toContainText('West Residence')
  await gallery.getByRole('button', { name: 'Previous image', exact: true }).click()
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 })
    await expect(gallery.locator('.gallery-image img')).toHaveJSProperty('naturalWidth', 800)
    expect(await gallery.locator('.gallery-image img').evaluate((node: HTMLImageElement) => {
      const canvas = document.createElement('canvas'); canvas.width = 80; canvas.height = 60
      const context = canvas.getContext('2d')!; context.drawImage(node, 0, 0, 80, 60)
      return new Set(context.getImageData(0, 0, 80, 60).data).size
    })).toBeGreaterThan(20)
    await noOverflow(page)
    expect(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true)
    await dialog.evaluate(node => { node.scrollTop = 0 })
    await page.screenshot({ path: info.outputPath(`project-gallery-${width}.png`) })
  }
  await page.keyboard.press('Escape')
  await expect(opener).toBeFocused()
  expect(apiState.requests.some(request => request.startsWith('POST '))).toBe(false)
})

for (const processing of [false, true]) test(`individual layout aliases and calculated areas remain separate (${processing ? 'processing' : 'published'})`, async ({ page }) => {
  await mockApi(page, false)
  const gallery = await mockGallery(page)
  const names = ['1BHK - unit 5/6', '1BHK- Unit 3', '2BHK- Unit 1/2', '2BHK - unit 4/7 <East>']
  const project: Project = {
    ...fixtures[0], status: processing ? 'needs_review' : 'published',
    configurations: names.map((name, index) => ({
      id: `layout-${index}`, name, configurationType: index < 2 ? '1BHK' : '2BHK',
      carpetAreaMin: index === 2 ? '594' : null, carpetAreaMax: index === 2 ? '594' : null,
      roomRectangleSumSqFt: index === 1 ? null : 438.44 + index,
      priceMin: null, priceMax: null, bathrooms: 2,
    })),
  }
  gallery.access = processing ? 'preview' : 'published'
  gallery.project = project
  gallery.items[0].layoutIds = ['layout-0', 'layout-2']
  gallery.items.push(
    { ...gallery.items[0], id: 'floor-two', title: 'Second tower', layoutIds: ['layout-1', 'layout-3'] },
    { ...gallery.items[0], id: 'floor-empty', title: 'No extracted units', layoutIds: [] },
    { ...gallery.items[1], id: 'project-image', category: 'general' },
    { ...gallery.items[1], id: 'amenity-image', category: 'amenities' },
  )
  await page.route('**/api/v1/projects/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/gallery')) return route.fallback()
    return route.fulfill({ json: path === '/api/v1/projects/' ? { results: [project], next: null } : project })
  })
  await page.goto(processing ? '/browse?includeProcessing=true' : '/browse')
  await page.getByRole('region', { name: 'Project feed' }).getByRole('button', { name: 'Fixture Lakes', exact: true }).click()
  const dialog = page.getByRole('dialog'), table = dialog.locator('.layout-summary-table')
  await expect(table.locator('tbody tr')).toHaveCount(4)
  for (const name of names) await expect(table.getByRole('rowheader', { name, exact: true })).toBeVisible()
  await expect(table.locator('tbody tr').nth(0)).toContainText('Unknown')
  await expect(table.locator('tbody tr').nth(0)).toContainText('438.44 sq ft')
  await expect(table.locator('tbody tr').nth(0)).toContainText('contact to know')
  await expect(table.locator('tbody tr').nth(2)).toContainText('594 sq ft')
  await expect(table.locator('tbody tr').nth(2)).toContainText('440.44 sq ft')
  await expect(table.locator('tbody tr').nth(1).getByRole('cell').nth(1)).toHaveText('Unknown')
  await expect(table.locator('East')).toHaveCount(0)
  await expect(dialog).toContainText('is not carpet area')
  const mediaType = dialog.getByLabel('Media type', { exact: true })
  await mediaType.selectOption('general')
  await expect(table.locator('tbody tr')).toHaveCount(4)
  await mediaType.selectOption('floor-plans')
  await expect(table.locator('tbody tr')).toHaveCount(2)
  await expect(table.getByRole('rowheader')).toHaveText([names[0], names[2]])
  await dialog.getByRole('button', { name: 'Next image', exact: true }).click()
  await expect(table.getByRole('rowheader')).toHaveText([names[1], names[3]])
  await dialog.getByRole('button', { name: 'Next image', exact: true }).click()
  await expect(table).toHaveCount(0)
  await expect(dialog).toContainText('No layouts linked to this floor plan.')
  await dialog.getByRole('button', { name: 'Image 1: East <b>Residence</b>', exact: true }).click()
  await expect(table.getByRole('rowheader')).toHaveText([names[0], names[2]])
  for (const category of ['layouts', 'amenities']) {
    await mediaType.selectOption(category)
    await expect(table).toHaveCount(0)
    await expect(dialog.getByRole('heading', { name: 'Available layouts', exact: true })).toHaveCount(0)
  }
  await mediaType.selectOption('all')
  await expect(table.locator('tbody tr')).toHaveCount(4)
  await dialog.getByRole('button', { name: 'Next image', exact: true }).click()
  await expect(table.locator('tbody tr')).toHaveCount(4)
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 })
    await noOverflow(page)
    expect(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true)
  }
})

test('project gallery temporary public preview lets guests view processing data and media', async ({ page }) => {
  const state = await mockApi(page, false)
  const gallery = await mockGallery(page)
  gallery.access = 'preview'
  gallery.items[0].reviewState = 'unreviewed'; gallery.items[0].layouts[0].reviewState = 'needs_review'
  const processing: Project = { ...fixtures[0], id: 'processing-project', submittedName: 'Processing Heights', status: 'needs_review', configurations: [], amenities: [] }
  gallery.project = { ...processing, configurations: fixtures[0].configurations, amenities: ['gym'], facts: [{ code: 'project.towers', value: 3, unit: '', sourceClass: 'developer', reviewedAt: null, status: 'draft' }] }
  await page.route('**/api/v1/projects/?*', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ results: [processing], next: null }) }))
  await page.goto('/browse?includeProcessing=true')
  const opener = page.getByRole('region', { name: 'Project feed' }).getByRole('button', { name: 'Processing Heights', exact: true })
  await opener.click()
  await expect(page.getByRole('dialog')).toContainText('Unpublished media. Extracted details may be unreviewed.')
  await expect(page.getByRole('dialog')).toContainText('Temporary public preview.')
  await expect(page.getByRole('region', { name: 'Unpublished project data' })).toContainText('project.towers')
  await expect(page.getByRole('region', { name: 'Unpublished project data' })).toContainText('gym')
  await expect(page.getByRole('region', { name: 'Unpublished project data' }).locator('.detail-price')).toHaveText('\u20b91,50,00,000 - \u20b91,80,00,000')
  await expect(page.locator('.gallery-caption')).toContainText('Unreviewed')
  await expect(page.locator('.gallery-image img')).toHaveJSProperty('naturalWidth', 800)
  await expect(page.locator('.layout-details')).toContainText('Unreviewed layout')
  expect(state.requests).not.toContain('GET /api/v1/projects/processing-project/')
  await page.keyboard.press('Escape')
  gallery.access = 'restricted'
  await opener.click()
  await expect(page.getByRole('dialog')).toContainText('Processing media is restricted to project editors and reviewers.')
  await expect(page.getByRole('dialog').locator('img')).toHaveCount(0)
  expect(state.requests.some(request => request.startsWith('POST '))).toBe(false)
})

test('project gallery retries metadata and image failures and handles empty media', async ({ page }) => {
  await mockApi(page)
  const gallery = await mockGallery(page)
  gallery.fail = true
  await page.goto('/browse')
  const opener = page.getByRole('region', { name: 'Project feed' }).getByRole('button', { name: 'Fixture Lakes', exact: true })
  await opener.click()
  await expect(page.getByRole('button', { name: 'Retry media', exact: true })).toBeVisible()
  gallery.fail = false; gallery.imageFail = true
  await page.getByRole('button', { name: 'Retry media', exact: true }).click()
  await expect(page.getByText('Image unavailable.', { exact: true })).toBeVisible()
  gallery.imageFail = false
  await page.getByRole('button', { name: 'Retry image', exact: true }).click()
  await expect(page.locator('.gallery-image img')).toHaveJSProperty('naturalWidth', 800)
  await page.keyboard.press('Escape')
  gallery.empty = true
  await opener.click()
  await expect(page.getByText('No approved media available yet.', { exact: true })).toBeVisible()
  await expect(page.locator('.gallery-image')).toHaveCount(0)
})

test('conflicting autosave never overwrites another version', async ({ page }) => {
  const state = await mockApi(page)
  await fillRequirements(page)
  await page.getByRole('checkbox', { name: /I agree to save/ }).check()
  await expect.poll(() => state.patches.length).toBe(1)
  state.conflict = true
  await page.getByLabel('Purchase maximum', { exact: true }).fill('21000000')
  await expect(page.getByRole('heading', { name: 'Another session changed this draft' })).toBeVisible()
  expect(state.patches).toHaveLength(1)
  await page.getByRole('button', { name: 'Use saved version', exact: true }).click()
  await expect(page.getByLabel('Purchase maximum', { exact: true })).toHaveValue('20000000')
})

test('private-action session expiry clears sensitive entries and requests sign-in', async ({ page }) => {
  const state = await mockApi(page)
  await fillRequirements(page)
  state.signedIn = false
  await page.getByRole('link', { name: 'Discover', exact: true }).click()
  await page.getByRole('button', { name: 'Shortlist Fixture Lakes', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Welcome back' })).toBeVisible()
  await page.keyboard.press('Escape')
  await page.getByRole('link', { name: 'Find my matches', exact: true }).click()
  await page.getByRole('button', { name: 'Summary', exact: true }).click()
  await expect(page.getByLabel('Purchase maximum', { exact: true })).toHaveValue('')
  await expect(page.getByRole('checkbox', { name: '2 BHK', exact: true })).toBeChecked()
})

test('empty matches show excluding constraints and queued matches wait explicitly', async ({ page }) => {
  const state = await mockApi(page)
  state.empty = true
  await page.goto(`/matches/${snapshotId}`)
  await expect(page.getByRole('heading', { name: 'No projects match this view' })).toBeVisible()
  await expect(page.getByText('Above your hard budget', { exact: false })).toBeVisible()
  state.pending = true
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Finding your project matches' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Check status' })).toBeVisible()
})

test('guests browse listings and station layers without accessing private data', async ({ page }, info) => {
  const state = await mockApi(page, false)
  await page.goto('/')
  await page.getByRole('link', { name: 'Browse', exact: true }).click()
  await expect(page).toHaveURL(/\/browse$/)
  await expect(page.locator('.project-card')).toHaveCount(3)
  await expect(page.locator('.station-pin')).toHaveCount(3)
  await expect(page.locator('.price-pin.registry-verified')).toHaveCount(1)
  expect(state.requests.some(request => /shortlists|requirement-profiles|consents|amenities/.test(request))).toBe(false)
  await page.getByRole('checkbox', { name: /Unverified stations/ }).uncheck()
  await expect(page.locator('.station-pin')).toHaveCount(1)
  await page.locator('.station-pin[title="Fixture Metro (metro)"]').click()
  await expect(page.locator('.leaflet-popup')).toContainText('Operational')
  await expect(page.locator('.leaflet-popup')).toContainText('Authority catalogue')
  await page.locator('.leaflet-popup-close-button').click()
  await page.getByRole('checkbox', { name: /Unverified stations/ }).check()
  await page.locator('.station-pin[title="Added <b>Metro</b> (metro)"]').click()
  await expect(page.locator('.station-details')).toContainText('Added <b>Metro</b>')
  await expect(page.locator('.station-details b')).toHaveCount(0)
  await expect(page.locator('.station-details')).toContainText('Status unverified')
  await page.getByRole('checkbox', { name: /^Metro/ }).uncheck()
  await expect(page.locator('.station-pin')).toHaveCount(1)
  await page.getByRole('checkbox', { name: /^Metro/ }).check()
  await page.getByRole('region', { name: 'Project feed' }).getByRole('button', { name: 'Fixture Lakes', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('Available layouts')
  await page.keyboard.press('Escape')
  await page.getByRole('checkbox', { name: 'MahaRERA verified', exact: true }).click()
  await expect(page).toHaveURL(/verified=true/)
  await expect(page.getByRole('checkbox', { name: 'MahaRERA verified', exact: true })).toBeChecked()
  await expect(page.locator('.project-card')).toHaveCount(2)
  await expect(page.locator('.price-pin')).toHaveCount(1)
  await noOverflow(page)
  await page.screenshot({ path: info.outputPath('guest-browse-desktop.png'), fullPage: true })
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 })
    await page.getByRole('button', { name: 'Map view', exact: true }).click()
    await expect(page.locator('.project-map')).toBeVisible()
    await expect(page.locator('.station-pin')).toHaveCount(3)
    await expect.poll(() => page.locator('.project-map').evaluate(map => {
      const bounds = map.getBoundingClientRect()
      return [...map.querySelectorAll('.station-pin')].every(marker => {
        const pin = marker.getBoundingClientRect()
        return pin.left >= bounds.left && pin.right <= bounds.right && pin.top >= bounds.top && pin.bottom <= bounds.bottom
      })
    })).toBe(true)
    await noOverflow(page)
    await page.screenshot({ path: info.outputPath(`guest-browse-${width}.png`), fullPage: true })
  }
  await page.getByRole('button', { name: 'Feed view', exact: true }).click()
  await page.getByRole('button', { name: 'Shortlist Fixture Lakes', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Welcome back' })).toBeVisible()
  expect(state.requests.some(request => request.startsWith('POST '))).toBe(false)
})

test('processing catalogue is off by default and preserves filters without private actions', async ({ page }, info) => {
  const state = await mockApi(page, false)
  const processing: Project = { ...fixtures[0], id: 'd0000000-0000-4000-8000-000000000001', submittedName: 'Processing Heights', status: 'ingestion', configurations: [], amenities: [], mahaReraId: '', verificationStatus: 'unavailable', verificationCheckedAt: null }
  let fail = false
  const queries: string[] = []
  await page.route('**/api/v1/projects/?*', route => {
    const query = new URL(route.request().url()).searchParams
    queries.push(query.toString())
    if (fail) return route.fulfill({ status: 503, body: '{}' })
    const items = [...fixtures, ...(query.get('includeProcessing') === 'true' ? [processing] : [])].filter(project => (!query.has('locality') || project.locality === query.get('locality')) && (!query.has('mahaReraStatus') || project.verificationStatus === query.get('mahaReraStatus')))
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ results: items, next: null }) })
  })
  await page.goto('/browse')
  const toggle = page.getByRole('checkbox', { name: 'Show processing listings', exact: true })
  await expect(toggle).not.toBeChecked()
  await expect(page.locator('.project-card')).toHaveCount(3)
  expect(queries.some(query => query.includes('includeProcessing=true'))).toBe(false)
  await toggle.click()
  await expect(toggle).toBeChecked()
  await expect(page.locator('.project-card')).toHaveCount(4)
  const card = page.locator(`#project-${processing.id}`)
  await expect(card.locator('.processing-label')).toHaveText('Processing listing')
  await expect(card.getByRole('button', { name: 'Shortlist Processing Heights' })).toBeDisabled()
  await expect(card.getByRole('checkbox', { name: 'Compare' })).toBeDisabled()
  await card.getByRole('button', { name: 'On map', exact: true }).click()
  await expect(page.locator('.map-preview')).toContainText('Processing listing')
  await card.getByRole('button', { name: 'Processing Heights', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('Project facts are not published yet.')
  await expect(page.getByRole('dialog')).toContainText('Processing media is restricted to project editors and reviewers.')
  expect(new Set(state.requests.filter(request => request.includes(processing.id)))).toEqual(
    new Set([`GET /api/v1/projects/${processing.id}/gallery`]),
  )
  await page.keyboard.press('Escape')
  await page.getByLabel('Locality', { exact: true }).selectOption('Powai')
  await expect(page.locator('.project-card')).toHaveCount(3)
  await page.getByRole('checkbox', { name: 'MahaRERA verified', exact: true }).click()
  await expect(card).toHaveCount(0)
  await page.getByRole('checkbox', { name: 'MahaRERA verified', exact: true }).click()
  await expect(card).toBeVisible()
  await page.reload()
  await expect(toggle).toBeChecked()
  await expect(page.locator('.project-card')).toHaveCount(3)
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 })
    await page.getByRole('button', { name: 'Feed view', exact: true }).click()
    await noOverflow(page)
    await page.screenshot({ path: info.outputPath(`processing-${width}.png`), fullPage: true })
  }
  await toggle.click()
  await expect(toggle).not.toBeChecked()
  await expect(card).toHaveCount(0)
  await expect(page.locator('.project-card')).toHaveCount(2)
  fail = true
  await toggle.click()
  await expect(toggle).toBeChecked()
  await expect(page.getByText('Projects could not be loaded')).toBeVisible()
  fail = false
  await page.getByRole('button', { name: 'Retry', exact: true }).click()
  await expect(card).toBeVisible()
  await toggle.click()
  await expect(toggle).not.toBeChecked()
  await page.reload()
  await expect(toggle).not.toBeChecked()
  await expect(card).toHaveCount(0)
  expect(state.requests.some(request => request.startsWith('POST '))).toBe(false)
})

test('local stations and properties share the default map without an embed', async ({ page }, info) => {
  const state = await mockApi(page, false)
  const googleRequests: string[] = []
  await page.route('https://www.google.com/**', route => { googleRequests.push(route.request().url()); return route.abort() })
  await page.goto('/browse?locality=Powai')
  const map = page.getByRole('region', { name: 'Projects and stations map', exact: true })
  await expect(map).toBeVisible()
  await expect(page.locator('iframe')).toHaveCount(0)
  await expect(page.getByRole('group', { name: 'Map source', exact: true })).toHaveCount(0)
  await expect(map.locator('.station-pin')).toHaveCount(3)
  await expect(map.locator('.price-pin')).toHaveCount(1)
  expect(state.requests).toContain('GET /api/v1/map-stations')
  await expect(page.locator('.project-card')).toHaveCount(2)
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    if (width < 760) await page.getByRole('button', { name: 'Map view', exact: true }).click()
    await expect(map.locator('.project-map')).toBeVisible()
    expect((await map.locator('.project-map').boundingBox())!.height).toBeGreaterThanOrEqual(300)
    await noOverflow(page)
    await page.screenshot({ path: info.outputPath(`combined-map-${width}.png`), fullPage: true })
  }
  await page.getByRole('button', { name: 'Feed view', exact: true }).click()
  await page.locator(`#project-${projectIds[0]}`).getByRole('button', { name: 'On map', exact: true }).click()
  await expect(page.locator('.map-preview')).toContainText('Fixture Lakes')
  await expect(page.locator('.price-pin')).toHaveCount(1)
  await expect(page.locator('.station-pin')).toHaveCount(3)
  await expect(page).toHaveURL(/locality=Powai/)
  await expect(map.locator('.leaflet-control-attribution')).toContainText('OpenStreetMap')
  expect(googleRequests).toEqual([])
})

test('station failure does not hide public projects and retries independently', async ({ page }) => {
  const state = await mockApi(page, false)
  state.stationError = true
  await page.goto('/browse')
  await expect(page.locator('.project-card')).toHaveCount(3)
  await expect(page.getByRole('button', { name: 'Retry stations', exact: true })).toBeVisible()
  state.stationError = false
  await page.getByRole('button', { name: 'Retry stations', exact: true }).click()
  await expect(page.locator('.station-pin')).toHaveCount(3)
  state.catalogueError = true
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Projects could not be loaded' })).toBeVisible()
  await expect(page.locator('.station-pin')).toHaveCount(3)
  state.catalogueError = false
  await page.getByRole('button', { name: 'Retry', exact: true }).click()
  await expect(page.locator('.project-card')).toHaveCount(3)
})

test('unavailable basemap does not block stations, projects or the feed', async ({ page }) => {
  await mockApi(page, false)
  await page.goto('/browse')
  await expect(page.getByRole('button', { name: 'Retry basemap', exact: true })).toBeVisible()
  await expect(page.locator('.project-card')).toHaveCount(3)
  await expect(page.locator('.price-pin')).toHaveCount(2)
  await expect(page.locator('.station-pin')).toHaveCount(3)
  const retryRequest = page.waitForRequest('https://tile.openstreetmap.org/**')
  await page.getByRole('button', { name: 'Retry basemap', exact: true }).click()
  await retryRequest
  await expect(page.locator('iframe')).toHaveCount(0)
})

test('stations remain visible before any projects are published', async ({ page }) => {
  await mockApi(page, false)
  await page.route('**/api/v1/projects/', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ results: [], next: null }) }))
  await page.goto('/browse')
  await expect(page.getByRole('heading', { name: 'No projects match this view', exact: true })).toBeVisible()
  await expect(page.locator('.price-pin')).toHaveCount(0)
  await expect(page.locator('.station-pin')).toHaveCount(3)
  await expect(page.locator('.project-map')).toBeVisible()
})

test('schematic lines use the requested Mumbai palette with readable station labels', async ({ page }) => {
  await mockApi(page, false)
  const palette = [
    ['Line 1', '#0072bc'], ['Line 2', '#ffcc00'], ['Line 2A', '#ffcc00'], ['Line 2B', '#ffcc00'],
    ['Line 3', '#00b5ad'], ['Line 4', '#00843d'], ['Line 4A', '#00843d'], ['Line 5', '#f58220'],
    ['Line 6', '#f06292'], ['Line 7', '#d9232e'], ['Line 7A', '#d9232e'], ['Line 8', '#000000'],
    ['Line 9', '#d9232e'], ['Line 10', '#000000'], ['Line 11', '#000000'], ['Line 12', '#f37a48'],
  ]
  const stations: MapStation[] = palette.flatMap(([name], index) => [0, 1].map(stop => ({
    id: `${index}-${stop}`, name: `${name} stop ${stop}`, kind: 'metro', line: name,
    location: { latitude: 19.05 + index * .01, longitude: 72.9 + stop * .05 },
    serviceStatus: 'unverified', source: 'user_candidate', asOf: null, sourceUrl: null,
  })))
  const lines: MapLine[] = palette.map(([name], index) => ({
    id: `line-${index}`, name, kind: 'metro', stationIds: [`${index}-0`, `${index}-1`],
    geometryStatus: 'schematic_unverified', source: 'user_candidate',
  }))
  await page.route('**/api/v1/map-stations', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ stations, lines, warnings: [] }) }))
  await page.goto('/browse')
  const paths = page.locator('path.transit-line')
  await expect(paths).toHaveCount(palette.length)
  expect(await paths.evaluateAll(elements => elements.map(element => element.getAttribute('stroke')))).toEqual(palette.map(([, color]) => color))
  expect(await paths.evaluateAll(elements => elements.every(element => element.getAttribute('stroke-width') === '7' && element.getAttribute('stroke-opacity') === '1' && !element.hasAttribute('stroke-dasharray')))).toBe(true)
  await page.getByRole('button', { name: 'Zoom out', exact: true }).click()
  await page.getByRole('button', { name: 'Zoom out', exact: true }).click()
  await expect(page.locator('.station-pin.compact')).toHaveCount(stations.length)
  await expect(page.locator('.station-pin').first()).toHaveCSS('width', '10px')
  for (let zoomStep = 0; zoomStep < 4; zoomStep += 1) await page.getByRole('button', { name: 'Zoom in', exact: true }).click()
  await expect(page.locator('.station-pin.compact')).toHaveCount(0)
  await expect(page.locator('.station-pin').first()).toHaveCSS('width', '32px')
  await page.getByRole('button', { name: 'Fit map locations', exact: true }).click()
  await page.locator('.transit-line-legend summary').click()
  function luminance(color: string) {
    const channels = color.match(/\d+/g)!.map(value => {
      const channel = Number(value) / 255
      return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4
    })
    return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722
  }
  for (const [name, color] of palette) {
    const rgb = `rgb(${[1, 3, 5].map(offset => Number.parseInt(color.slice(offset, offset + 2), 16)).join(', ')})`
    const marker = page.locator(`.station-pin[title="${name} stop 0 (metro)"] > span`)
    await expect(marker).toHaveCSS('background-color', rgb)
    await expect(page.locator('.transit-line-legend li').filter({ hasText: new RegExp(`^Metro \\| ${name}$`) }).locator('.line-swatch')).toHaveCSS('background-color', rgb)
    const foreground = luminance(await marker.evaluate(element => getComputedStyle(element).color))
    const background = luminance(rgb)
    expect((Math.max(foreground, background) + .05) / (Math.min(foreground, background) + .05)).toBeGreaterThanOrEqual(4.5)
  }
  await page.getByLabel('Transit line', { exact: true }).selectOption('metro:line 12')
  await expect(paths).toHaveCount(1)
  await expect(paths.first()).toHaveAttribute('stroke', '#f37a48')
  await expect(page.getByRole('checkbox', { name: 'Schematic lines', exact: true })).toHaveCount(0)
})

test('schematic lines stay separate and filters never connect across hidden stations', async ({ page }, info) => {
  await mockApi(page, false)
  const stations: MapStation[] = [
    { id: 'first', name: 'First', kind: 'metro', line: 'Line 1', location: { latitude: 19.1, longitude: 72.9 }, serviceStatus: 'operational', source: 'authority', asOf: null, sourceUrl: null },
    { id: 'middle', name: 'Middle', kind: 'metro', line: 'Line 1', location: { latitude: 19.15, longitude: 72.95 }, serviceStatus: 'unverified', source: 'user_candidate', asOf: null, sourceUrl: null },
    { id: 'end', name: 'End', kind: 'metro', line: 'Line 1', location: { latitude: 19.2, longitude: 73 }, serviceStatus: 'operational', source: 'authority', asOf: null, sourceUrl: null },
    { id: 'shared', name: 'Shared location', kind: 'metro', line: 'Line 2A', location: { latitude: 19.2, longitude: 73 }, serviceStatus: 'unverified', source: 'user_candidate', asOf: null, sourceUrl: null },
    { id: 'other', name: 'Other line', kind: 'metro', line: 'Line 2A', location: { latitude: 19.25, longitude: 73.05 }, serviceStatus: 'unverified', source: 'user_candidate', asOf: null, sourceUrl: null },
    { id: 'unknown', name: 'Unknown line', kind: 'railway', line: '', location: { latitude: 19.18, longitude: 73.05 }, serviceStatus: 'unverified', source: 'saved_reference', asOf: null, sourceUrl: null },
  ]
  const lines: MapLine[] = [
    { id: 'line-one', name: 'Line 1', kind: 'metro', stationIds: ['first', 'middle', 'end'], geometryStatus: 'schematic_unverified', source: 'user_candidate' },
    { id: 'line-two', name: 'Line 2A', kind: 'metro', stationIds: ['shared', 'other'], geometryStatus: 'schematic_unverified', source: 'user_candidate' },
    { id: 'mismatched', name: 'Line 1', kind: 'metro', stationIds: ['end', 'shared', 'missing'], geometryStatus: 'schematic_unverified', source: 'user_candidate' },
  ]
  await page.route('**/api/v1/map-stations', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ stations, lines, warnings: [] }) }))
  await page.goto('/browse')
  const paths = page.locator('path.transit-line')
  await expect(page.locator('.station-pin')).toHaveCount(6)
  await expect(paths).toHaveCount(3)
  await expect(page.getByRole('checkbox', { name: 'Schematic lines', exact: true })).toHaveCount(0)
  await expect(paths.first()).not.toHaveAttribute('stroke-dasharray')
  await expect(paths.first()).toHaveAttribute('stroke-width', '7')
  await expect(paths.first()).toHaveAttribute('stroke-opacity', '1')
  await expect(page.locator('path.transit-line-outline')).toHaveCount(3)
  await expect(page.locator('path.transit-line-outline').first()).toHaveAttribute('stroke-width', '11')
  await expect(page.locator('.line-evidence-note')).toContainText('Crossings do not confirm transfers')
  await expect(page.locator('.station-pin[title="First (metro)"]')).toHaveText('M1')
  await expect(page.locator('.station-pin[title="Other line (metro)"]')).toHaveText('M2A')
  const firstColor = await paths.first().getAttribute('stroke')
  expect(await paths.last().getAttribute('stroke')).not.toBe(firstColor)
  await page.getByLabel('Transit line', { exact: true }).selectOption('metro:line 1')
  await expect(paths).toHaveCount(2)
  await expect(page.locator('.station-pin')).toHaveCount(3)
  await expect(page.locator('.price-pin')).toHaveCount(2)
  expect(await paths.evaluateAll(elements => new Set(elements.map(element => element.getAttribute('stroke'))).size)).toBe(1)
  await page.getByRole('checkbox', { name: 'Unverified stations', exact: true }).uncheck()
  await expect(page.locator('.station-pin')).toHaveCount(2)
  await expect(paths).toHaveCount(0)
  await expect(page.locator('path.transit-line-outline')).toHaveCount(0)
  await page.getByRole('checkbox', { name: 'Unverified stations', exact: true }).check()
  await expect(paths).toHaveCount(2)
  await expect(page.locator('.station-pin')).toHaveCount(3)
  await page.getByLabel('Transit line', { exact: true }).selectOption('railway:')
  await expect(paths).toHaveCount(0)
  await expect(page.locator('.station-pin')).toHaveCount(1)
  await page.getByLabel('Transit line', { exact: true }).selectOption('')
  await page.getByRole('checkbox', { name: /^Metro/ }).uncheck()
  await expect(paths).toHaveCount(0)
  await expect(page.locator('.station-pin')).toHaveCount(1)
  await page.getByRole('checkbox', { name: /^Metro/ }).check()
  await page.locator('.transit-line-legend summary').click()
  await expect(page.locator('.transit-line-legend')).toContainText('Metro | Line 2A')
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 })
    if (width < 760) await page.getByRole('button', { name: 'Map view', exact: true }).click()
    await expect(paths).toHaveCount(3)
    await expect(page.locator('.project-map')).toBeVisible()
    await noOverflow(page)
    await page.screenshot({ path: info.outputPath(`schematic-lines-${width}.png`), fullPage: true })
  }
})