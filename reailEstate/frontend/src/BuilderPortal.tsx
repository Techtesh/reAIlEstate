import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiError, apiPath, cancelSessionRequests, json, onSessionExpired } from './api'
import { BuilderMediaWorkspace, type BuilderLayout as Layout } from './BuilderMediaWorkspace'
import './BuilderPortal.css'

type Account = { id: string | number; username: string; email: string }
type Organization = { id: string; legalName: string; role: 'owner' | 'editor' }
type Address = { line1: string; line2: string; city: string; pincode: string; locality?: string }
type Project = {
  id: string; organizationId: string; organizationName: string; submittedName: string
  mahaReraId: string; locality: string; address: Address; processingAttested: boolean
  status: string; verificationStatus: string; updatedAt: string; createdAt: string
}
type ProjectDraft = Pick<Project, 'submittedName' | 'mahaReraId' | 'locality' | 'address' | 'processingAttested'>
type Evidence = { id: string; originalName: string; status: string; scanStatus: string; byteLength: number; errorCategory?: string }
type UploadIntent = { documentId: string; uploadAction: string; method: 'PUT'; requiredHeaders: Record<string, string>; maximumSize: number }
type Upload = { file: File; key: string; intent: UploadIntent | null; phase: 'intent' | 'put' | 'complete' | 'done' }
const message = (error: unknown) => error instanceof Error ? error.message : 'The action could not be completed.'
const aborted = (error: unknown) => error instanceof DOMException && error.name === 'AbortError'
const pathFor = (id: string) => `/api/v1/builder/projects/${encodeURIComponent(id)}`
const documentsFor = (id: string) => `/api/v1/developer-projects/${encodeURIComponent(id)}/documents`
const emptyDraft = (): ProjectDraft => ({ submittedName: '', mahaReraId: '', locality: '', address: { line1: '', line2: '', city: '', pincode: '' }, processingAttested: false })
const projectDraft = (project: Project): ProjectDraft => ({
  submittedName: project.submittedName, mahaReraId: project.mahaReraId, locality: project.locality,
  address: { line1: project.address?.line1 ?? '', line2: project.address?.line2 ?? '', city: project.address?.city ?? '', pincode: project.address?.pincode ?? '' },
  processingAttested: project.processingAttested,
})

function useRequestScope() {
  const [scope] = useState(() => ({ controller: new AbortController(), mounted: false }))
  useEffect(() => {
    scope.mounted = true
    return () => {
      scope.mounted = false
      // StrictMode immediately reconnects effects; only a real unmount ends this scope.
      queueMicrotask(() => { if (!scope.mounted) scope.controller.abort() })
    }
  }, [scope])
  return scope.controller.signal
}

