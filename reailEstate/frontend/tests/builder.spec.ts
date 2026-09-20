import { test as base, expect, type Page } from '@playwright/test'

const test = base.extend<{ clientErrors: string[] }>({
  clientErrors: [async ({ page }, use) => {
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await use(errors)
    expect(errors).toEqual([])
  }, { auto: true }],
})
const organizationId = 'd0000000-0000-4000-8000-000000000001'
const secondOrganizationId = 'd0000000-0000-4000-8000-000000000002'
const projectId = 'e0000000-0000-4000-8000-000000000001'
const layoutId = 'f0000000-0000-4000-8000-000000000001'
const floorId = 'f0000000-0000-4000-8000-000000000002'
const documentId = 'a1000000-0000-4000-8000-000000000001'
const projectPath = `/api/v1/builder/projects/${projectId}`
const documentPath = `/api/v1/developer-projects/${projectId}/documents`
const mediaPath = `${projectPath}/media`
const assetId = 'a2000000-0000-4000-8000-000000000001'
const draftId = 'a3000000-0000-4000-8000-000000000001'
const profileId = 'a4000000-0000-4000-8000-000000000001'
const sourceSha256 = 'a'.repeat(64)
const initialProject = {
  id: projectId, organizationId, organizationName: 'Fixture Builders', submittedName: 'Builder Gardens',
  mahaReraId: 'P51800000001', locality: 'Powai',
  address: { line1: 'Evidence Road', line2: '', city: 'Mumbai', pincode: '400076' },
  processingAttested: true, status: 'draft', verificationStatus: 'manual_required',
  updatedAt: '2026-09-19T10:00:00Z', createdAt: '2026-09-19T10:00:00Z',
}
const initialLayout = { id: layoutId, name: 'Garden 2BHK', price: '15000000.00' as string | null, configurationType: '2BHK', carpetAreaSqFt: '740', updatedAt: '2026-09-19T10:00:00Z', reviewState: 'pending_review' }
type FixtureEvaluation = { state: string; label: string; canRun: boolean; error?: string; retryAt?: string; attempts?: number }
type FixtureRoom = { room: string; lengthFt: number | null; widthFt: number | null; areaSqFt: number | null }
async function mockBuilder(page: Page, signedIn = true) {
  const png = Buffer.from(await page.evaluate(() => {
    const canvas = document.createElement('canvas')
    canvas.width = 400; canvas.height = 600
    const context = canvas.getContext('2d')!
    context.fillStyle = '#eef5f1'; context.fillRect(0, 0, 400, 600)
    context.fillStyle = '#203d31'; context.fillRect(40, 60, 160, 240)
    return canvas.toDataURL('image/png').split(',')[1]
  }), 'base64')
  const state = {
    imageBytes: png, mediaImageUrl: `${mediaPath}/assets/${assetId}`,
    signedIn, builder: true, rejectLogin: false, conflict: false, expirePatch: false,
    failComplete: false, unsafeUpload: false, reviewFailure: false, duplicateUpload: false,
    project: structuredClone(initialProject),
    projects: [structuredClone(initialProject)],
    layout: structuredClone(initialLayout),
    extraLayouts: [] as typeof initialLayout[],
    evaluations: { [layoutId]: { state: 'completed', label: 'Extraction completed - awaiting review', canRun: true, attempts: 1 } } as Record<string, FixtureEvaluation>,
    rooms: { [layoutId]: [{ room: 'Living room', lengthFt: 12.5, widthFt: 10, areaSqFt: 125 }, { room: 'Kitchen', lengthFt: 8, widthFt: null, areaSqFt: null }] } as Record<string, FixtureRoom[]>,
    documents: [] as { id: string; originalName: string; status: string; scanStatus: string; byteLength: number }[],
    requests: [] as { method: string; path: string; body: Record<string, unknown>; headers: Record<string, string> }[],
    completionKeys: [] as string[], intents: 0, uploads: 0, revision: 0,
    mediaEditable: true, confirmNameChanges: false, mediaForbidden: false, mediaConflict: false, cropFailure: false, cropValidationFailure: false, mediaActionFailure: false,
    floorName: 'First floor', floorActive: true, planActive: true, planRemoved: false, longAmenityLabel: false,
    mediaDocuments: [{ id: documentId, name: 'evidence.pdf', pageCount: 2, sha256: sourceSha256 }] as { id: string; name: string; pageCount: number; sha256: string; error?: string }[],
    mediaDrafts: [] as { id: string; label: string; imageUrl: string }[],
    mediaJobs: [] as { id: string; kind: string; state: string; error: string | null }[],
  }
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname, method = request.method()
    const body = method !== 'PUT' && request.postData() && !request.headers()['content-type']?.startsWith('multipart/') ? request.postDataJSON() as Record<string, unknown> : {}
    state.requests.push({ method, path, body, headers: request.headers() })
    const respond = (data: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) })
    const bump = () => `2026-09-19T10:00:${String(++state.revision).padStart(2, '0')}Z`
    if (path === '/api/v1/auth/builder-login' || path === '/api/v1/auth/login') {
      if (method === 'POST' && state.rejectLogin) return respond({ errors: { detail: 'Invalid credentials.' } }, 403)
      if (method === 'POST' && !state.builder && path.endsWith('builder-login')) return respond({ errors: { detail: 'Your credentials are correct, but builder access is not assigned. Ask a platform administrator to add an active owner or editor membership in an active organization.' } }, 403)
      if (method === 'POST') state.signedIn = true
      return route.fulfill({ contentType: 'application/json', headers: { 'Set-Cookie': 'csrftoken=builder-test-csrf; Path=/; SameSite=Lax' }, body: JSON.stringify(method === 'POST' ? { id: 51, username: 'fixture-builder', email: '' } : { status: 'ready' }) })
    }
    if (path === '/api/v1/projects/') return respond([])
    if (path === '/api/v1/map-stations') return respond({ stations: [], warnings: [] })
    if (!state.signedIn) return respond({ detail: 'Sign in required.' }, 401)
    if (path === '/api/v1/auth/me') return respond({ id: 51, username: 'fixture-builder', email: '', isStaff: false })
    if (path === '/api/v1/auth/logout') { state.signedIn = false; return route.fulfill({ status: 204 }) }
    if (['/api/v1/requirement-profiles/', '/api/v1/shortlists/', '/api/v1/amenities'].includes(path)) return respond([])
    if (path === '/api/v1/builder/organizations') return respond(state.builder ? [{ id: organizationId, legalName: 'Fixture Builders', role: 'owner' }, { id: secondOrganizationId, legalName: 'Second Builders', role: 'editor' }] : [])
    if (path === '/api/v1/builder/projects') {
      if (method === 'POST') {
        expect(body.organizationId).toBe(organizationId)
        expect(body).not.toHaveProperty('status')
        state.project = { ...initialProject, ...body, status: 'verifying', updatedAt: bump() } as typeof initialProject
        state.projects = [state.project]
        return respond(state.project, 201)
      }
      return respond(state.projects)
    }
    if (path === projectPath) {
      if (method === 'PATCH') {
        if (state.expirePatch) { state.signedIn = false; return respond({ detail: 'Session expired.' }, 401) }
        if (state.conflict) { state.project = { ...state.project, submittedName: 'Saved by another editor', updatedAt: bump() }; return respond({ detail: 'Another editor updated this project.' }, 409) }
        expect(body.expectedUpdatedAt).toBe(state.project.updatedAt)
        expect(body).not.toHaveProperty('organizationId')
        expect(body).not.toHaveProperty('status')
        state.project = { ...state.project, ...body, status: state.project.status === 'published' ? 'needs_review' : state.project.status, updatedAt: bump() } as typeof initialProject
      }
      return respond(state.project)
    }
    if (path === `${projectPath}:archive`) {
      expect(body.expectedUpdatedAt).toBe(state.project.updatedAt)
      state.project = { ...state.project, status: 'retired', updatedAt: bump() }
      return respond(state.project)
    }
    if (path.startsWith(mediaPath)) {
      if (state.mediaForbidden) return respond({ detail: 'Organization membership no longer permits access to this project.' }, 403)
      if (method === 'GET' && path !== mediaPath) return route.fulfill({ contentType: 'image/png', body: png })
      if (path === mediaPath) {
        const item = { documentId, pageNumber: 1, sourceSha256, canCrop: true, canEvaluate: false, updatedAt: null, planId: null, assetId, imageUrl: state.mediaImageUrl, active: true, reviewState: 'needs_review' }
        return respond({
          projectId, projectName: state.project.submittedName, projectPrice: null, projectUpdatedAt: state.project.updatedAt, canEdit: state.mediaEditable, confirmNameChanges: state.confirmNameChanges,
          items: [
            { ...item, id: assetId, category: 'project-images', label: 'Building exterior' },
            { ...item, id: 'a2000000-0000-4000-8000-000000000002', category: 'project-images', label: 'Entrance view' },
            { ...item, id: 'a2000000-0000-4000-8000-000000000003', category: 'amenities', label: state.longAmenityLabel ? 'Amenities brochure with a very long original document filename and collection of pool images - page 9' : 'Pool' },
            { ...item, id: floorId, category: 'floor-plans', label: state.floorName, displayName: state.floorName, planId: floorId, updatedAt: state.layout.updatedAt, active: state.floorActive },
            ...[state.layout, ...state.extraLayouts].map(layout => ({
              ...item, id: layout.id, category: 'layouts', label: layout.name, displayName: layout.name, planId: layout.id,
              updatedAt: layout.updatedAt, canEvaluate: true, active: state.planActive, reviewState: state.planRemoved ? 'removed_by_user' : 'needs_review',
              configuration: layout.configurationType, area: layout.carpetAreaSqFt,
              dimensions: state.rooms[layout.id] ?? [],
              roomRectangleSumSqFt: state.rooms[layout.id]?.some(room => room.areaSqFt != null) ? state.rooms[layout.id].reduce((total, room) => total + (room.areaSqFt ?? 0), 0) : null,
              roomCount: 2, bathrooms: 1, balconies: 0, orientation: 'East', confidence: 0.9, sourceKind: 'unit_crop',
              extractorVersion: 'fixture-extractor', features: { balcony: false },
              evaluation: state.evaluations[layout.id] ?? { state: 'not_queued', label: 'No page/crop extraction job found', canRun: true },
            })),
          ],
          documents: state.mediaDocuments,
          profiles: [{ id: profileId, label: 'Detailed evaluation' }], drafts: state.mediaDrafts, jobs: state.mediaJobs,
        })
      }
      if (state.mediaConflict) return respond({ detail: 'The source digest or layout version changed.' }, 409)
      if (path === `${mediaPath}/actions`) {
        if (state.mediaActionFailure) return respond({ detail: 'Media action response unavailable.' }, 503)
        if (body.action === 'rename') {
          const targets = body.targets as { planId: string }[]
          if (targets[0].planId === floorId) state.floorName = String(body.name)
          else state.layout.name = String(body.name)
        }
        if (body.action === 'set-active') {
          const targets = body.targets as { planId: string }[]
          if (targets.some(target => target.planId === floorId)) {
            state.floorActive = body.active === true
            if (!state.floorActive) state.planActive = false
          }
          if (targets.some(target => target.planId === layoutId)) state.planActive = body.active === true
        }
        if (body.action === 'remove') state.planRemoved = true
        if (body.action === 'restore') state.planRemoved = false
        return respond({ state: 'updated' }, 202)
      }
      if (path === `${mediaPath}/picture`) return respond({ state: 'updated' }, 202)
      if (path === `${mediaPath}/crops`) {
        expect(body.requestId).toMatch(/^[a-f0-9-]{36}$/)
        if (state.cropFailure) return respond({ detail: 'Crop response temporarily unavailable.' }, 503)
        if (state.cropValidationFailure) return respond({ status: 400, errors: { detail: 'Select a crop with visible pixels.' } }, 400)
        const regions = body.regions as { label: string }[]
        state.mediaDrafts = regions.map((region, index) => {
          const id = `${draftId.slice(0, -12)}${(index + 1).toString(16).padStart(12, '0')}`
          return { id, label: region.label, imageUrl: `${mediaPath}/drafts/${id}` }
        })
        return respond({ draftIds: state.mediaDrafts.map(draft => draft.id), state: 'drafts_saved', cartCount: state.mediaDrafts.length }, 202)
      }
      if (path === `${mediaPath}/drafts`) {
        if (state.mediaActionFailure) return respond({ detail: 'Media action response unavailable.' }, 503)
        if (body.action === 'attach') state.mediaJobs.push({ id: 'attach-job', kind: 'layout_evaluation', state: 'queued', error: null })
        state.mediaDrafts = []
        return respond({ state: 'accepted', planIds: body.action === 'attach' ? [layoutId] : [] }, 202)
      }
      if (path === `${mediaPath}/pages`) {
        state.mediaJobs.push({ id: 'page-job', kind: 'page_evaluation', state: 'queued', error: null })
        return respond({ state: 'queued', pages: body.pages }, 202)
      }
      if (path === `${mediaPath}/evaluate`) {
        if (state.mediaActionFailure) return respond({ detail: 'Media action response unavailable.' }, 503)
        state.mediaJobs.push({ id: 'evaluate-job', kind: 'layout_evaluation', state: 'queued', error: null })
        return respond({ state: 'queued', operationId: 'evaluate-operation', jobId: 'evaluate-job' }, 202)
      }
    }
    if (path === `${projectPath}/layouts`) return respond([state.layout, ...state.extraLayouts])
    if (path === `${projectPath}/layouts/${layoutId}`) {
      expect(body.expectedUpdatedAt).toBe(state.layout.updatedAt)
      state.layout = { ...state.layout, ...body, updatedAt: bump() } as typeof state.layout
      state.project = { ...state.project, status: 'needs_review', updatedAt: bump() }
      return respond(state.layout)
    }
    if (path === documentPath) return respond(state.documents)
    if (path === `${documentPath}:upload-intent`) {
      state.intents++
      if (state.duplicateUpload) return respond({ errors: { code: 'DOCUMENT_ALREADY_EXISTS', detail: 'This file already exists in this project as "original.pdf" (status: extracting). Refresh documents to see its progress; uploading it again will not restart processing.' } }, 409)
      expect(body.sha256).toMatch(/^[a-f0-9]{64}$/)
      expect(body.declaredMediaType).toBe('application/pdf')
      return respond({ documentId, uploadAction: state.unsafeUpload ? 'https://untrusted.invalid/api/v1/upload' : `${documentPath}/${documentId}/content`, method: 'PUT', requiredHeaders: { 'Content-Type': 'application/pdf' }, maximumSize: 50 * 1024 * 1024 }, 201)
    }
    if (path === `${documentPath}/${documentId}/content`) {
      state.uploads++
      expect(request.headers()['content-type']).toBe('application/pdf')
      expect(request.headers()['content-disposition']).toBe('attachment; filename="evidence.pdf"')
      expect(request.headers()['x-csrftoken']).toBe('builder-test-csrf')
      expect(request.postDataBuffer()?.toString()).toBe('%PDF-1.4\nfixture evidence')
      return route.fulfill({ status: 204 })
    }
    if (path === `${documentPath}/${documentId}:complete`) {
      expect(body).toEqual({})
      state.completionKeys.push(request.headers()['idempotency-key'])
      if (state.failComplete) return respond({ detail: 'Queue temporarily unavailable.' }, 503)
      state.documents = [{ id: documentId, originalName: 'evidence.pdf', status: 'queued', scanStatus: 'pending', byteLength: 25 }]
      return respond({ status: 'queued' }, 202)
    }
    if (path === `/api/v1/developer-projects/${projectId}:submit-review`) {
      if (state.reviewFailure) return respond({ detail: 'Processing attestation and evidence are required.' }, 400)
      state.project = { ...state.project, status: 'needs_review', updatedAt: bump() }
      return respond({ status: 'needs_review' }, 202)
    }
    return respond({ detail: `Unexpected mocked route: ${method} ${path}` }, 500)
  })
  return state
}
async function openProject(page: Page, showLayouts = true) {
  await page.goto('/builder')
  await page.getByRole('button', { name: /Builder Gardens/ }).click()
  await expect(page.getByRole('heading', { name: 'Project details' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Project media workspace' })).toBeVisible()
  if (showLayouts) {
    await page.getByRole('tab', { name: 'Layouts', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Save layout' })).toBeVisible()
  } else {
    await expect(page.getByRole('button', { name: 'Preview Building exterior' })).toBeVisible()
  }
}
const evidence = { name: 'evidence.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\nfixture evidence') }

test('dedicated builder login does not bootstrap buyer data or expose admin navigation', async ({ page }) => {
  const state = await mockBuilder(page, false)
  await page.goto('/builder')
  await expect(page).toHaveURL(/\/builder\/login$/)
  await page.getByLabel('Username', { exact: true }).fill('fixture-builder')
  await page.getByLabel('Password', { exact: true }).fill('test-only-password')
  await page.getByRole('button', { name: 'Sign in to builder workspace' }).click()
  await expect(page.getByRole('heading', { name: 'Your projects' })).toBeVisible()
  expect(state.requests.some(item => item.path === '/api/v1/auth/builder-login' && item.method === 'POST' && item.headers['x-csrftoken'] === 'builder-test-csrf')).toBeTruthy()
  expect(state.requests.every(item => !item.path.includes('/admin') && !['/api/v1/auth/login', '/api/v1/projects/', '/api/v1/requirement-profiles/', '/api/v1/shortlists/'].includes(item.path))).toBeTruthy()
  await expect(page.locator('a[href*="/admin"]')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /publish/i })).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Your projects' })).toBeVisible()
})

test('nonbuilders and rejected logins cannot enter the workspace', async ({ page }) => {
  const state = await mockBuilder(page)
  state.builder = false
  await page.goto('/builder')
  await expect(page.getByRole('heading', { name: 'Builder access unavailable' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'New project' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Sign out', exact: true }).click()
  await page.getByLabel('Username', { exact: true }).fill('buyer')
  await page.getByLabel('Password', { exact: true }).fill('incorrect')
  await page.getByRole('button', { name: 'Sign in to builder workspace' }).click()
  await expect(page.getByRole('alert')).toContainText('active owner or editor membership')
  await expect(page.getByRole('heading', { name: 'Builder sign in' })).toBeVisible()
})

test('homepage login offers Buyer or Builder and reuses the session', async ({ page }) => {
  const state = await mockBuilder(page, false)
  await page.goto('/')
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Buyer', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: 'Builder', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Builder', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.getByLabel('Username', { exact: true }).fill('fixture-builder')
  await page.getByLabel('Password', { exact: true }).fill('test-password')
  await page.getByRole('button', { name: 'Sign in as Builder' }).click()
  await expect(page).toHaveURL(/\/builder$/)
  await expect(page.getByRole('heading', { name: 'Your projects' })).toBeVisible()
  expect(state.requests.filter(item => item.method === 'POST' && item.path.includes('/auth/')).map(item => item.path)).toEqual(['/api/v1/auth/builder-login'])
  await page.getByRole('link', { name: 'Switch workspace' }).click()
  await page.getByRole('button', { name: 'Continue as Buyer' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Switch workspace for fixture-builder' })).toBeVisible()
})

test('workspace selection does not grant builder access to buyers', async ({ page }) => {
  const state = await mockBuilder(page)
  state.builder = false
  await page.goto('/')
  await page.getByRole('button', { name: 'Switch workspace for fixture-builder' }).click()
  await expect(page.getByRole('dialog')).toContainText('Builder access needs an active owner or editor membership')
  await expect(page.getByRole('button', { name: 'Continue as Builder' })).toBeDisabled()
  await page.getByRole('button', { name: 'Continue as Buyer' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(state.requests.some(item => item.path === '/api/v1/builder/projects')).toBe(false)
})

test('workspace selection stays usable on mobile and retries failed access checks', async ({ page }) => {
  await mockBuilder(page)
  await page.setViewportSize({ width: 320, height: 740 })
  let failed = true
  await page.route('**/api/v1/builder/organizations', route => failed
    ? route.fulfill({ status: 503, contentType: 'application/json', body: '{}' })
    : route.fallback())
  await page.goto('/?workspace=choose')
  await expect(page.getByRole('button', { name: 'Retry access check' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Continue as Builder' })).toBeDisabled()
  failed = false
  await page.getByRole('button', { name: 'Retry access check' }).click()
  await expect(page.getByRole('button', { name: 'Continue as Builder' })).toBeEnabled()
  expect(await page.getByRole('dialog').evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true)
  await page.getByRole('button', { name: 'Close workspace selection' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

test('wrong builder password is distinct from missing membership', async ({ page }) => {
  const state = await mockBuilder(page, false)
  state.rejectLogin = true
  await page.goto('/builder/login')
  await page.getByLabel('Username', { exact: true }).fill('fixture-builder')
  await page.getByLabel('Password', { exact: true }).fill('incorrect')
  await page.getByRole('button', { name: 'Sign in to builder workspace' }).click()
  await expect(page.getByRole('alert')).toHaveText('Invalid credentials.')
  await expect(page.getByRole('heading', { name: 'Builder sign in' })).toBeVisible()
})

test('organization scope, create and edit preserve server-managed fields', async ({ page }) => {
  const state = await mockBuilder(page)
  state.projects = []
  await page.goto('/builder')
  await page.getByRole('button', { name: 'New project' }).click()
  await page.getByLabel('Project name', { exact: true }).fill('My New Project')
  await page.getByLabel('MahaRERA registration').fill('A12B345678')
  await page.getByLabel('Locality', { exact: true }).fill('Powai')
  await page.getByLabel('City', { exact: true }).fill('Mumbai')
  await page.getByLabel('I am authorized').check()
  await page.getByRole('button', { name: 'Create project', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Project details' })).toBeVisible()
  expect(state.project.mahaReraId).toBe('A12B345678')
  await page.getByLabel('Project name', { exact: true }).fill('Edited Project')
  await page.getByRole('button', { name: 'Save project changes' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Project saved' })).toBeVisible()
  expect(state.project.submittedName).toBe('Edited Project')
  await page.getByLabel('Active organization').selectOption(secondOrganizationId)
  await expect(page.getByRole('heading', { name: 'Select a project' })).toBeVisible()
  await expect(page.getByRole('button', { name: /Edited Project/ })).toHaveCount(0)
  await expect(page.getByText('No projects yet. Create your first project.')).toBeVisible()
})

test('conflicts preserve edits until explicit discard and reload', async ({ page }) => {
  const state = await mockBuilder(page)
  await openProject(page)
  state.conflict = true
  await page.getByLabel('Project name', { exact: true }).fill('My unsaved private edit')
  await page.getByRole('button', { name: 'Save project changes' }).click()
  await expect(page.getByRole('heading', { name: 'Changes were not overwritten' })).toBeVisible()
  await expect(page.getByLabel('Project name', { exact: true })).toHaveValue('My unsaved private edit')
  await expect(page.getByRole('button', { name: 'Save project changes' })).toBeDisabled()
  expect(state.requests.filter(item => item.path === projectPath && item.method === 'PATCH')).toHaveLength(1)
  await page.getByRole('button', { name: 'Reload latest version' }).click()
  await page.getByRole('button', { name: 'Discard entries and reload' }).click()
  await expect(page.getByLabel('Project name', { exact: true })).toHaveValue('Saved by another editor')
  await expect(page.getByRole('button', { name: 'Save project changes' })).toBeEnabled()
})

test('layout edits accept optional INR price and require review', async ({ page }) => {
  const state = await mockBuilder(page)
  await openProject(page)
  await page.getByLabel('Total price (INR)', { exact: true }).fill('16750000.50')
  await page.getByRole('button', { name: 'Save layout', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Edit layout name: Garden 2BHK' })).toBeVisible()
  await expect(page.getByText('Layout saved. Independent review is required.')).toBeVisible()
  expect(state.layout.price).toBe('16750000.50')
  await page.getByLabel('Total price (INR)', { exact: true }).fill('')
  await page.getByRole('button', { name: 'Save layout', exact: true }).click()
  await expect.poll(() => state.layout.price).toBeNull()
  await expect(page.getByRole('button', { name: /publish/i })).toHaveCount(0)
})

test('duplicate upload explains the existing document without offering a misleading retry', async ({ page }) => {
  const state = await mockBuilder(page)
  state.duplicateUpload = true
  await openProject(page)
  await page.getByLabel('Evidence file').setInputFiles(evidence)
  await page.getByRole('button', { name: 'Upload evidence', exact: true }).click()
  const alert = page.getByRole('alert').filter({ hasText: 'This file already exists' })
  await expect(alert).toContainText('original.pdf')
  await expect(alert).toContainText('extracting')
  await expect(alert).not.toContainText('may have reached the server')
  await expect(page.getByRole('button', { name: 'Retry upload request' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Upload evidence', exact: true })).toBeDisabled()
  expect(state.intents).toBe(1)
  expect(state.uploads).toBe(0)
  expect(state.completionKeys).toHaveLength(0)
  state.duplicateUpload = false
  await page.getByLabel('Evidence file').setInputFiles(evidence)
  await expect(page.getByRole('button', { name: 'Upload evidence', exact: true })).toBeEnabled()
})

test('document upload retries completion with the same key and never starts workers', async ({ page }) => {
  const state = await mockBuilder(page)
  state.failComplete = true
  await openProject(page)
  await page.getByLabel('Evidence file').setInputFiles(evidence)
  await page.getByRole('button', { name: 'Upload evidence', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'Queue temporarily unavailable' })).toBeVisible()
  state.failComplete = false
  await page.getByRole('button', { name: 'Retry same upload' }).click()
  await expect(page.getByText('Status: queued · Scan: pending', { exact: false })).toBeVisible()
  expect(state.intents).toBe(1)
  expect(state.uploads).toBe(1)
  expect(state.completionKeys).toHaveLength(2)
  expect(state.completionKeys[0]).toBeTruthy()
  expect(state.completionKeys[0]).toBe(state.completionKeys[1])
  expect(state.requests.some(item => /process_outbox|worker|model|extract/.test(item.path))).toBeFalsy()
})

test('layout completion preserves project edits entered while its request was in flight', async ({ page }) => {
  await mockBuilder(page)
  await openProject(page)
  let release: () => void = () => {}
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route(`**${projectPath}/layouts/${layoutId}`, async route => {
    await gate
    await route.fallback()
  })
  await page.getByLabel('Total price (INR)', { exact: true }).fill('17000000')
  const request = page.waitForRequest(request => request.method() === 'PATCH' && request.url().endsWith(`/layouts/${layoutId}`))
  await page.getByRole('button', { name: 'Save layout', exact: true }).click()
  await request
  await page.getByLabel('Project name', { exact: true }).fill('Keep these local project edits')
  release()
  await expect(page.getByRole('heading', { name: 'Changes were not overwritten' })).toBeVisible()
  await expect(page.getByLabel('Project name', { exact: true })).toHaveValue('Keep these local project edits')
  await expect(page.getByRole('button', { name: 'Save project changes' })).toBeDisabled()
})

test('upload refuses cross-origin actions before transferring private bytes', async ({ page }) => {
  const state = await mockBuilder(page)
  state.unsafeUpload = true
  const external: string[] = []
  page.on('request', request => { if (request.url().includes('untrusted.invalid')) external.push(request.url()) })
  await openProject(page)
  await page.getByLabel('Evidence file').setInputFiles(evidence)
  await page.getByRole('button', { name: 'Upload evidence', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'Invalid API resource' })).toBeVisible()
  expect(state.uploads).toBe(0)
  expect(external).toEqual([])
})

test('review validation is actionable and archive requires confirmation without deleting evidence', async ({ page }) => {
  const state = await mockBuilder(page)
  state.reviewFailure = true
  await openProject(page)
  await page.getByRole('button', { name: 'Submit for review' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'registry verification attempt' })).toBeVisible()
  await page.getByRole('button', { name: 'Archive project', exact: true }).click()
  await expect(page.getByRole('group', { name: 'Confirm archive' })).toContainText('does not delete documents or evidence')
  expect(state.requests.filter(item => item.path.endsWith(':archive'))).toHaveLength(0)
  await page.getByRole('button', { name: 'Confirm archive', exact: true }).click()
  await expect(page.getByText('Project archived. Evidence has been retained.')).toBeVisible()
  await expect(page.getByLabel('Project name', { exact: true })).toBeDisabled()
  await expect(page.getByLabel('Evidence file')).toBeDisabled()
  expect(state.requests.some(item => item.method === 'DELETE')).toBeFalsy()
})

test('session expiration clears all private forms and redirects to dedicated login', async ({ page }) => {
  const state = await mockBuilder(page)
  await openProject(page)
  await page.getByLabel('Project name', { exact: true }).fill('Sensitive unsaved builder details')
  state.expirePatch = true
  await page.getByRole('button', { name: 'Save project changes' }).click()
  await expect(page).toHaveURL(/\/builder\/login$/)
  await expect(page.getByRole('heading', { name: 'Builder sign in' })).toBeVisible()
  await expect(page.getByLabel('Project name', { exact: true })).toHaveCount(0)
  expect(await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))).not.toContain('Sensitive unsaved')
})

test('logout resets project selection and upload state before the next login', async ({ page }) => {
  await mockBuilder(page)
  await openProject(page)
  await page.getByLabel('Evidence file').setInputFiles(evidence)
  await page.getByRole('button', { name: 'Sign out', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Builder sign in' })).toBeVisible()
  await expect(page.getByLabel('Evidence file')).toHaveCount(0)
  await page.getByLabel('Username', { exact: true }).fill('fixture-builder')
  await page.getByLabel('Password', { exact: true }).fill('test-only-password')
  await page.getByRole('button', { name: 'Sign in to builder workspace' }).click()
  await expect(page.getByRole('heading', { name: 'Select a project' })).toBeVisible()
  await expect(page.getByLabel('Project name', { exact: true })).toHaveCount(0)
})

for (const width of [320, 390]) {
  test(`builder forms remain usable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    await mockBuilder(page)
    await openProject(page)
    await expect(page.getByLabel('Project name', { exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
    await page.getByLabel('Project name', { exact: true }).fill('Mobile project')
    await page.getByRole('button', { name: 'Save project changes' }).click()
    await expect(page.getByText('Project saved. Changes require independent review before publication.')).toBeVisible()
  })
}

test('builder media tabs, previews and carousel expose review data but no admin or publication controls', async ({ page }) => {
  const state = await mockBuilder(page)
  await openProject(page, false)
  await expect(page.getByRole('tab')).toHaveText(['Project images', 'Amenities', 'Floor plans', 'Layouts'])
  await expect(page.getByRole('tab', { name: 'Project images' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('.builder-media-preview > img')).toHaveAttribute('alt', 'Building exterior')
  await page.getByRole('button', { name: 'Next image', exact: true }).click()
  await expect(page.locator('.builder-media-preview > img')).toHaveAttribute('alt', 'Entrance view')
  await page.getByRole('button', { name: 'Previous image', exact: true }).click()
  await expect(page.locator('.builder-media-preview > img')).toHaveAttribute('alt', 'Building exterior')
  await page.getByRole('tab', { name: 'Amenities', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Preview Pool' })).toBeVisible()
  await page.getByRole('tab', { name: 'Amenities', exact: true }).press('ArrowRight')
  await expect(page.getByRole('tab', { name: 'Floor plans' })).toBeFocused()
  await expect(page.getByRole('button', { name: 'Preview First floor' })).toBeVisible()
  await page.getByRole('tab', { name: 'Layouts', exact: true }).click()
  const summary = page.getByRole('table', { name: 'Layout review summary' })
  await expect(summary).toContainText('Garden 2BHK')
  await expect(summary).toContainText('15000000.00')
  await expect(summary).toContainText('740')
  await expect(summary).toContainText('pending review')
  await expect(page.locator('a[href*="/admin"]')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /publish|approve/i })).toHaveCount(0)
  expect(state.requests.filter(item => item.method === 'POST')).toEqual([])
})

test('builder media images open full-size in a separate tab without changing edits or draft selection', async ({ page }) => {
  const state = await mockBuilder(page)
  state.mediaDrafts = [{ id: draftId, label: 'Draft layout', imageUrl: `${mediaPath}/drafts/${draftId}` }]
  await page.context().route(/\/media\/(?:assets|drafts)\/[^/?]+$/, route => route.fulfill({ contentType: 'image/png', body: state.imageBytes }))
  await openProject(page, false)
  for (const [category, label] of [['Project images', 'Building exterior'], ['Amenities', 'Pool'], ['Floor plans', 'First floor'], ['Layouts', 'Garden 2BHK']]) {
    await page.getByRole('tab', { name: category, exact: true }).click()
    const link = page.getByRole('link', { name: `Open ${label} image in new tab`, exact: true })
    await expect(link).toHaveAttribute('href', state.mediaImageUrl)
    await expect(link).toHaveAttribute('target', '_blank')
    await expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  }
  await page.getByRole('button', { name: 'Edit layout name: Garden 2BHK' }).click()
  await page.getByRole('textbox', { name: 'Selected layout name', exact: true }).fill('Keep my unsaved alias')
  const openLayout = page.getByRole('link', { name: 'Open Garden 2BHK image in new tab', exact: true })
  await openLayout.focus()
  const popupPromise = page.waitForEvent('popup')
  await openLayout.press('Enter')
  const popup = await popupPromise
  await expect(popup).toHaveURL(new RegExp(`${mediaPath}/assets/${assetId}$`))
  await expect(popup.locator('img')).toHaveJSProperty('naturalWidth', 400)
  expect(await popup.evaluate(() => window.opener)).toBeNull()
  await popup.close()
  await expect(page.getByRole('textbox', { name: 'Selected layout name', exact: true })).toHaveValue('Keep my unsaved alias')
  const draftPopupPromise = page.waitForEvent('popup')
  await page.getByRole('link', { name: 'Open Draft layout image in new tab', exact: true }).click()
  const draftPopup = await draftPopupPromise
  await expect(draftPopup).toHaveURL(new RegExp(`${mediaPath}/drafts/${draftId}$`))
  await expect(draftPopup.locator('img')).toHaveJSProperty('naturalWidth', 400)
  await draftPopup.close()
  await expect(page.getByRole('checkbox', { name: 'Draft layout', exact: true })).not.toBeChecked()
  expect(state.requests.filter(item => item.method === 'POST')).toEqual([])
})

for (const invalid of ['external', 'unavailable']) {
  test(`builder does not offer an open link for ${invalid} media images`, async ({ page }) => {
    const state = await mockBuilder(page)
    if (invalid === 'external') state.mediaImageUrl = 'https://invalid.example/image.png'
    else await page.route(`**${mediaPath}/assets/*`, route => route.fulfill({ status: 403, contentType: 'text/plain', body: 'Access denied' }))
    const externalRequests: string[] = []
    page.on('request', request => { if (request.url().startsWith('https://invalid.example')) externalRequests.push(request.url()) })
    await openProject(page, false)
    const preview = page.locator('.builder-media-preview')
    await expect(preview).toContainText('Preview unavailable')
    await expect(preview.getByRole('link', { name: /image in new tab/ })).toHaveCount(0)
    expect(externalRequests).toEqual([])
  })
}

test('layout directory shows selected room measurements and preserves edits while searching and sorting', async ({ page }) => {
  const state = await mockBuilder(page)
  const otherId = 'f0000000-0000-4000-8000-000000000003'
  state.extraLayouts.push({ ...initialLayout, id: otherId, name: 'Compact 1BHK', price: null })
  state.evaluations[otherId] = { state: 'completed', label: 'Extraction completed, but no readable room dimensions were found', canRun: true }
  await openProject(page)
  const directory = page.getByRole('complementary', { name: 'Layout directory' })
  const detail = page.getByRole('region', { name: 'Selected layout evaluation' })
  await expect(directory.getByRole('button', { name: /Garden 2BHK/ })).toHaveAttribute('aria-pressed', 'true')
  const table = detail.getByRole('table', { name: 'Room measurements for Garden 2BHK' })
  await expect(table).toContainText('Living room')
  await expect(table).toContainText('12.5 ft')
  await expect(table).toContainText('10 ft')
  await expect(table).toContainText('125 sq ft')
  await expect(table.getByRole('row', { name: /Kitchen/ })).toContainText('Unknown')
  await expect(detail).toContainText('not carpet area')
  const sidebarBox = await directory.boundingBox(), detailBox = await detail.boundingBox()
  expect(sidebarBox).not.toBeNull()
  expect(detailBox).not.toBeNull()
  if (!sidebarBox || !detailBox) throw new Error('Layout explorer did not render')
  expect(sidebarBox.x + sidebarBox.width).toBeLessThan(detailBox.x)
  await page.getByRole('textbox', { name: 'Total price (INR)', exact: true }).fill('18000000')
  await directory.getByRole('button', { name: /Compact 1BHK/ }).click()
  await expect(page.getByRole('form', { name: 'Edit layout Compact 1BHK' })).toBeVisible()
  await expect(detail.getByRole('table')).toHaveCount(0)
  await expect(detail).toContainText('no readable room dimensions')
  await directory.getByRole('button', { name: /Garden 2BHK/ }).click()
  await expect(page.getByRole('textbox', { name: 'Total price (INR)', exact: true })).toHaveValue('18000000')
  await page.getByRole('combobox', { name: 'Sort layouts', exact: true }).selectOption('name')
  await expect(directory.getByRole('button').first()).toContainText('Compact 1BHK')
  await page.getByLabel('Find a layout', { exact: true }).fill('nothing matches')
  await expect(directory).toContainText('No matching layouts.')
  await expect(detail).toHaveCount(0)
  await page.getByLabel('Find a layout', { exact: true }).fill('garden')
  await expect(page.getByRole('textbox', { name: 'Total price (INR)', exact: true })).toHaveValue('18000000')
  expect(state.requests.filter(item => item.method === 'POST')).toEqual([])
  await page.setViewportSize({ width: 390, height: 844 })
  const mobileSidebar = await directory.boundingBox(), mobileDetail = await detail.boundingBox()
  if (!mobileSidebar || !mobileDetail) throw new Error('Mobile layout explorer did not render')
  expect(mobileDetail.y).toBeGreaterThan(mobileSidebar.y + mobileSidebar.height)
  expect(mobileDetail.x + mobileDetail.width).toBeLessThanOrEqual(391)
})

for (const [stateName, label, text, button, runnable] of [
  ['queued', 'Queued - extraction has not run yet', 'pending extraction', 'Extraction queued', false],
  ['running', 'Extracting - refresh to see results', 'pending extraction', 'Extracting...', false],
  ['failed', 'Extraction failed - inspect the error or retry', 'evaluation did not complete', 'Requeue failed extraction', true],
  ['not_queued', 'No page/crop extraction job found', 'Queue extraction to evaluate', 'Queue extraction', true],
] as const) {
  test(`selected layout explains ${stateName} extraction state without starting work on read`, async ({ page }) => {
    const state = await mockBuilder(page)
    state.rooms[layoutId] = []
    state.evaluations[layoutId] = { state: stateName, label, canRun: stateName !== 'running',
      ...(stateName === 'failed' ? { error: 'MANUAL_CROP_NOT_SINGLE_LAYOUT' } : {}) }
    await openProject(page)
    const detail = page.getByRole('region', { name: 'Selected layout evaluation' })
    await expect(detail).toContainText(label)
    await expect(detail).toContainText(text)
    const evaluate = page.getByRole('button', { name: button, exact: true })
    if (runnable) await expect(evaluate).toBeEnabled()
    else await expect(evaluate).toBeDisabled()
    if (stateName === 'failed') await expect(detail).toContainText('Update its boundary to one layout')
    expect(state.requests.filter(item => item.method === 'POST')).toEqual([])
    if (runnable) {
      await evaluate.click()
      await page.getByRole('button', { name: 'Confirm and queue evaluation' }).click()
      await expect(page.getByText('Layout re-evaluation queued. Refresh jobs to see progress.')).toBeVisible()
      expect(state.requests.find(item => item.path.endsWith('/evaluate'))?.body.planId).toBe(layoutId)
    }
  })
}

test('refresh loads completed measurements for the selected layout without losing its selection', async ({ page }) => {
  const state = await mockBuilder(page)
  state.rooms[layoutId] = []
  state.evaluations[layoutId] = { state: 'queued', label: 'Queued - extraction has not run yet', canRun: true }
  await openProject(page)
  await expect(page.getByRole('region', { name: 'Selected layout evaluation' })).toContainText('pending extraction')
  state.rooms[layoutId] = [{ room: 'Bedroom', lengthFt: 11, widthFt: 10, areaSqFt: 110 }]
  state.evaluations[layoutId] = { state: 'completed', label: 'Extraction completed - awaiting review', canRun: true }
  await page.getByRole('button', { name: 'Refresh media and jobs' }).click()
  await expect(page.getByRole('table', { name: 'Room measurements for Garden 2BHK' })).toContainText('110 sq ft')
  await expect(page.getByRole('region', { name: 'Selected layout evaluation' })).toContainText('Calculated room area: 110 sq ft')
  await expect(page.getByRole('complementary', { name: 'Layout directory' }).getByRole('button', { name: /Garden 2BHK/ })).toHaveAttribute('aria-pressed', 'true')
  expect(state.requests.filter(item => item.method === 'POST')).toEqual([])
})

test('crop saves normalized draft without AI then attaches only after explicit cost confirmation', async ({ page }) => {
  const state = await mockBuilder(page)
  await openProject(page, false)
  await page.getByRole('button', { name: 'Crop this image' }).click()
  const sourceLink = page.getByRole('link', { name: 'Open original source page in new tab', exact: true })
  await expect(sourceLink).toHaveAttribute('href', `${mediaPath}/documents/${documentId}/pages/1?sha256=${sourceSha256}`)
  await expect(sourceLink).toHaveAttribute('target', '_blank')
  await expect(sourceLink).toHaveAttribute('rel', 'noopener noreferrer')
  await expect(page.getByLabel('Source document')).toHaveValue(documentId)
  await expect(page.getByLabel('Source page')).toHaveValue('1')
  await expect(page.getByRole('button', { name: 'Save crop draft' })).toBeEnabled()
  await page.getByLabel('Crop label').fill('Missing garden layout')
  await page.getByLabel('X (%)', { exact: true }).fill('10')
  await page.getByLabel('Y (%)', { exact: true }).fill('20')
  await page.getByLabel('WIDTH (%)', { exact: true }).fill('60')
  await page.getByLabel('HEIGHT (%)', { exact: true }).fill('50')
  expect(state.requests.filter(item => item.path.startsWith(mediaPath) && item.method === 'POST')).toEqual([])
  await page.getByRole('button', { name: 'Save crop draft' }).click()
  await expect(page.getByText('Crop draft saved. No AI evaluation was started.')).toBeVisible()
  const cropRequests = state.requests.filter(item => item.path === `${mediaPath}/crops`)
  expect(cropRequests).toHaveLength(1)
  expect(cropRequests[0].body).toEqual({
    documentId, pageNumber: 1, sourceSha256, category: 'project-images', rotationDegrees: 0, requestId: expect.stringMatching(/^[a-f0-9-]{36}$/),
    regions: [{ label: 'Missing garden layout', x: 0.1, y: 0.2, width: 0.6, height: 0.5 }],
  })
  expect(cropRequests[0].headers['x-csrftoken']).toBe('builder-test-csrf')
  expect(state.mediaJobs).toEqual([])
  await page.getByRole('checkbox', { name: /Missing garden layout/ }).check()
  await page.getByLabel('Evaluation profile').selectOption(profileId)
  await page.getByRole('button', { name: 'Attach selected drafts' }).click()
  await expect(page.getByRole('group', { name: 'Confirm media action' })).toContainText('may incur model costs')
  expect(state.requests.filter(item => item.path === `${mediaPath}/drafts` && item.method === 'POST')).toHaveLength(0)
  await page.getByRole('button', { name: 'Confirm and queue evaluation' }).click()
  await expect(page.getByText('Selected drafts attached; evaluation queued.')).toBeVisible()
  const attach = state.requests.filter(item => item.path === `${mediaPath}/drafts` && item.method === 'POST')
  expect(attach).toHaveLength(1)
  expect(attach[0].body).toEqual({ action: 'attach', draftIds: [draftId], visionProfileId: profileId, confirm: true, requestId: expect.stringMatching(/^[a-f0-9-]{36}$/) })
  await expect(page.getByRole('region', { name: 'Media processing jobs' })).toContainText('queued')
  expect(state.requests.some(item => item.path.endsWith('/evaluate'))).toBe(false)
})

test('manual multi-layout crops retain consecutive drags without Add or labels and attach through the standard queue', async ({ page }) => {
  const state = await mockBuilder(page)
  await openProject(page, false)
  await page.getByRole('tab', { name: 'Floor plans', exact: true }).click()
  await page.getByRole('button', { name: 'Manually crop layouts', exact: true }).click()
  await expect(page.getByLabel('Crop category')).toHaveValue('layouts')
  await expect(page.getByLabel('Crop category')).toBeDisabled()
  await expect(page.getByLabel('Source page')).toHaveValue('1')
  await expect(page.getByRole('button', { name: 'Add layout crop', exact: true })).toBeEnabled()
  const sourceImage = page.getByRole('img', { name: 'Original source page 1' })
  async function drawRectangle(x: number) {
    await sourceImage.scrollIntoViewIfNeeded()
    const image = await sourceImage.boundingBox()
    if (!image) throw new Error('Source page preview was not rendered')
    await page.mouse.move(image.x + image.width * x, image.y + image.height * 0.1)
    await page.mouse.down()
    await page.mouse.move(image.x + image.width * (x + 0.4), image.y + image.height * 0.5, { steps: 4 })
    await page.mouse.up()
  }
  await drawRectangle(0.05)
  await expect(page.getByLabel('Crop label', { exact: true })).toHaveValue('Layout 1')
  await drawRectangle(0.5)
  await expect(page.getByLabel('Crop label', { exact: true })).toHaveValue('Layout 2')
  await expect(page.getByRole('list', { name: 'Unsaved layout crops' })).toContainText('Layout 1')
  await expect(page.locator('.builder-crop-added')).toHaveCount(1)
  await expect(page.locator('.builder-crop-region')).toHaveCount(2)
  await sourceImage.click({ position: { x: 10, y: 10 } })
  await expect(page.locator('.builder-crop-region')).toHaveCount(2)
  await expect(page.getByLabel('Crop label', { exact: true })).toHaveValue('Layout 2')
  const image = await sourceImage.boundingBox()
  if (!image) throw new Error('Source page preview was not rendered')
  await page.mouse.move(image.x + 20, image.y + 20)
  await page.mouse.down()
  await page.mouse.move(image.x + 80, image.y + 80, { steps: 4 })
  await page.locator('.builder-crop-canvas').dispatchEvent('pointercancel')
  await page.mouse.up()
  await expect(page.locator('.builder-crop-region')).toHaveCount(2)
  await expect(page.getByLabel('Crop label', { exact: true })).toHaveValue('Layout 2')
  await page.getByLabel('Layout crop 1 label', { exact: true }).fill('Layout A')
  await page.getByLabel('Crop label', { exact: true }).fill('Layout B')
  expect(state.requests.filter(item => item.method === 'POST')).toHaveLength(0)
  await page.getByRole('button', { name: 'Save layout drafts', exact: true }).click()
  await expect(page.getByText('2 layout draft(s) saved. Select them below and attach to queue evaluation.')).toBeVisible()
  const crops = state.requests.filter(item => item.path === `${mediaPath}/crops`)
  expect(crops).toHaveLength(1)
  expect(crops[0].body).toMatchObject({ documentId, pageNumber: 1, sourceSha256, category: 'layouts', rotationDegrees: 0 })
  expect(crops[0].body).not.toHaveProperty('targetPlanId')
  const regions = crops[0].body.regions as { label: string; x: number; y: number; width: number; height: number }[]
  expect(regions).toHaveLength(2)
  expect(regions[0].label).toBe('Layout A')
  expect(regions[0].x).toBeCloseTo(0.05, 2)
  expect(regions[0].width).toBeCloseTo(0.4, 2)
  expect(regions[1].label).toBe('Layout B')
  expect(regions[1].x).toBeCloseTo(0.5, 2)
  expect(regions[1].y).toBeCloseTo(0.1, 2)
  expect(regions[1].width).toBeCloseTo(0.4, 2)
  expect(regions[1].height).toBeCloseTo(0.4, 2)
  expect(state.mediaJobs).toEqual([])
  const ids = state.mediaDrafts.map(draft => draft.id)
  await page.getByRole('checkbox', { name: /Layout A/ }).check()
  await page.getByRole('checkbox', { name: /Layout B/ }).check()
  await page.getByRole('button', { name: 'Attach selected drafts', exact: true }).click()
  await expect(page.getByRole('group', { name: 'Confirm media action' })).toContainText('may incur model costs')
  await page.getByRole('button', { name: 'Confirm and queue evaluation' }).click()
  await expect(page.getByText('Selected drafts attached; evaluation queued.')).toBeVisible()
  expect(state.requests.find(item => item.path === `${mediaPath}/drafts`)?.body).toMatchObject({ action: 'attach', draftIds: ids, confirm: true })
  expect(state.requests.some(item => item.path === `${mediaPath}/actions`)).toBe(false)
})

test('manual multi-layout crops retain all rectangles and request ID after a failed save', async ({ page }) => {
  const state = await mockBuilder(page)
  state.cropFailure = true
  await openProject(page, false)
  await page.getByRole('tab', { name: 'Floor plans', exact: true }).click()
  await page.getByRole('button', { name: 'Manually crop layouts', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Add layout crop', exact: true })).toBeEnabled()
  for (const label of ['Layout A', 'Layout B']) {
    await page.getByLabel('Crop label').fill(label)
    await page.getByLabel('X (%)', { exact: true }).fill(label === 'Layout A' ? '5' : '55')
    await page.getByLabel('WIDTH (%)', { exact: true }).fill('40')
    await page.getByRole('button', { name: 'Add layout crop', exact: true }).click()
  }
  await page.getByRole('button', { name: 'Save layout drafts', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'No automatic retry' })).toBeVisible()
  await expect(page.getByRole('list', { name: 'Unsaved layout crops' }).getByRole('listitem')).toHaveCount(2)
  await page.getByRole('button', { name: 'Refresh media and jobs' }).click()
  await expect(page.getByRole('button', { name: 'Save layout drafts', exact: true })).toBeEnabled()
  expect(state.requests.filter(item => item.path === `${mediaPath}/crops`)).toHaveLength(1)
  state.cropFailure = false
  await page.getByRole('button', { name: 'Save layout drafts', exact: true }).click()
  await expect(page.getByText('2 layout draft(s) saved. Select them below and attach to queue evaluation.')).toBeVisible()
  const attempts = state.requests.filter(item => item.path === `${mediaPath}/crops`)
  expect(attempts).toHaveLength(2)
  expect(attempts[1].body).toEqual(attempts[0].body)
})

test('manual multi-layout crops protect orientation and source changes and enforce the 20-crop limit', async ({ page }) => {
  const state = await mockBuilder(page)
  await openProject(page, false)
  await page.getByRole('tab', { name: 'Floor plans', exact: true }).click()
  await page.getByRole('button', { name: 'Manually crop layouts', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Add layout crop', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'Save layout drafts', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'at least one' })).toBeVisible()
  for (let index = 1; index <= 20; index++) {
    await page.getByLabel('Crop label').fill(`Layout ${index}`)
    await page.getByLabel('X (%)', { exact: true }).fill(String(((index - 1) % 5) * 20))
    await page.getByLabel('Y (%)', { exact: true }).fill(String(Math.floor((index - 1) / 5) * 25))
    await page.getByLabel('WIDTH (%)', { exact: true }).fill('15')
    await page.getByLabel('HEIGHT (%)', { exact: true }).fill('15')
    await page.getByRole('button', { name: 'Add layout crop', exact: true }).click()
  }
  await expect(page.getByRole('button', { name: 'Add layout crop', exact: true })).toBeDisabled()
  await expect(page.getByLabel('Source rotation')).toBeDisabled()
  page.once('dialog', dialog => dialog.dismiss())
  await page.getByLabel('Source page').selectOption('2')
  await expect(page.getByLabel('Source page')).toHaveValue('1')
  await expect(page.getByRole('list', { name: 'Unsaved layout crops' }).getByRole('listitem')).toHaveCount(20)
  await page.getByLabel('Crop label').fill('Layout 21')
  await page.getByLabel('WIDTH (%)', { exact: true }).fill('60')
  await page.getByRole('button', { name: 'Save layout drafts', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'at most 20' })).toBeVisible()
  expect(state.requests.filter(item => item.method === 'POST')).toHaveLength(0)
  await page.getByRole('button', { name: 'Clear current rectangle' }).click()
  await page.getByRole('button', { name: 'Remove crop 1: Layout 1', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Add layout crop', exact: true })).toBeEnabled()
  await page.getByLabel('Crop label').fill('Replacement layout')
  await page.getByLabel('WIDTH (%)', { exact: true }).fill('15')
  await page.getByLabel('HEIGHT (%)', { exact: true }).fill('15')
  await page.getByRole('button', { name: 'Save layout drafts', exact: true }).click()
  await expect(page.getByText('20 layout draft(s) saved. Select them below and attach to queue evaluation.')).toBeVisible()
  const crops = state.requests.filter(item => item.path === `${mediaPath}/crops`)
  expect(crops).toHaveLength(1)
  expect(crops[0].body.regions).toHaveLength(20)
})

test('crop save accepts shared-wall overlap and shows size and server rejections beside the button', async ({ page }) => {
  const state = await mockBuilder(page)
  await openProject(page, false)
  await page.getByRole('tab', { name: 'Floor plans', exact: true }).click()
  await page.getByRole('button', { name: 'Manually crop layouts', exact: true }).click()
  const editor = page.getByRole('region', { name: 'Crop source editor' })
  const save = editor.getByRole('button', { name: 'Save layout drafts', exact: true })
  await expect(save).toBeEnabled()
  await page.getByLabel('Crop label').fill('Small layout')
  await page.getByLabel('WIDTH (%)', { exact: true }).fill('5')
  await page.getByLabel('HEIGHT (%)', { exact: true }).fill('5')
  await save.click()
  await expect(editor.getByRole('alert')).toContainText('"Small layout" covers 0.25%')
  await expect(editor.getByRole('alert')).toBeInViewport()
  await page.getByLabel('WIDTH (%)', { exact: true }).fill('90')
  await page.getByLabel('HEIGHT (%)', { exact: true }).fill('90')
  await save.click()
  await expect(editor.getByRole('alert')).toContainText('between 1% and 70%')
  await page.getByLabel('WIDTH (%)', { exact: true }).fill('40')
  await page.getByLabel('HEIGHT (%)', { exact: true }).fill('40')
  await page.getByRole('button', { name: 'Add layout crop', exact: true }).click()
  await page.getByLabel('Crop label').fill('Adjacent layout')
  await page.getByLabel('X (%)', { exact: true }).fill('5')
  await page.getByLabel('WIDTH (%)', { exact: true }).fill('40')
  await page.getByLabel('HEIGHT (%)', { exact: true }).fill('40')
  expect(state.requests.filter(item => item.path === `${mediaPath}/crops`)).toHaveLength(0)
  state.cropValidationFailure = true
  await save.click()
  await expect(editor.getByRole('alert')).toContainText('Select a crop with visible pixels.')
  await expect(editor.getByRole('alert')).toBeInViewport()
  await expect(page.getByLabel('Crop label')).toHaveValue('Adjacent layout')
  await expect(page.getByLabel('Layout crop 1 label')).toHaveValue('Small layout')
  await expect(page.getByRole('region', { name: 'Crop drafts' })).toContainText('No crop drafts.')
  expect(state.mediaDrafts).toEqual([])
  state.cropValidationFailure = false
  await save.click()
  await expect(page.getByText('2 layout draft(s) saved. Select them below and attach to queue evaluation.')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Crop drafts' }).getByRole('checkbox')).toHaveCount(2)
  expect(state.requests.filter(item => item.path === `${mediaPath}/crops`)[1].body.regions).toEqual([
    { label: 'Small layout', x: 0, y: 0, width: .4, height: .4 },
    { label: 'Adjacent layout', x: .05, y: 0, width: .4, height: .4 },
  ])
})

test('source-page crop validates bounds and preserves failed drafts without automatic retries', async ({ page }) => {
  const state = await mockBuilder(page)
  await openProject(page, false)
  await page.getByRole('button', { name: 'Crop source page', exact: true }).click()
  await page.getByLabel('Source page').selectOption('2')
  await expect(page.getByRole('img', { name: 'Original source page 2' })).toHaveAttribute('src', `${mediaPath}/documents/${documentId}/pages/2?sha256=${sourceSha256}`)
  await expect(page.getByRole('button', { name: 'Save crop draft' })).toBeEnabled()
  await page.getByLabel('Crop label').fill('Keep this crop')
  await page.getByLabel('X (%)', { exact: true }).fill('75')
  await page.getByRole('button', { name: 'Save crop draft' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'positive bounds' })).toBeVisible()
  expect(state.requests.filter(item => item.path.endsWith('/crops'))).toHaveLength(0)
  await page.getByLabel('WIDTH (%)', { exact: true }).fill('20')
  state.mediaConflict = true
  await page.getByRole('button', { name: 'Save crop draft' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'No automatic retry' })).toBeVisible()
  await expect(page.getByLabel('Crop label')).toHaveValue('Keep this crop')
  await expect(page.getByRole('button', { name: 'Save crop draft' })).toBeDisabled()
  expect(state.requests.filter(item => item.path.endsWith('/crops'))).toHaveLength(1)
  state.mediaConflict = false
  await page.getByRole('button', { name: 'Refresh media and jobs' }).click()
  await expect(page.getByLabel('Crop label')).toHaveValue('Keep this crop')
  expect(state.requests.filter(item => item.path.endsWith('/crops'))).toHaveLength(1)
})

test('pointer cropping and percentage inputs describe the same source-page rectangle', async ({ page }) => {
  const state = await mockBuilder(page)
  await openProject(page, false)
  await page.getByRole('button', { name: 'Crop source page', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Save crop draft' })).toBeEnabled()
  const image = page.getByRole('img', { name: 'Original source page 1' })
  await image.scrollIntoViewIfNeeded()
  const box = await image.boundingBox()
  expect(box).not.toBeNull()
  await page.mouse.move(box!.x + box!.width * 0.1, box!.y + box!.height * 0.1)
  await page.mouse.down()
  await page.mouse.move(box!.x + box!.width * 0.5, box!.y + box!.height * 0.5, { steps: 4 })
  await page.mouse.up()
  await expect.poll(async () => Number(await page.getByLabel('X (%)', { exact: true }).inputValue())).toBeCloseTo(10, 0)
  await expect.poll(async () => Number(await page.getByLabel('Y (%)', { exact: true }).inputValue())).toBeCloseTo(10, 0)
  await expect.poll(async () => Number(await page.getByLabel('WIDTH (%)', { exact: true }).inputValue())).toBeCloseTo(40, 0)
  await expect.poll(async () => Number(await page.getByLabel('HEIGHT (%)', { exact: true }).inputValue())).toBeCloseTo(40, 0)
  expect(state.requests.filter(item => item.method === 'POST')).toEqual([])
})

test('crop request IDs survive explicit retries and refresh but change when crop fields change', async ({ page }) => {
  const state = await mockBuilder(page)
  state.cropFailure = true
  await openProject(page, false)
  await page.getByRole('button', { name: 'Crop source page', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Save crop draft' })).toBeEnabled()
  await page.getByLabel('Crop label').fill('Replay-safe crop')
  await page.getByLabel('WIDTH (%)', { exact: true }).fill('50')
  await page.getByRole('button', { name: 'Save crop draft' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'Crop response temporarily unavailable' })).toBeVisible()
  const requests = () => state.requests.filter(item => item.path === `${mediaPath}/crops`)
  expect(requests()).toHaveLength(1)
  const firstId = requests()[0].body.requestId
  await page.getByRole('button', { name: 'Refresh media and jobs' }).click()
  await expect(page.getByText(/Media and jobs refreshed/)).toBeVisible()
  expect(requests()).toHaveLength(1)
  await page.getByRole('button', { name: 'Save crop draft' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'Crop response temporarily unavailable' })).toBeVisible()
  expect(requests()).toHaveLength(2)
  expect(requests()[1].body.requestId).toBe(firstId)
  await page.getByLabel('Crop label').fill('Changed crop')
  await page.getByRole('button', { name: 'Save crop draft' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'Crop response temporarily unavailable' })).toBeVisible()
  expect(requests()).toHaveLength(3)
  expect(requests()[2].body.requestId).not.toBe(firstId)
  state.cropFailure = false
  await page.getByRole('button', { name: 'Save crop draft' }).click()
  await expect(page.getByText('Crop draft saved. No AI evaluation was started.')).toBeVisible()
  expect(requests()).toHaveLength(4)
  expect(requests()[3].body.requestId).toBe(requests()[2].body.requestId)
})

test('missing or corrupt sources show server errors and cannot be selected for cropping', async ({ page }) => {
  const state = await mockBuilder(page)
  state.mediaDocuments[0] = { ...state.mediaDocuments[0], pageCount: 0, error: 'The original PDF is missing or corrupt.' }
  await openProject(page, false)
  await expect(page.getByRole('alert').filter({ hasText: 'evidence.pdf: The original PDF is missing or corrupt.' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Crop source page', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Crop this image' })).toHaveCount(0)
  const readableId = 'a1000000-0000-4000-8000-000000000002'
  state.mediaDocuments.push({ id: readableId, name: 'readable.pdf', pageCount: 1, sha256: sourceSha256 })
  await page.getByRole('button', { name: 'Refresh media and jobs' }).click()
  await expect(page.getByRole('button', { name: 'Crop source page', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'Crop source page', exact: true }).click()
  await expect(page.getByLabel('Source document')).toHaveValue(readableId)
  await expect(page.getByLabel('Source document').locator(`option[value="${documentId}"]`)).toHaveJSProperty('disabled', true)
  await expect(page.getByRole('button', { name: 'Save crop draft' })).toBeEnabled()
  await page.getByLabel('Crop label').fill('Preserve on source failure')
  state.mediaDocuments[1] = { ...state.mediaDocuments[1], pageCount: 0, error: 'Source is no longer readable.' }
  await page.getByRole('button', { name: 'Refresh media and jobs' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'readable.pdf: Source is no longer readable.' })).toBeVisible()
  await expect(page.getByLabel('Crop label')).toHaveValue('Preserve on source failure')
  await expect(page.getByRole('button', { name: 'Save crop draft' })).toBeDisabled()
  await expect(page.getByRole('img', { name: 'Original source page 1' })).toHaveCount(0)
  expect(state.requests.filter(item => item.path.includes(`/documents/${documentId}/pages/`))).toHaveLength(0)
  expect(state.requests.filter(item => item.method === 'POST')).toHaveLength(0)
})

for (const category of ['Floor plans', 'Layouts']) for (const confirmNameChanges of [false, true]) {
  test(`builders rename ${category} directly beneath the preview with confirmation ${confirmNameChanges}`, async ({ page }) => {
    const state = await mockBuilder(page)
    state.confirmNameChanges = confirmNameChanges
    await openProject(page, false)
    await page.getByRole('tab', { name: category, exact: true }).click()
    const title = page.getByRole('button', { name: `Edit ${category === 'Floor plans' ? 'floor plan name: First floor' : 'layout name: Garden 2BHK'}` })
    const name = page.getByLabel(category === 'Floor plans' ? 'Floor plan name' : 'Selected layout name', { exact: true })
    await expect(name).toHaveCount(0)
    await title.click()
    await expect(name).toBeFocused()
    await name.fill('Discard this alias')
    await name.press('Escape')
    await expect(title).toBeFocused()
    await expect(name).toHaveCount(0)
    await title.press('Enter')
    await name.fill('   ')
    await page.getByRole('button', { name: 'Save media name' }).click()
    await expect(page.getByRole('group', { name: 'Confirm media action' })).toHaveCount(0)
    await name.fill('Alpina east wing')
    await name.press('Enter')
    if (confirmNameChanges) {
      await expect(page.getByRole('group', { name: 'Confirm media action' })).toBeVisible()
      expect(state.requests.filter(item => item.path.endsWith('/actions'))).toHaveLength(0)
      await page.getByRole('button', { name: 'Confirm media change' }).click()
    } else {
      await expect(page.getByRole('group', { name: 'Confirm media action' })).toHaveCount(0)
    }
    await expect(name).toHaveCount(0)
    await expect(page.getByRole('button', { name: `Edit ${category === 'Floor plans' ? 'floor plan' : 'layout'} name: Alpina east wing` })).toBeVisible()
    await expect(page.getByLabel('Layout name', { exact: true })).toHaveCount(0)
    const saved = state.requests.filter(item => item.path.endsWith('/actions'))
    expect(saved).toHaveLength(1)
    expect(saved[0].body).toMatchObject({ action: 'rename', name: 'Alpina east wing', confirm: true,
      targets: [{ planId: category === 'Floor plans' ? floorId : layoutId, expectedUpdatedAt: state.layout.updatedAt }] })
    expect(saved[0].body).not.toHaveProperty('reviewState')
  })
}

test('amenity crops are inline, retain their category, and accept long source labels', async ({ page }) => {
  const state = await mockBuilder(page)
  state.longAmenityLabel = true
  await openProject(page, false)
  await page.getByRole('tab', { name: 'Amenities', exact: true }).click()
  await page.getByRole('button', { name: 'Crop this image' }).click()
  await expect(page.getByLabel('Crop category')).toHaveValue('amenities')
  expect((await page.getByLabel('Crop label').inputValue()).length).toBeLessThanOrEqual(64)
  const editor = page.getByRole('region', { name: 'Crop source editor' })
  expect(await editor.evaluate(node => {
    const preview = document.querySelector('.builder-media-preview')!
    const drafts = document.querySelector('.builder-media-drafts')!
    return !!(preview.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING)
      && !!(node.compareDocumentPosition(drafts) & Node.DOCUMENT_POSITION_FOLLOWING)
  })).toBe(true)
  await page.getByLabel('WIDTH (%)', { exact: true }).fill('70')
  await page.getByRole('button', { name: 'Save crop draft' }).click()
  await expect(page.getByText('Crop draft saved. No AI evaluation was started.')).toBeVisible()
  expect(state.requests.find(item => item.path.endsWith('/crops'))?.body).toMatchObject({ category: 'amenities' })
  expect(state.mediaJobs).toEqual([])
})

test('inline name conflicts retain entries and require refresh and confirmation before retrying', async ({ page }) => {
  const state = await mockBuilder(page)
  state.confirmNameChanges = true
  await openProject(page)
  await page.getByRole('button', { name: 'Edit layout name: Garden 2BHK' }).click()
  const name = page.getByRole('textbox', { name: 'Selected layout name' })
  await name.fill('My local alias')
  page.once('dialog', dialog => dialog.dismiss())
  await page.getByRole('tab', { name: 'Floor plans', exact: true }).click()
  await expect(name).toHaveValue('My local alias')
  state.mediaConflict = true
  await name.press('Enter')
  await page.getByRole('button', { name: 'Confirm media change' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'The source digest or layout version changed.' })).toBeVisible()
  await expect(name).toHaveValue('My local alias')
  const writes = () => state.requests.filter(request => request.path.endsWith('/actions'))
  expect(writes()).toHaveLength(1)
  state.mediaConflict = false
  await page.getByRole('button', { name: 'Refresh media and jobs' }).click()
  await expect(name).toBeEnabled()
  await expect(name).toHaveValue('My local alias')
  expect(writes()).toHaveLength(1)
  await name.press('Enter')
  await page.getByRole('button', { name: 'Confirm media change' }).click()
  await expect(page.getByRole('button', { name: 'Edit layout name: My local alias' })).toBeVisible()
  expect(writes()).toHaveLength(2)
  await page.getByRole('button', { name: 'Edit layout name: My local alias' }).click()
  await name.fill('Cancelled alias')
  await page.getByRole('button', { name: 'Cancel name edit' }).click()
  await expect(page.getByRole('button', { name: 'Edit layout name: My local alias' })).toBeFocused()
  expect(writes()).toHaveLength(2)
})

test('immediate name saves retain failed drafts and retry IDs and honor organization settings after refresh', async ({ page }) => {
  const state = await mockBuilder(page)
  state.mediaActionFailure = true
  await openProject(page)
  await page.getByRole('button', { name: 'Edit layout name: Garden 2BHK' }).click()
  const name = page.getByRole('textbox', { name: 'Selected layout name' })
  await name.fill('Retry this alias')
  await page.getByRole('button', { name: 'Save media name' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'Media action response unavailable.' })).toBeVisible()
  await expect(name).toHaveValue('Retry this alias')
  await expect(page.getByRole('group', { name: 'Confirm media action' })).toHaveCount(0)
  const writes = () => state.requests.filter(request => request.path.endsWith('/actions'))
  expect(writes()).toHaveLength(1)
  const requestId = writes()[0].body.requestId
  state.confirmNameChanges = true
  await page.getByRole('button', { name: 'Refresh media and jobs' }).click()
  await expect(name).toBeEnabled()
  expect(writes()).toHaveLength(1)
  await page.getByRole('button', { name: 'Save media name' }).click()
  await expect(page.getByRole('group', { name: 'Confirm media action' })).toBeVisible()
  expect(writes()).toHaveLength(1)
  state.mediaActionFailure = false
  await page.getByRole('button', { name: 'Confirm media change' }).click()
  await expect(page.getByRole('button', { name: 'Edit layout name: Retry this alias' })).toBeVisible()
  expect(writes()).toHaveLength(2)
  expect(writes()[1].body.requestId).toBe(requestId)
  await page.getByRole('button', { name: 'Deactivate plan', exact: true }).click()
  await expect(page.getByRole('group', { name: 'Confirm media action' })).toBeVisible()
  expect(writes()).toHaveLength(2)
})

test('media jobs show five full entries with scrolling and resize correctly on mobile', async ({ page }) => {
  const state = await mockBuilder(page)
  await openProject(page)
  const section = page.getByRole('region', { name: 'Media processing jobs', exact: true })
  await expect(section).toContainText('No recent media jobs.')
  state.mediaJobs = Array.from({ length: 8 }, (_, index) => ({
    id: `job-${index}`, kind: `layout_evaluation_${index + 1}`, state: 'failed',
    error: index % 2 ? 'An extraction error requiring boundary review before retrying this layout' : null,
  }))
  await page.getByRole('button', { name: 'Refresh media and jobs' }).click()
  const jobs = section.getByRole('list', { name: 'Recent media processing jobs' })
  await expect(jobs.getByRole('listitem')).toHaveCount(8)
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 844 })
    await expect.poll(() => jobs.evaluate(element => {
      const rows = Array.from(element.children).slice(0, 5)
      const expected = rows.reduce((height, row) => height + row.getBoundingClientRect().height, 0)
      return Math.abs(element.getBoundingClientRect().height - expected)
    })).toBeLessThan(1)
    expect(await jobs.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true)
  }
  await jobs.focus()
  await jobs.press('Control+End')
  await expect.poll(() => jobs.evaluate(element => element.scrollTop)).toBeGreaterThan(0)
  await expect(jobs.getByRole('listitem').last()).toBeInViewport()
  state.mediaJobs = state.mediaJobs.slice(0, 3)
  await page.getByRole('button', { name: 'Refresh media and jobs' }).click()
  await expect(jobs.getByRole('listitem')).toHaveCount(3)
  await expect.poll(() => jobs.evaluate(element => element.scrollHeight - element.clientHeight)).toBeLessThanOrEqual(1)
  expect(state.requests.filter(request => request.method === 'POST')).toEqual([])
})

test('whole-page crop gets actionable feedback instead of a rejected save', async ({ page }) => {
  const state = await mockBuilder(page)
  await openProject(page, false)
  await page.getByRole('tab', { name: 'Amenities', exact: true }).click()
  await page.getByRole('button', { name: 'Crop this image' }).click()
  await page.getByRole('button', { name: 'Save crop draft' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'Draw a crop rectangle' })).toBeVisible()
  expect(state.requests.filter(item => item.method === 'POST')).toHaveLength(0)
  await expect(page.getByRole('button', { name: 'Add full page as amenities' })).toBeEnabled()
})

test('crop update preserves plan identity and uses the original source with rotation', async ({ page }) => {
  const state = await mockBuilder(page)
  await openProject(page)
  await page.getByRole('button', { name: 'Reset / update crop' }).click()
  await expect(page.getByLabel('Source document')).toBeDisabled()
  await expect(page.getByLabel('Crop category')).toBeDisabled()
  await page.getByLabel('Source rotation').selectOption('90')
  const image = page.getByRole('img', { name: 'Original source page 1' })
  await expect(image).toHaveJSProperty('naturalWidth', 600)
  await expect(image).toHaveJSProperty('naturalHeight', 400)
  await page.getByLabel('WIDTH (%)', { exact: true }).fill('70')
  await page.getByRole('button', { name: 'Save crop draft' }).click()
  await expect(page.getByText('Crop draft saved. No AI evaluation was started.')).toBeVisible()
  expect(state.requests.find(item => item.path.endsWith('/crops'))?.body).toMatchObject({
    targetPlanId: layoutId, expectedUpdatedAt: state.layout.updatedAt, category: 'layouts', rotationDegrees: 90,
  })
})

test('project picture selection and automatic reset are versioned and confirmed', async ({ page }) => {
  const state = await mockBuilder(page)
  await openProject(page, false)
  await page.getByRole('button', { name: 'Use as project picture' }).click()
  expect(state.requests.filter(item => item.method === 'POST')).toHaveLength(0)
  await page.getByRole('button', { name: 'Confirm media change' }).click()
  await expect(page.getByText('Project picture saved. Independent review is required.')).toBeVisible()
  const saved = state.requests.find(item => item.path.endsWith('/picture'))
  expect(saved?.body).toMatchObject({ source: 'asset', assetId, expectedUpdatedAt: state.project.updatedAt })
  await page.getByRole('button', { name: 'Use automatic project picture' }).click()
  await page.getByRole('button', { name: 'Confirm media change' }).click()
  await expect.poll(() => state.requests.filter(item => item.path.endsWith('/picture')).length).toBe(2)
  expect(state.requests.filter(item => item.path.endsWith('/picture'))[1].body.source).toBe('automatic')
})

test('project picture upload waits for confirmation and sends multipart with CSRF', async ({ page }) => {
  const state = await mockBuilder(page)
  await openProject(page, false)
  await page.getByLabel('Upload project picture').setInputFiles({ name: 'cover.png', mimeType: 'image/png',
    buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jbm8AAAAASUVORK5CYII=', 'base64') })
  expect(state.requests.filter(item => item.method === 'POST')).toHaveLength(0)
  await page.getByRole('button', { name: 'Confirm media change' }).click()
  await expect(page.getByText('Project picture saved. Independent review is required.')).toBeVisible()
  const saved = state.requests.find(item => item.path.endsWith('/picture'))
  expect(saved?.headers['content-type']).toContain('multipart/form-data')
  expect(saved?.headers['x-csrftoken']).toBe('builder-test-csrf')
})

test('plan lifecycle operations require confirmation and retain original evidence', async ({ page }) => {
  const state = await mockBuilder(page)
  await openProject(page)
  for (const button of ['Deactivate plan', 'Activate plan', 'Remove plan', 'Restore plan']) {
    await page.getByRole('button', { name: button, exact: true }).click()
    await page.getByRole('button', { name: 'Confirm media change' }).click()
    await expect(page.getByRole('group', { name: 'Confirm media action' })).toHaveCount(0)
  }
  expect(state.requests.filter(item => item.path.endsWith('/actions')).map(item => item.body.action)).toEqual(['set-active', 'set-active', 'remove', 'restore'])
  expect(state.requests.filter(item => item.method === 'DELETE')).toHaveLength(0)
})

test('floor deactivation warns about derived layouts and refreshes their inactive state', async ({ page }) => {
  const state = await mockBuilder(page)
  await openProject(page, false)
  await page.getByRole('tab', { name: 'Floor plans', exact: true }).click()
  await page.getByRole('button', { name: 'Deactivate plan', exact: true }).click()
  await expect(page.getByRole('group', { name: 'Confirm media action' })).toContainText('Deactivate this floor plan and its derived layouts?')
  expect(state.floorActive).toBe(true)
  await page.getByRole('button', { name: 'Confirm media change' }).click()
  await expect(page.getByRole('button', { name: 'Activate plan', exact: true })).toBeEnabled()
  await page.getByRole('tab', { name: 'Layouts', exact: true }).click()
  await expect(page.getByRole('complementary', { name: 'Layout directory' })).toContainText('Inactive')
  await expect(page.getByRole('button', { name: 'Activate plan', exact: true })).toBeVisible()
  await page.getByRole('tab', { name: 'Floor plans', exact: true }).click()
  await page.getByRole('button', { name: 'Activate plan', exact: true }).click()
  await expect(page.getByRole('group', { name: 'Confirm media action' })).toContainText('Derived layouts will stay inactive.')
  await page.getByRole('button', { name: 'Confirm media change' }).click()
  await expect(page.getByRole('button', { name: 'Deactivate plan', exact: true })).toBeEnabled()
  await page.getByRole('tab', { name: 'Layouts', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Activate plan', exact: true })).toBeVisible()
  expect(state.floorActive).toBe(true)
  expect(state.planActive).toBe(false)
})

test('bulk replacement is explicit and carries only selected layout and draft IDs', async ({ page }) => {
  const state = await mockBuilder(page)
  state.mediaDrafts = [{ id: draftId, label: 'Replacement crop', imageUrl: `${mediaPath}/drafts/${draftId}` }]
  await openProject(page)
  await page.getByRole('checkbox', { name: 'Replacement crop' }).check()
  await page.getByRole('checkbox', { name: /Select Garden 2BHK/ }).check()
  await page.getByRole('button', { name: 'Replace selected layouts with drafts' }).click()
  await expect(page.getByRole('group', { name: 'Confirm media action' })).toContainText('Replace selected layouts')
  await page.getByRole('button', { name: 'Confirm and queue evaluation' }).click()
  await expect(page.getByText('Selected drafts attached; evaluation queued.')).toBeVisible()
  expect(state.requests.find(item => item.path.endsWith('/drafts') && item.method === 'POST')?.body).toMatchObject({
    action: 'attach', draftIds: [draftId], replacementIds: [layoutId], confirm: true,
  })
})

for (const [button, action] of [['Extract layouts', 'segment'], ['Evaluate amenity pages', 'evaluate-amenities'], ['Reprocess failed documents', 'reprocess-failed'], ['Reprocess all documents', 'reprocess-all'], ['Restore original crop', 'reset-crop'], ['Rotate layout 90° counterclockwise', 'normalize']]) {
  test(`${button} is cost-confirmed and uses the standard queue`, async ({ page }) => {
    const state = await mockBuilder(page)
    await openProject(page, false)
    if (action === 'segment') await page.getByRole('tab', { name: 'Floor plans', exact: true }).click()
    if (['reset-crop', 'normalize'].includes(action)) await page.getByRole('tab', { name: 'Layouts', exact: true }).click()
    await page.getByLabel('Evaluation profile').selectOption(profileId)
    await page.getByRole('button', { name: button, exact: true }).click()
    await expect(page.getByRole('group', { name: 'Confirm media action' })).toContainText('may incur model costs')
    expect(state.requests.filter(item => item.method === 'POST')).toHaveLength(0)
    await page.getByRole('button', { name: 'Confirm and queue evaluation' }).click()
    await expect(page.getByText('Processing queued. Refresh jobs to follow progress.')).toBeVisible()
    expect(state.requests.find(item => item.path.endsWith('/actions'))?.body).toMatchObject({ action, confirm: true, visionProfileId: profileId })
  })
}

test('layout evaluation uses selected plan version and never queues before confirmation', async ({ page }) => {
  const state = await mockBuilder(page)
  await openProject(page)
  await page.getByLabel('Evaluation profile').selectOption(profileId)
  await page.getByRole('button', { name: 'Re-evaluate layout' }).click()
  await expect(page.getByRole('group', { name: 'Confirm media action' })).toContainText('may incur model costs')
  await page.getByRole('button', { name: 'Cancel media action' }).click()
  expect(state.requests.filter(item => item.path.endsWith('/evaluate'))).toHaveLength(0)
  await page.getByRole('button', { name: 'Re-evaluate layout' }).click()
  await page.getByRole('button', { name: 'Confirm and queue evaluation' }).click()
  await expect(page.getByText('Layout re-evaluation queued. Refresh jobs to see progress.')).toBeVisible()
  const evaluations = state.requests.filter(item => item.path.endsWith('/evaluate'))
  expect(evaluations).toHaveLength(1)
  expect(evaluations[0].body).toEqual({ planId: layoutId, expectedUpdatedAt: state.layout.updatedAt, visionProfileId: profileId, confirm: true, requestId: expect.stringMatching(/^[a-f0-9-]{36}$/) })
  await page.getByRole('button', { name: 'Refresh media and jobs' }).click()
  await expect(page.getByText(/Media and jobs refreshed/)).toBeVisible()
  expect(state.requests.filter(item => item.path.endsWith('/evaluate'))).toHaveLength(1)
})

for (const category of ['floor-plans', 'amenities'] as const) {
  test(`full source page addition as ${category} requires confirmation`, async ({ page }) => {
    const state = await mockBuilder(page)
    await openProject(page, false)
    await page.getByRole('button', { name: 'Crop source page', exact: true }).click()
    await page.getByRole('button', { name: category === 'floor-plans' ? 'Add full page as floor plan' : 'Add full page as amenities', exact: true }).click()
    expect(state.requests.filter(item => item.method === 'POST')).toHaveLength(0)
    await expect(page.getByRole('group', { name: 'Confirm media action' })).toContainText('may incur model costs')
    await page.getByRole('button', { name: 'Confirm and queue evaluation' }).click()
    await expect(page.getByText('Source page added; processing queued.')).toBeVisible()
    expect(state.requests.filter(item => item.path.endsWith('/pages') && item.method === 'POST').map(item => item.body)).toEqual([
      { documentId, sourceSha256, pages: [1], category, confirm: true, requestId: expect.stringMatching(/^[a-f0-9-]{36}$/) },
    ])
  })
}

test('unsaved layouts survive tab switches and refresh and block model actions', async ({ page }) => {
  const state = await mockBuilder(page)
  await openProject(page)
  await page.getByLabel('Total price (INR)', { exact: true }).fill('19000000')
  await expect(page.getByRole('button', { name: 'Re-evaluate layout' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Crop source page', exact: true })).toBeDisabled()
  await page.getByRole('tab', { name: 'Amenities', exact: true }).click()
  await page.getByRole('button', { name: 'Refresh media and jobs' }).click()
  await expect(page.getByText(/Media and jobs refreshed/)).toBeVisible()
  await page.getByRole('tab', { name: 'Layouts', exact: true }).click()
  await expect(page.getByLabel('Total price (INR)', { exact: true })).toHaveValue('19000000')
  page.once('dialog', dialog => dialog.dismiss())
  await page.getByLabel('Active organization').selectOption(secondOrganizationId)
  await expect(page.getByLabel('Active organization')).toHaveValue(organizationId)
  await expect(page.getByLabel('Total price (INR)', { exact: true })).toHaveValue('19000000')
  expect(state.requests.filter(item => item.method === 'POST')).toHaveLength(0)
})

test('project edits and server read-only permissions lock media and layout writes', async ({ page }) => {
  const state = await mockBuilder(page)
  await openProject(page)
  await page.getByLabel('Project name', { exact: true }).fill('Unsaved project')
  await expect(page.getByRole('button', { name: 'Re-evaluate layout' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Crop source page', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Edit layout name: Garden 2BHK' })).toBeDisabled()
  await page.getByRole('button', { name: 'Refresh media and jobs' }).click()
  await expect(page.getByText(/Media and jobs refreshed/)).toBeVisible()
  await expect(page.getByLabel('Project name', { exact: true })).toHaveValue('Unsaved project')
  await page.getByLabel('Project name', { exact: true }).fill(initialProject.submittedName)
  state.mediaEditable = false
  await page.getByRole('button', { name: 'Refresh media and jobs' }).click()
  await expect(page.getByText(/organization access permits viewing only/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Re-evaluate layout' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Save layout', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Edit layout name: Garden 2BHK' })).toBeDisabled()
  expect(state.requests.filter(item => ['POST', 'PATCH'].includes(item.method))).toEqual([])
})

test('media access revocation clears private workspace and redirects instead of retaining previews', async ({ page }) => {
  const state = await mockBuilder(page)
  await openProject(page, false)
  state.mediaForbidden = true
  await page.getByRole('button', { name: 'Refresh media and jobs' }).click()
  await expect(page).toHaveURL(/\/builder\/login$/)
  await expect(page.getByRole('heading', { name: 'Builder sign in' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Project media workspace' })).toHaveCount(0)
  await expect(page.getByText(/session or organization access expired/)).toBeVisible()
})

test('switching organizations aborts pending media refresh and does not restore private project data', async ({ page }) => {
  await mockBuilder(page)
  await openProject(page, false)
  let release: () => void = () => {}
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route(`**${mediaPath}`, async route => { await gate; await route.fallback() })
  const request = page.waitForRequest(request => new URL(request.url()).pathname === mediaPath)
  await page.getByRole('button', { name: 'Refresh media and jobs' }).click()
  await request
  await page.getByLabel('Active organization').selectOption(secondOrganizationId)
  release()
  await expect(page.getByRole('heading', { name: 'Select a project' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Project media workspace' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Builder Gardens/ })).toHaveCount(0)
})

test('discarding selected crop drafts is confirmed and does not queue AI', async ({ page }) => {
  const state = await mockBuilder(page)
  state.mediaDrafts = [{ id: draftId, label: 'Disposable crop', imageUrl: `${mediaPath}/drafts/${draftId}` }]
  await openProject(page, false)
  await page.getByRole('checkbox', { name: /Disposable crop/ }).check()
  await page.getByRole('button', { name: 'Discard selected drafts' }).click()
  await expect(page.getByRole('group', { name: 'Confirm media action' })).toContainText('not their source documents')
  expect(state.requests.filter(item => item.path === `${mediaPath}/drafts` && item.method === 'POST')).toHaveLength(0)
  await page.getByRole('button', { name: 'Confirm discard' }).click()
  await expect(page.getByText('Selected crop drafts discarded.')).toBeVisible()
  expect(state.requests.find(item => item.path === `${mediaPath}/drafts` && item.method === 'POST')?.body).toEqual({ action: 'discard', draftIds: [draftId], confirm: true, requestId: expect.stringMatching(/^[a-f0-9-]{36}$/) })
  expect(state.mediaJobs).toEqual([])
})

for (const action of ['attach', 'evaluate'] as const) {
  test(`${action} retries retain request ID after refresh and change it for a different profile`, async ({ page }) => {
    const state = await mockBuilder(page)
    state.mediaActionFailure = true
    state.mediaDrafts = [{ id: draftId, label: 'Retry draft', imageUrl: `${mediaPath}/drafts/${draftId}` }]
    await openProject(page)
    if (action === 'attach') await page.getByRole('checkbox', { name: /Retry draft/ }).check()
    const start = page.getByRole('button', { name: action === 'attach' ? 'Attach selected drafts' : 'Re-evaluate layout', exact: true })
    const confirm = page.getByRole('button', { name: 'Confirm and queue evaluation' })
    const requests = () => state.requests.filter(item => item.method === 'POST' && item.path === `${mediaPath}/${action === 'attach' ? 'drafts' : 'evaluate'}`)
    await start.click()
    await confirm.click()
    await expect(page.getByRole('alert').filter({ hasText: 'Media action response unavailable' })).toBeVisible()
    expect(requests()).toHaveLength(1)
    const firstId = requests()[0].body.requestId
    await page.getByRole('button', { name: 'Refresh media and jobs' }).click()
    await expect(page.getByText(/Media and jobs refreshed/)).toBeVisible()
    expect(requests()).toHaveLength(1)
    await start.click()
    await confirm.click()
    await expect(page.getByRole('alert').filter({ hasText: 'Media action response unavailable' })).toBeVisible()
    expect(requests()).toHaveLength(2)
    expect(requests()[1].body).toEqual(requests()[0].body)
    await page.getByRole('button', { name: 'Cancel media action' }).click()
    await page.getByLabel('Evaluation profile').selectOption(profileId)
    await start.click()
    await confirm.click()
    await expect(page.getByRole('alert').filter({ hasText: 'Media action response unavailable' })).toBeVisible()
    expect(requests()).toHaveLength(3)
    expect(requests()[2].body.requestId).not.toBe(firstId)
    expect(requests()[2].body.visionProfileId).toBe(profileId)
    state.mediaActionFailure = false
    await confirm.click()
    await expect(page.getByText(action === 'attach' ? 'Selected drafts attached; evaluation queued.' : 'Layout re-evaluation queued. Refresh jobs to see progress.')).toBeVisible()
    expect(requests()).toHaveLength(4)
    expect(requests()[3].body).toEqual(requests()[2].body)
  })
}