export function BuilderPortal() {
  const navigate = useNavigate()
  const [user, setUser] = useState<Account | null>(null)
  const [organizations, setOrganizations] = useState<Organization[]>([])
  const [booting, setBooting] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const signal = useRequestScope()
  const booted = useRef(false)
  const sessionEnded = useRef(false)
  useEffect(() => {
    document.title = 'Builder workspace | MMR Homefinder'
    return onSessionExpired(() => {
      sessionEnded.current = true
      cancelSessionRequests(); setUser(null); setOrganizations([]); setBooting(false); setBusy(false)
      setError('Your session or organization access expired. Private entries were cleared. Sign in again.')
      navigate('/builder/login', { replace: true })
    })
  }, [navigate])
  useEffect(() => {
    const controller = new AbortController()
    async function bootstrap() {
      if (sessionEnded.current) return
      setBooting(true); setError('')
      try {
        await api('/api/v1/auth/builder-login', { signal: controller.signal })
        const account = await api<Account>('/api/v1/auth/me', { signal: controller.signal })
        const memberships = await api<Organization[]>('/api/v1/builder/organizations', { signal: controller.signal })
        setUser(account); setOrganizations(memberships); navigate('/builder', { replace: true })
      } catch (failure) {
        if (aborted(failure)) return
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) navigate('/builder/login', { replace: true })
        else setError(message(failure))
      } finally { if (!controller.signal.aborted) { booted.current = true; setBooting(false) } }
    }
    void bootstrap()
    return () => { controller.abort(); cancelSessionRequests() }
  }, [navigate, attempt])
  async function signedIn(account: Account) {
    const memberships = await api<Organization[]>('/api/v1/builder/organizations', { signal })
    sessionEnded.current = false
    setUser(account); setOrganizations(memberships); setError(''); navigate('/builder', { replace: true })
  }
  async function logout() {
    setBusy(true); setError('')
    try {
      await api('/api/v1/auth/logout', { method: 'POST', body: '{}', signal })
      sessionEnded.current = true
      cancelSessionRequests(); setUser(null); setOrganizations([]); navigate('/builder/login', { replace: true })
    } catch (failure) { if (!aborted(failure)) setError(message(failure)) }
    finally { if (!signal.aborted) setBusy(false) }
  }
  return <div className="builder-portal">
    <a className="skip-link" href="#builder-main">Skip to content</a>
    <header className="builder-header"><Link to="/" className="builder-brand">MMR Homefinder <span>Builder workspace</span></Link><nav aria-label="Builder navigation"><Link to="/browse">Browse homes</Link>{user && <><Link to="/?workspace=choose">Switch workspace</Link><span>{user.username}</span><button className="secondary" onClick={() => void logout()} disabled={busy}>Sign out</button></>}</nav></header>
    <main id="builder-main" className="builder-main">
      {error && <div role="alert" className="builder-notice">{error}{!user && booted.current && <button className="secondary" onClick={() => setAttempt(value => value + 1)}>Retry connection</button>}</div>}
      {booting ? <p role="status">Connecting to your builder workspace…</p> : !user ? <BuilderLogin key={attempt} onSuccess={signedIn} /> : organizations.length === 0 ? <section className="builder-panel"><h1>Builder access unavailable</h1><p>You need an owner or editor membership in an active organization. A platform administrator must provision your account and membership. Buyer or staff access alone does not grant builder access.</p><p>Sign out to use a different account.</p></section> : <BuilderWorkspace key={user.id} organizations={organizations} />}
    </main>
    <footer className="builder-footer">Accounts and organization access are managed by the platform administrator. Builders cannot publish their own evidence.</footer>
  </div>
}

function BuilderLogin({ onSuccess }: { onSuccess: (account: Account) => Promise<void> }) {
  const [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const signal = useRequestScope()
  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget, data = new FormData(form)
    setBusy(true); setError('')
    try {
      await api('/api/v1/auth/builder-login', { signal })
      const account = await api<Account>('/api/v1/auth/builder-login', { method: 'POST', body: json({ username: data.get('username'), password: data.get('password') }), signal })
      form.reset(); await onSuccess(account)
    } catch (failure) {
      if (!aborted(failure)) setError(message(failure))
    } finally { if (!signal.aborted) setBusy(false) }
  }
  return <section className="builder-login builder-panel"><p className="eyebrow">For organization owners and editors</p><h1>Builder sign in</h1><p>Manage your organization’s projects and submit evidence for independent review.</p>{error && <p role="alert" className="field-error">{error}</p>}<form onSubmit={event => void login(event)}><fieldset disabled={busy}><label>Username<input name="username" autoComplete="username" required /></label><label>Password<input name="password" type="password" autoComplete="current-password" required /></label><button className="primary" type="submit">{busy ? 'Signing in…' : 'Sign in to builder workspace'}</button></fieldset></form><p className="data-note">No public sign-up. Contact your platform administrator for an account or organization access.</p></section>
}

function BuilderWorkspace({ organizations }: { organizations: Organization[] }) {
  const [organizationId, setOrganizationId] = useState(organizations[0].id)
  const [dirty, setDirty] = useState(false)
  const organization = organizations.find(item => item.id === organizationId)!
  return <>
    <div className="builder-heading"><div><p className="eyebrow">Organization workspace</p><h1>Your projects</h1><p>{organization.legalName} · {organization.role}</p></div><label>Active organization<select value={organizationId} onChange={event => { if (!dirty || window.confirm('Discard unsaved project, layout or crop entries and switch organization? An in-flight action may still finish on the server.')) { setDirty(false); setOrganizationId(event.target.value) } }}>{organizations.map(item => <option value={item.id} key={item.id}>{item.legalName} ({item.role})</option>)}</select></label></div>
    <OrganizationProjects key={organizationId} organization={organization} dirty={dirty} onDirty={setDirty} />
  </>
}

function OrganizationProjects({ organization, dirty, onDirty }: { organization: Organization; dirty: boolean; onDirty: (dirty: boolean) => void }) {
  const signal = useRequestScope()
  const [projects, setProjects] = useState<Project[]>([])
  const [selected, setSelected] = useState<Project | 'new' | null>(null)
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [reload, setReload] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    api<Project[]>('/api/v1/builder/projects', { signal: AbortSignal.any([signal, controller.signal]) })
      .then(items => setProjects(items.filter(item => item.organizationId === organization.id)))
      .catch(failure => { if (!aborted(failure)) setError(message(failure)) })
      .finally(() => { if (!controller.signal.aborted && !signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [organization.id, signal, reload])
  function saved(project: Project) {
    if (project.organizationId !== organization.id) return
    setProjects(current => [project, ...current.filter(item => item.id !== project.id)])
    setSelected(project)
  }
  function select(project: Project | 'new') {
    if (project === 'new' ? selected === 'new' : selected !== 'new' && selected?.id === project.id) return
    if (dirty && !window.confirm('Discard unsaved project, layout or crop entries and switch project? An in-flight action may still finish on the server.')) return
    onDirty(false); setSelected(project)
  }
  return <div className="builder-workspace">
    <aside className="builder-panel builder-project-list" aria-label="Organization projects"><button className="primary" onClick={() => select('new')}>New project</button>{loading ? <p role="status">Loading projects…</p> : error ? <div role="alert"><p>{error}</p><button className="secondary" onClick={() => setReload(value => value + 1)}>Retry projects</button></div> : projects.length === 0 ? <p>No projects yet. Create your first project.</p> : <ul>{projects.map(project => <li key={project.id}><button aria-pressed={selected !== 'new' && selected?.id === project.id} onClick={() => select(project)}><strong>{project.submittedName}</strong><span>{project.locality || 'Locality not supplied'} · {project.status.replaceAll('_', ' ')}</span></button></li>)}</ul>}</aside>
    {selected ? <ProjectEditor key={selected === 'new' ? 'new' : selected.id} organization={organization} initial={selected === 'new' ? null : selected} onSaved={saved} onDirty={onDirty} /> : <section className="builder-panel"><h2>Select a project</h2><p>Create a project or select one from your organization to edit details, upload evidence and request review. Publication is reserved for independent reviewers.</p></section>}
  </div>
}

function ProjectEditor({ organization, initial, onSaved, onDirty }: { organization: Organization; initial: Project | null; onSaved: (project: Project) => void; onDirty: (dirty: boolean) => void }) {
  const signal = useRequestScope()
  const [project, setProject] = useState(initial)
  const [draft, setDraft] = useState<ProjectDraft>(() => initial ? projectDraft(initial) : emptyDraft())
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('')
  const [conflict, setConflict] = useState(false), [confirmReload, setConfirmReload] = useState(false), [confirmArchive, setConfirmArchive] = useState(false)
  const [evidenceVersion, setEvidenceVersion] = useState(0)
  const [mediaDirty, setMediaDirty] = useState(false)
  const locked = !!project && ['retired', 'merged'].includes(project.status)
  const dirty = json(draft) !== json(project ? projectDraft(project) : emptyDraft())
  const dirtyRef = useRef(dirty)
  dirtyRef.current = dirty
  useEffect(() => { onDirty(dirty || mediaDirty || busy) }, [dirty, mediaDirty, busy, onDirty])
  useEffect(() => {
    if (!dirty && !mediaDirty && !busy) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty, mediaDirty, busy])
  function accepted(saved: Project) { setProject(saved); setDraft(projectDraft(saved)); onSaved(saved) }
  function failure(error: unknown) {
    if (aborted(error)) return
    if (error instanceof ApiError && error.status === 409) setConflict(true)
    setError(message(error))
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(''); setNotice('')
    try {
      const saved = await api<Project>(project ? pathFor(project.id) : '/api/v1/builder/projects', {
        method: project ? 'PATCH' : 'POST', signal,
        body: json({ ...draft, ...(project ? { expectedUpdatedAt: project.updatedAt } : { organizationId: organization.id }) }),
      })
      accepted(saved); setNotice('Project saved. Changes require independent review before publication.')
    } catch (error) { failure(error) } finally { if (!signal.aborted) setBusy(false) }
  }
  async function reloadProject() {
    if (!project) return
    setBusy(true); setError('')
    try {
      accepted(await api<Project>(pathFor(project.id), { signal }))
      setConflict(false); setConfirmReload(false); setConfirmArchive(false); setEvidenceVersion(value => value + 1); setNotice('Latest saved project loaded.')
    } catch (error) { failure(error) } finally { if (!signal.aborted) setBusy(false) }
  }
  async function archive() {
    if (!project) return
    setBusy(true); setError(''); setNotice('')
    try {
      accepted(await api<Project>(`${pathFor(project.id)}:archive`, { method: 'POST', body: json({ expectedUpdatedAt: project.updatedAt }), signal }))
      setConfirmArchive(false); setNotice('Project archived. Evidence has been retained.')
    } catch (error) { failure(error) } finally { if (!signal.aborted) setBusy(false) }
  }
  async function submitReview() {
    if (!project) return
    setBusy(true); setError(''); setNotice('')
    try {
      await api(`/api/v1/developer-projects/${encodeURIComponent(project.id)}:submit-review`, { method: 'POST', body: '{}', signal })
      setNotice('Review requested. This queues review only; it does not publish or run extraction.')
      accepted(await api<Project>(pathFor(project.id), { signal }))
    } catch (error) { failure(error); if (error instanceof ApiError && error.status === 400) setError(`${message(error)} Review requires processing attestation, uploaded evidence and a registry verification attempt. Save your details and refresh evidence before retrying.`) }
    finally { if (!signal.aborted) setBusy(false) }
  }
  async function layoutChanged(noticeMessage = 'Layout saved. Independent review is required.') {
    if (!project) return
    if (dirtyRef.current) { setConflict(true); setError('Layout changes updated the project version. Your local entries are preserved. Reload the project before saving them.'); return }
    setBusy(true)
    try {
      const latest = await api<Project>(pathFor(project.id), { signal })
      if (dirtyRef.current) { setConflict(true); setError('Layout changes updated the project version. Your local entries are preserved. Reload the project before saving them.'); return }
      accepted(latest); setNotice(noticeMessage)
    }
    catch (error) { failure(error) } finally { if (!signal.aborted) setBusy(false) }
  }
  return <div className="builder-editor">
    <section className="builder-panel"><h2>{project ? 'Project details' : 'Create project'}</h2>{project && <p className="builder-status">Status: <strong>{project.status.replaceAll('_', ' ')}</strong> · Registry: {project.verificationStatus.replaceAll('_', ' ')}</p>}
      <p>Organization: {organization.legalName}. Organization assignment and publication status cannot be changed here.</p>
      {locked && <p role="status">This project is {project?.status}; editing and uploads are unavailable.</p>}
      {error && <p role="alert" className="field-error">{error}</p>}{notice && <p role="status" className="builder-notice">{notice}</p>}
      {conflict && <div role="alert" className="builder-conflict"><h3>Changes were not overwritten</h3><p>The saved version or project state changed. Your entries remain below. Copy anything you need before reloading. Saving is blocked until you load the latest version.</p><button className="secondary" disabled={busy} onClick={() => setConfirmReload(true)}>Reload latest version</button></div>}
      {confirmReload && <div className="builder-confirm" role="group" aria-label="Confirm reload"><p>Replace your unsaved project, layout and crop entries with the latest saved project? An in-flight media action may still finish on the server.</p><div className="button-row"><button className="primary" disabled={busy} onClick={() => void reloadProject()}>Discard entries and reload</button><button className="secondary" disabled={busy} onClick={() => setConfirmReload(false)}>Keep editing locally</button></div></div>}
      <form onSubmit={event => void save(event)}><fieldset disabled={busy || locked}><div className="builder-form-grid">
        <label>Project name<input value={draft.submittedName} maxLength={255} required onChange={event => setDraft({ ...draft, submittedName: event.target.value })} /></label>
        <label>MahaRERA registration<input value={draft.mahaReraId} maxLength={64} placeholder="P51800000001" required onChange={event => setDraft({ ...draft, mahaReraId: event.target.value.toUpperCase() })} /></label>
        <label>Locality<input value={draft.locality} required onChange={event => setDraft({ ...draft, locality: event.target.value })} /></label>
        <label>Address line 1<input value={draft.address.line1} onChange={event => setDraft({ ...draft, address: { ...draft.address, line1: event.target.value } })} /></label>
        <label>Address line 2<input value={draft.address.line2} onChange={event => setDraft({ ...draft, address: { ...draft.address, line2: event.target.value } })} /></label>
        <label>City<input value={draft.address.city} onChange={event => setDraft({ ...draft, address: { ...draft.address, city: event.target.value } })} /></label>
        <label>PIN code<input value={draft.address.pincode} inputMode="numeric" pattern="[0-9]{6}" title="Six digit PIN code" onChange={event => setDraft({ ...draft, address: { ...draft.address, pincode: event.target.value } })} /></label>
      </div><label className="builder-check"><input type="checkbox" checked={draft.processingAttested} onChange={event => setDraft({ ...draft, processingAttested: event.target.checked })} />I am authorized to submit and process this project’s documents and evidence.</label><p className="data-note">Attestation is required before review. MahaRERA verification is not legal certification.</p><button className="primary" type="submit" disabled={conflict}>{busy ? 'Saving…' : project ? 'Save project changes' : 'Create project'}</button></fieldset></form>
      {project && !locked && <div className="builder-actions"><button className="secondary" disabled={busy || conflict || dirty || mediaDirty} onClick={() => void submitReview()}>Submit for review</button><button className="secondary" disabled={busy || conflict || dirty || mediaDirty} onClick={() => setConfirmArchive(true)}>Archive project</button>{(dirty || mediaDirty) && <p>Save your project and layout changes and finish crop editing before submitting or archiving.</p>}</div>}
      {confirmArchive && <div className="builder-confirm" role="group" aria-label="Confirm archive"><h3>Archive this project?</h3><p>This retires the project and prevents further edits. It does not delete documents or evidence.</p><div className="button-row"><button className="primary" disabled={busy || conflict || dirty || mediaDirty} onClick={() => void archive()}>Confirm archive</button><button className="secondary" disabled={busy} onClick={() => setConfirmArchive(false)}>Cancel archive</button></div></div>}
    </section>
    {project && <><ProjectDocuments key={`${project.id}-${evidenceVersion}`} projectId={project.id} locked={locked || conflict} /><BuilderMediaWorkspace key={`${project.id}-media-${evidenceVersion}`} projectId={project.id} locked={locked || conflict || busy || dirty} onChanged={noticeMessage => void layoutChanged(noticeMessage)} onDirty={setMediaDirty} renderLayout={(layout, layoutLocked, onSavedLayout, onDirtyLayout) => <LayoutEditor layout={layout} projectId={project.id} locked={layoutLocked} onSaved={onSavedLayout} onDirty={onDirtyLayout} />} /></>}
  </div>
}

function ProjectDocuments({ projectId, locked }: { projectId: string; locked: boolean }) {
  const signal = useRequestScope()
  const [documents, setDocuments] = useState<Evidence[]>([]), [error, setError] = useState(''), [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(false), [busy, setBusy] = useState(false), [upload, setUpload] = useState<Upload | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  useEffect(() => {
    setLoading(true)
    api<Evidence[]>(documentsFor(projectId), { signal }).then(setDocuments).catch(error => { if (!aborted(error)) setError(message(error)) }).finally(() => { if (!signal.aborted) setLoading(false) })
  }, [projectId, signal])
  async function refresh() {
    setLoading(true); setError('')
    try { setDocuments(await api<Evidence[]>(documentsFor(projectId), { signal })) }
    catch (error) { if (!aborted(error)) setError(message(error)) }
    finally { if (!signal.aborted) setLoading(false) }
  }
  function select(file: File | undefined) {
    setError(''); setNotice(''); setUpload(null)
    if (!file) return
    if (!['application/pdf', 'image/png', 'image/jpeg', 'image/webp'].includes(file.type)) { setError('Choose a PDF, PNG, JPEG or WEBP file.'); return }
    if (!file.size) { setError('The selected file is empty.'); return }
    setUpload({ file, key: crypto.randomUUID(), intent: null, phase: 'intent' })
  }
  async function send() {
    if (!upload || locked) return
    setBusy(true); setError(''); setNotice('')
    let current = upload
    try {
      if (current.phase === 'intent') {
        const bytes = await current.file.arrayBuffer()
        const digest = await crypto.subtle.digest('SHA-256', bytes)
        signal.throwIfAborted()
        const sha256 = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('')
        const intent = await api<UploadIntent>(`${documentsFor(projectId)}:upload-intent`, { method: 'POST', signal, body: json({ filename: current.file.name, declaredMediaType: current.file.type, byteLength: current.file.size, sha256 }) })
        current = { ...current, intent, phase: 'put' }; setUpload(current)
      }
      if (current.phase === 'put') {
        const intent = current.intent!
        if (intent.method !== 'PUT') throw new Error('Unsupported upload method. Contact the platform administrator.')
        const destination = apiPath(intent.uploadAction)
        if (current.file.size > intent.maximumSize) throw new Error(`This file exceeds the server limit of ${Math.floor(intent.maximumSize / 1024 / 1024)} MB. Select a smaller file.`)
        const headers = new Headers(intent.requiredHeaders)
        headers.set('Content-Disposition', `attachment; filename="${current.file.name.replace(/[^\x20-\x7E]|["\\]/g, '_')}"`)
        await api(destination, { method: 'PUT', signal, headers, body: current.file })
        current = { ...current, phase: 'complete' }; setUpload(current)
      }
      if (current.phase === 'complete') {
        await api(`${documentsFor(projectId)}/${encodeURIComponent(current.intent!.documentId)}:complete`, { method: 'POST', signal, headers: { 'Idempotency-Key': current.key }, body: '{}' })
        current = { ...current, phase: 'done' }; setUpload(current)
        if (fileInput.current) fileInput.current.value = ''
        setNotice('Upload queued. Scanning and extraction require backend workers; no jobs or models were started by this page.')
        await refresh()
      }
    } catch (failure) {
      if (!aborted(failure)) {
        const rejectedIntent = current.phase === 'intent' && failure instanceof ApiError && [400, 409, 413, 415].includes(failure.status)
        if (rejectedIntent) {
          setUpload(null)
          if (fileInput.current) fileInput.current.value = ''
        }
        setError(`${message(failure)}${rejectedIntent ? '' : current.phase === 'intent' ? ' An upload request may have reached the server. Refresh documents before explicitly retrying the request.' : ' Retry resumes this same upload; it does not create a new document.'}`)
      }
    }
    finally { if (!signal.aborted) setBusy(false) }
  }
  const pending = upload && upload.phase !== 'done'
  return <section className="builder-panel"><h2>Project documents</h2><p>Upload PDF, PNG, JPEG or WEBP evidence. Files are private until reviewed. Uploading only queues processing.</p>{error && <p role="alert" className="field-error">{error}</p>}{notice && <p role="status" className="builder-notice">{notice}</p>}<label>Evidence file<input ref={fileInput} type="file" accept="application/pdf,image/png,image/jpeg,image/webp" disabled={busy || locked || !!(pending && upload.intent)} onChange={event => select(event.target.files?.[0])} /></label>
    {pending && <p>{upload.file.name} · {upload.file.size.toLocaleString()} bytes · {upload.phase === 'intent' ? 'Ready to request upload' : upload.phase === 'put' ? 'Ready to transfer file' : 'File transferred; completion pending'}</p>}
    <div className="button-row"><button className="primary" disabled={!pending || busy || locked} onClick={() => void send()}>{busy ? 'Uploading…' : error && pending ? upload.phase === 'intent' ? 'Retry upload request' : 'Retry same upload' : 'Upload evidence'}</button><button className="secondary" disabled={loading || busy} onClick={() => void refresh()}>Refresh documents</button>{pending && upload.intent && <button className="secondary" disabled={busy} onClick={() => { setUpload(null); setError(''); if (fileInput.current) fileInput.current.value = '' }}>Cancel local upload</button>}</div>
    {loading ? <p role="status">Loading documents…</p> : documents.length ? <ul className="builder-documents">{documents.map(document => <li key={document.id}><strong>{document.originalName}</strong><span>Status: {document.status} · Scan: {document.scanStatus} · {document.byteLength.toLocaleString()} bytes</span>{document.errorCategory && <span className="field-error">Processing issue: {document.errorCategory}. Contact the platform administrator if it persists.</span>}</li>)}</ul> : <p>No evidence uploaded yet.</p>}
  </section>
}

function LayoutEditor({ layout, projectId, locked, onSaved, onDirty }: { layout: Layout; projectId: string; locked: boolean; onSaved: (layout: Layout) => void; onDirty: (id: string, dirty: boolean) => void }) {
  const signal = useRequestScope()
  const [price, setPrice] = useState(layout.price ?? '')
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [conflict, setConflict] = useState(false)
  const dirty = price !== (layout.price ?? '') || busy || conflict
  useEffect(() => { onDirty(layout.id, dirty) }, [layout.id, dirty, onDirty])
  useEffect(() => () => onDirty(layout.id, false), [layout.id, onDirty])
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('')
    if (price !== '' && (!/^\d+(\.\d{1,2})?$/.test(price) || Number(price) <= 0)) { setError('Enter a positive INR amount with up to two decimal places, or leave price blank.'); setBusy(false); return }
    try { onSaved(await api<Layout>(`${pathFor(projectId)}/layouts/${encodeURIComponent(layout.id)}`, { method: 'PATCH', signal, body: json({ name: layout.name, price: price || null, expectedUpdatedAt: layout.updatedAt }) })) }
    catch (error) { if (!aborted(error)) { setError(message(error)); if (error instanceof ApiError && error.status === 409) setConflict(true) } }
    finally { if (!signal.aborted) setBusy(false) }
  }
  async function reload() {
    setBusy(true); setError('')
    try { const latest = await api<Layout[]>(`${pathFor(projectId)}/layouts`, { signal }); const saved = latest.find(item => item.id === layout.id); if (!saved) throw new Error('This layout is no longer available.'); setPrice(saved.price ?? ''); setConflict(false); onSaved(saved) }
    catch (error) { if (!aborted(error)) setError(message(error)) } finally { if (!signal.aborted) setBusy(false) }
  }
  return <form className="builder-layout" aria-label={`Edit layout ${layout.name}`} onSubmit={event => void save(event)}><p>{layout.configurationType} · Carpet area: {layout.carpetAreaSqFt ?? 'Unknown'}{layout.carpetAreaSqFt ? ' sq ft' : ''} · Review: {layout.reviewState.replaceAll('_', ' ')}</p>{error && <p role="alert" className="field-error">{error}</p>}{conflict && <div className="builder-conflict" role="alert"><p>No changes were overwritten. Your entries remain below. Copy your edits before discarding them to reload.</p><button className="secondary" type="button" disabled={busy} onClick={() => void reload()}>Discard layout entries and reload</button></div>}<fieldset disabled={busy || locked}><label>Total price (INR)<input value={price} inputMode="decimal" placeholder="Leave blank for unknown" onChange={event => setPrice(event.target.value)} /></label><button className="secondary" type="submit" disabled={conflict}>{busy ? 'Saving layout…' : 'Save layout'}</button></fieldset></form>
}
