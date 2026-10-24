import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { api, ApiError, cancelSessionRequests, collection, json, onSessionExpired } from './api'
import { errorMessage } from './errors'
import { ThemeControl } from './ThemeControl'
import { AdminWorkspace } from './BuilderPortal'
import './AdminPortal.css'
import { hasUnsavedChanges, useUnsavedChanges } from './useUnsavedChanges'

const base = '/api/v1/admin/console'
type Session = { username: string; permissions: string[]; resources: { key: string; label: string; create: boolean; update: boolean }[] }
type Row = Record<string, unknown> & { id: string; versionToken: string }
type Field = { name: string; label: string; required: boolean; kind: string; multiple: boolean; options?: { value: string; label: string }[] }
type Records = { count: number; page: number; pageSize: number; results: Row[]; schema: Field[]; canCreate: boolean; canUpdate: boolean; canDelete: boolean; canAct: boolean; localities?: string[] }
const keyFor = (key: string) => key.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase())
const sections: [string, string[]][] = [
  ['Projects', ['projects', 'documents', 'floor-plans', 'assets', 'pages', 'merges']],
  ['Review', ['review-tasks', 'project-amenities', 'candidates', 'facts']],
  ['Organizations & access', ['organizations', 'users', 'platform-roles', 'organization-roles', 'memberships']],
  ['Reference data', ['amenities', 'stations']],
  ['History & support', ['runs', 'outbox', 'audit', 'consents', 'requirements', 'recommendations']],
]
const display = (value: unknown) => value == null ? 'Unknown' : typeof value === 'object' ? JSON.stringify(value) : String(value)

function ReadableDetails({ value }: { value: unknown }) {
  if (value == null || typeof value !== 'object') return <span>{display(value)}</span>
  if (Array.isArray(value)) return value.length ? <ul>{value.map((item, index) => <li key={index}><ReadableDetails value={item} /></li>)}</ul> : <span>None recorded</span>
  const fields = value as Record<string, unknown>
  return <dl className="admin-record-details">{Object.entries(fields).filter(([key]) => !['id', 'versionToken'].includes(key) && !key.endsWith('Label')).map(([key, item]) => <div key={key}><dt>{key.replace(/([A-Z])/g, ' $1')}</dt><dd>{fields[key + 'Label'] ? String(fields[key + 'Label']) : item && typeof item === 'object' ? <details><summary>View evidence/details</summary><ReadableDetails value={item} /></details> : <ReadableDetails value={item} />}</dd></div>)}</dl>
}

export function AdminPortal() {
  const location = useLocation()
  const workspace = location.pathname.startsWith('/admin/project-workspace')
  const [menuOpen, setMenuOpen] = useState(false)
  useEffect(() => { document.title = 'Administration | MMR Homefinder' }, [location.pathname])
  const [session, setSession] = useState<Session | null>(null), [ready, setReady] = useState(false), [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    const scope = new AbortController()
    api<Session>(`${base}/session`, { signal: scope.signal }).then(setSession).catch(e => { if (!scope.signal.aborted && !(e instanceof ApiError && [401, 403].includes(e.status))) setError(errorMessage(e)) }).finally(() => { if (!scope.signal.aborted) setReady(true) })
    return () => scope.abort()
  }, [revision])
  useEffect(() => onSessionExpired(e => { if (e.status === 401) { cancelSessionRequests(); setSession(null) } }), [])
  async function logout() {
    if (hasUnsavedChanges() && !window.confirm('Discard unsaved entries and sign out?')) return
    try { await api(`${base}/logout`, { method: 'POST' }); cancelSessionRequests(); setSession(null) } catch (e) { setError(errorMessage(e)) }
  }
  if (!ready) return <main className="builder-main" role="status">Loading administration…</main>
  if (!session) return <div className="builder-portal"><AdminLogin onSuccess={() => { setError(''); setRevision(n => n + 1) }} />{error && <p role="alert">{error}</p>}</div>
  const resource = location.pathname.match(/^\/admin\/records\/([^/]+)/)?.[1]
  const has = (permission: string) => session.permissions.includes(`platform_api.${permission}`)
  return <div className="builder-portal admin-portal"><a className="skip-link" href="#admin-main">Skip to administration</a>
    <header className="builder-header"><Link className="builder-brand" to="/admin/">MMR Homefinder · Administration</Link><nav aria-label="Admin account">{workspace && <button className="secondary" aria-expanded={menuOpen} aria-controls="admin-navigation" onClick={() => setMenuOpen(open => !open)}>Administration menu</button>}<ThemeControl /><span>{session.username}</span><Link to="/admin/password/">Change password</Link><button className="secondary" onClick={() => void logout()}>Sign out</button></nav></header>
    <div className={`admin-shell${workspace ? ' is-workspace' : ''}${menuOpen ? ' menu-open' : ''}`}><aside id="admin-navigation" className="admin-navigation"><nav aria-label="Administration">
      <Link to="/admin/">Overview</Link>{has('change_project') && <Link to="/admin/project-workspace/">Project workspace</Link>}{['view_project', 'view_operation', 'view_outboxmessage'].every(has) && <Link to="/admin/queue/">Processing queue</Link>}{(has('view_featureflag') || has('change_featureflag')) && <Link to="/admin/flags/">Feature flags</Link>}<Link to="/admin/operations/">Integrations & metrics</Link>
      {sections.map(([label, keys]) => <div key={label}><h2>{label}</h2>{keys.flatMap(key => { const item = session.resources.find(item => item.key === key); return item ? [<Link key={key} to={`/admin/records/${key}/`}>{item.label}</Link>] : [] })}</div>)}
    </nav></aside><main id="admin-main" tabIndex={-1} className="admin-main">
      {error && <p role="alert">{error}</p>}
      {location.pathname.startsWith('/admin/project-workspace') ? <AdminWorkspace embedded /> : resource ? <ResourceScreen key={resource} resource={resource} label={session.resources.find(item => item.key === resource)?.label || 'Records'} />
        : location.pathname.startsWith('/admin/queue') ? <QueueScreen /> : location.pathname.startsWith('/admin/password') ? <PasswordScreen />
        : location.pathname.startsWith('/admin/flags') ? <FlagsScreen /> : location.pathname.startsWith('/admin/operations') ? <OperationsScreen />
        : location.pathname === '/admin/' || location.pathname === '/admin' || location.pathname.startsWith('/admin/login') ? <section className="builder-panel"><h1>Administration</h1><p>Review evidence, manage projects and monitor processing from one workspace.</p><div className="admin-overview">{session.resources.map(item => <Link className="builder-panel" key={item.key} to={`/admin/records/${item.key}/`}>{item.label}</Link>)}</div></section>
        : <section className="builder-panel"><h1>Page unavailable</h1><p>This administration page does not exist.</p><Link to="/admin/">Return to overview</Link></section>}
    </main></div></div>
}

function AdminLogin({ onSuccess }: { onSuccess: () => void }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget); setBusy(true); setError('')
    try { await api(`${base}/login`); await api(`${base}/login`, { method: 'POST', body: json({ username: data.get('username'), password: data.get('password') }) }); onSuccess() }
    catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  return <main className="builder-main"><section className="builder-panel builder-login"><h1>Administration sign in</h1><p>Use an active account with administration access.</p><ThemeControl />{error && <p role="alert">{error}</p>}<form onSubmit={event => void submit(event)}><label>Username<input name="username" autoComplete="username" required autoFocus /></label><label>Password<input name="password" type="password" autoComplete="current-password" required /></label><button className="primary" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button></form></section></main>
}

function ResourceScreen({ resource, label }: { resource: string; label: string }) {
  const [data, setData] = useState<Records | null>(null), [selected, setSelected] = useState<Row | null>(null), [creating, setCreating] = useState(false)
  const [query, setQuery] = useState(''), [locality, setLocality] = useState(''), [page, setPage] = useState(1), [revision, setRevision] = useState(0)
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [editorRevision, setEditorRevision] = useState(0)
  const [checked, setChecked] = useState<Row[]>([])
  const bulkRequest = useRef({ payload: '', key: '' })
  useUnsavedChanges(dirty)
  useEffect(() => {
    const record = new URLSearchParams(window.location.search).get('record')
    if (!record) return
    const scope = new AbortController()
    api<Row>(`${base}/records/${resource}/${encodeURIComponent(record)}`, { signal: scope.signal }).then(setSelected).catch(e => { if (!scope.signal.aborted) setError(errorMessage(e)) })
    return () => scope.abort()
  }, [resource])
  function choose(row: Row | null, create = false) {
    if (hasUnsavedChanges() && !window.confirm('Discard unsaved entries in this record?')) return
    setDirty(false); setSelected(row); setCreating(create); setEditorRevision(n => n + 1)
    const url = new URL(window.location.href)
    if (row) url.searchParams.set('record', row.id); else url.searchParams.delete('record')
    window.history.replaceState(null, '', url)
  }
  async function remove() {
    if (!selected || !window.confirm('Permanently delete this unused record? Records with dependencies will be retained.')) return
    try { await api(`${base}/records/${resource}/${selected.id}`, { method: 'DELETE', body: json({ confirm: true, versionToken: selected.versionToken }) }); choose(null); setRevision(n => n + 1) }
    catch (e) { setError(errorMessage(e)) }
  }
  async function bulk(action: 'retire' | 'reanalyse') {
    if (!checked.length || !window.confirm(`${action} ${checked.length} selected projects? Evidence is retained. Reanalysis explicitly queues processing and may incur model costs.`)) return
    const payload = json({ action, confirm: true, ids: checked.map(row => row.id), versions: Object.fromEntries(checked.map(row => [row.id, row.updatedAt])) })
    if (bulkRequest.current.payload !== payload) bulkRequest.current = { payload, key: crypto.randomUUID() }
    setBusy(true)
    try { const result = await api<{ updated: number }>(`${base}/bulk-projects`, { method: 'POST', headers: { 'Idempotency-Key': bulkRequest.current.key }, body: payload }); setNotice(`${result.updated} projects updated.`); setChecked([]); bulkRequest.current = { payload: '', key: '' }; setRevision(n => n + 1) }
    catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  useEffect(() => {
    const scope = new AbortController()
    api<Records>(`${base}/records/${resource}?${new URLSearchParams({ q: query, locality, page: String(page) })}`, { signal: scope.signal }).then(setData).catch(e => { if (!scope.signal.aborted) setError(errorMessage(e)) })
    return () => scope.abort()
  }, [resource, query, locality, page, revision])
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!data) return
    const form = new FormData(event.currentTarget), values: Record<string, unknown> = {}
    for (const field of data.schema) {
      const value = form.get(field.name)
      try { values[field.name] = field.multiple ? form.getAll(field.name) : field.kind === 'boolean' ? value === 'on' : field.kind === 'json' ? JSON.parse(String(value || '{}')) : field.kind === 'number' ? value === '' ? null : value : value }
      catch { setError(`${field.label}: enter valid JSON.`); return }
    }
    setBusy(true); setError(''); setNotice('')
    try { const saved = await api<Row>(`${base}/records/${resource}${creating ? '' : '/' + selected?.id}`, { method: 'POST', body: json({ values, versionToken: selected?.versionToken }) }); setDirty(false); setSelected(saved); setCreating(false); setNotice('Changes saved.'); setRevision(n => n + 1) }
    catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  const columns = data?.results.length ? Object.keys(data.results[0]).filter(key => !['id', 'versionToken', 'updatedAt'].includes(key) && !key.endsWith('Label')).slice(0, 4) : []
  return <section className="builder-panel"><h1>{label}</h1><div className="admin-toolbar"><label>{resource === 'projects' ? 'Find a project' : 'Search by name or text'}<input type="search" value={query} onChange={event => { setQuery(event.target.value); setPage(1) }} /></label>
    {resource === 'projects' && <label>Locality<select value={locality} onChange={event => { setLocality(event.target.value); setPage(1) }}><option value="">All localities</option>{data?.localities?.map(place => <option key={place}>{place}</option>)}</select></label>}
    <button className="secondary" onClick={() => setRevision(n => n + 1)}>Refresh</button>{data?.canCreate && <button className="primary" onClick={() => choose(null, true)}>New record</button>}</div>
    {resource === 'projects' && data?.canAct && <div className="button-row"><span>{checked.length} selected (up to 100)</span><button className="secondary" disabled={busy || !checked.length} onClick={() => void bulk('retire')}>Retire selected</button><button className="secondary" disabled={busy || !checked.length} onClick={() => void bulk('reanalyse')}>Queue selected for reanalysis</button></div>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{!data ? <p role="status">Loading records…</p> : <><div className="admin-list-detail"><div className="admin-table"><table><caption>{data.count} records</caption><thead><tr>{columns.map(key => <th key={key}>{key.replace(/([A-Z])/g, ' $1')}</th>)}<th>Inspect</th></tr></thead><tbody>
      {data.results.map(row => <tr key={row.id}>{columns.map(key => <td key={key}>{display(row[key + 'Label'] ?? row[key])}</td>)}<td>{resource === 'projects' && data.canAct && <input type="checkbox" aria-label={`Select ${row.submittedName}`} checked={checked.some(item => item.id === row.id)} disabled={checked.length >= 100 && !checked.some(item => item.id === row.id)} onChange={event => setChecked(items => event.target.checked ? [...items, row] : items.filter(item => item.id !== row.id))} />}<button className="secondary" aria-pressed={selected?.id === row.id} onClick={() => choose(row)}>Open</button></td></tr>)}
    </tbody></table>{!data.count && <p>No records match.</p>}<div className="button-row"><button className="secondary" disabled={page === 1} onClick={() => setPage(n => n - 1)}>Previous</button><span>Page {page}</span><button className="secondary" disabled={page * data.pageSize >= data.count} onClick={() => setPage(n => n + 1)}>Next</button></div></div>
    {(selected || creating) && <section className="admin-detail"><h2>{creating ? 'New record' : 'Record details'}</h2>{selected && <details open={!data.schema.length}><summary>Saved record and history</summary><ReadableDetails value={selected} /></details>}
      {selected && resource === 'projects' && data.canAct && <><Link className="secondary" to={`/admin/project-workspace/?${new URLSearchParams({ project: selected.id, organization: String(selected.organization) })}`}>Open project workspace</Link><ProjectActions key={selected.id} project={selected} onSaved={() => setRevision(n => n + 1)} /></>}
      {selected && resource === 'review-tasks' && data.canAct && <TaskActions key={selected.id} task={selected} onSaved={() => { setSelected(null); setRevision(n => n + 1) }} />}
      {data.schema.length > 0 && (creating ? data.canCreate : data.canUpdate) && <form key={`${creating ? 'new' : selected?.id}-${selected?.versionToken}-${editorRevision}`} onChange={() => setDirty(true)} onSubmit={event => void save(event)}><fieldset disabled={busy}>
        <div className="builder-form-grid">{data.schema.map(field => <label key={field.name}>{field.label}{field.kind === 'select' ? <select name={field.name} multiple={field.multiple} required={field.required} defaultValue={field.multiple ? (selected?.[keyFor(field.name)] as string[] | undefined)?.map(String) ?? [] : String(selected?.[keyFor(field.name)] ?? '')}><option value="">Choose…</option>{field.options?.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
          : field.kind === 'json' ? <textarea name={field.name} rows={4} defaultValue={JSON.stringify(selected?.[keyFor(field.name)] ?? {}, null, 2)} /> : field.kind === 'boolean' ? <input type="checkbox" name={field.name} defaultChecked={!!selected?.[keyFor(field.name)]} /> : <input name={field.name} required={field.required} type={field.kind === 'number' ? 'number' : 'text'} step="any" defaultValue={String(selected?.[keyFor(field.name)] ?? '')} />}</label>)}</div>
        <button className="primary">{busy ? 'Saving…' : 'Save record'}</button></fieldset></form>}
      {selected && resource === 'users' && data.canUpdate && <PasswordScreen key={`${selected.id}-${editorRevision}`} userId={selected.id} />}
      {selected && data.canAct && (resource === 'documents' || resource === 'outbox') && <ProcessingAction key={selected.id} resource={resource} row={selected} onSaved={() => setRevision(n => n + 1)} />}
      {selected && data.canUpdate && ['platform-roles', 'organization-roles'].includes(resource) && <RoleMembers key={`${selected.id}-${editorRevision}`} resource={resource} roleId={selected.id} />}
      {selected && data.canDelete && <button className="secondary" disabled={busy} onClick={() => void remove()}>Delete unused record</button>}
    </section>}</div></>}
  </section>
}

function ProcessingAction({ resource, row, onSaved }: { resource: string; row: Row; onSaved: () => void }) {
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [notice, setNotice] = useState('')
  const requestKey = useRef(crypto.randomUUID())
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const reasonCode = new FormData(event.currentTarget).get('reason')
    if (!window.confirm(resource === 'documents' ? 'Queue document reprocessing? This may incur model costs.' : 'Replay this outbox message as a new event?')) return
    setBusy(true); setError('')
    try { await api(`${base}/${resource}/${row.id}/${resource === 'documents' ? 'retry' : 'replay'}`, { method: 'POST', headers: { 'Idempotency-Key': requestKey.current }, body: json({ reasonCode, confirm: true }) }); setNotice('Processing request queued.'); onSaved() }
    catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  return <form onSubmit={event => void submit(event)}><h3>{resource === 'documents' ? 'Reprocess document' : 'Replay outbox event'}</h3><label>Reason<input name="reason" required maxLength={64} /></label><button className="primary" disabled={busy || !!notice}>Confirm and queue</button>{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}</form>
}

function RoleMembers({ resource, roleId }: { resource: string; roleId: string }) {
  const [message, setMessage] = useState(''), [busy, setBusy] = useState(false)
  const [dirty, setDirty] = useState(false)
  useUnsavedChanges(dirty || busy)
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget
    const usernames = [...new Set(String(new FormData(form).get('usernames')).split(/[\s,]+/).filter(Boolean))]
    setBusy(true)
    try { const result = await api<{ assigned: number }>(`${base}/roles/${resource}/${roleId}/members`, { method: 'POST', body: json({ usernames }) }); setMessage(`${result.assigned} members assigned.`); form.reset(); setDirty(false) }
    catch (e) { setMessage(errorMessage(e)) } finally { setBusy(false) }
  }
  return <form onChange={() => setDirty(true)} onSubmit={event => void submit(event)}><h3>Onboard members</h3><label>Existing active usernames<textarea name="usernames" rows={3} required placeholder="One username per line, or separated by commas" /></label><p>Assign this role to up to 100 existing accounts. Account access and owner safeguards still apply.</p><button className="primary" disabled={busy}>Assign role</button><p role="status">{message}</p></form>
}

function PasswordScreen({ userId }: { userId?: string }) {
  const [message, setMessage] = useState(''), [busy, setBusy] = useState(false)
  const [dirty, setDirty] = useState(false)
  useUnsavedChanges(dirty || busy)
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; setBusy(true)
    try { await api(userId ? `${base}/users/${userId}/password` : `${base}/password`, { method: 'POST', body: json(Object.fromEntries(new FormData(form))) }); setMessage('Password changed.'); form.reset(); setDirty(false) }
    catch (e) { setMessage(errorMessage(e)) } finally { setBusy(false) }
  }
  return <section className="builder-panel"><h2>{userId ? 'Set account password' : 'Change your password'}</h2><form onChange={() => setDirty(true)} onSubmit={event => void submit(event)}>{!userId && <label>Current password<input name="old_password" type="password" required autoComplete="current-password" /></label>}<label>New password<input name={userId ? 'password1' : 'new_password1'} type="password" required autoComplete="new-password" /></label><label>Confirm new password<input name={userId ? 'password2' : 'new_password2'} type="password" required autoComplete="new-password" /></label><button className="primary" disabled={busy}>Change password</button><p role="status">{message}</p></form></section>
}

type Job = { id: string; project: string; projectId: string; label: string; kind: string; state: string; attempts: number; retryAt?: string; error?: string; canRequeue: boolean; canDelete: boolean; deleteVersion: string; progress: unknown }
type Queue = { updatedAt: string; canRun: boolean; capacity: { active: number; limit: number; available: number }; counts: Record<string, number>; jobs: Job[]; workers: { id: string; project: string; state: string; heartbeatAt: string }[] }
function QueueScreen() {
  const [data, setData] = useState<Queue | null>(null), [state, setState] = useState(''), [query, setQuery] = useState(''), [selected, setSelected] = useState<Job | null>(null)
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [revision, setRevision] = useState(0)
  useEffect(() => { const scope = new AbortController(); api<Queue>(`${base}/queue`, { signal: scope.signal }).then(setData).catch(e => { if (!scope.signal.aborted) setError(errorMessage(e)) }); return () => scope.abort() }, [revision])
  async function act(action: string, job?: Job) {
    if (!window.confirm(`${action === 'run' ? 'Run your queued evaluations? This can incur model costs.' : `${action} this dead-letter job?`}`)) return
    setBusy(true); setError('')
    try { await api(`${base}/queue`, { method: 'POST', body: json({ action, id: job?.id, version: job?.deleteVersion, confirm: true }) }); setSelected(null); setRevision(n => n + 1) }
    catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  return <section className="builder-panel"><h1>Processing queue</h1>{error && <p role="alert">{error}</p>}<div className="admin-toolbar"><label>Find a job<input type="search" value={query} onChange={e => setQuery(e.target.value)} /></label><label>State<select value={state} onChange={e => setState(e.target.value)}><option value="">All states</option>{['queued', 'running', 'retrying', 'completed', 'dead_letter'].map(s => <option key={s}>{s}</option>)}</select></label><button className="secondary" onClick={() => setRevision(n => n + 1)}>Refresh queue</button>{data?.canRun && <button className="primary" disabled={busy} onClick={() => void act('run')}>Run my queue</button>}</div>
    {!data ? <p role="status">Loading queue…</p> : <><p>Updated {new Date(data.updatedAt).toLocaleTimeString()} · Workers {data.capacity.active}/{data.capacity.limit} · {data.capacity.available} slots available</p><div className="button-row">{Object.entries(data.counts).map(([key, count]) => <span key={key}>{key}: {count}</span>)}</div>
      <div className="admin-list-detail"><div className="admin-table"><table><thead><tr><th>Project / item</th><th>Kind / state</th><th>Attempts</th><th>Details</th></tr></thead><tbody>{data.jobs.filter(job => (!state || job.state === state) && `${job.project} ${job.label} ${job.kind}`.toLowerCase().includes(query.toLowerCase())).map(job => <tr key={job.id}><td>{job.project}<br />{job.label}</td><td>{job.kind}<br />{job.state}</td><td>{job.attempts}</td><td><button className="secondary" onClick={() => setSelected(job)}>Inspect</button></td></tr>)}</tbody></table></div>
      {selected && <section className="admin-detail"><h2>{selected.label}</h2><p>{selected.error || 'No reported error'}</p><p>Retry: {selected.retryAt || 'Not scheduled'}</p><pre>{JSON.stringify(selected.progress, null, 2)}</pre><div className="button-row">{selected.canRequeue && <button className="primary" disabled={busy} onClick={() => void act('requeue', selected)}>Requeue</button>}{selected.canDelete && <button className="secondary" disabled={busy} onClick={() => void act('delete', selected)}>Delete dead letter</button>}</div></section>}</div>
      <h2>Worker status</h2><ul>{data.workers.map(worker => <li key={worker.id}>{worker.project} · {worker.state} · heartbeat {new Date(worker.heartbeatAt).toLocaleString()}</li>)}</ul>
    </>}
  </section>
}

function ProjectActions({ project, onSaved }: { project: Row; onSaved: () => void }) {
  const [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const [targets, setTargets] = useState<Row[]>([])
  const [targetSearch, setTargetSearch] = useState('')
  const [warning, setWarning] = useState<{ confirmationToken: string; total: number; failures: { label: string; error: string }[] } | null>(null)
  const pending = useRef<{ endpoint: string; values: Record<string, unknown>; key: string } | null>(null)
  useEffect(() => { const scope = new AbortController(); api<Records>(`${base}/records/projects?${new URLSearchParams({ organization: String(project.organization), q: targetSearch })}`, { signal: scope.signal }).then(data => setTargets(data.results.filter(item => item.id !== project.id && !['retired', 'merged'].includes(String(item.status))))).catch(e => { if (!scope.signal.aborted) setError(errorMessage(e)) }); return () => scope.abort() }, [project.id, project.organization, targetSearch])
  async function send(retryConfirmation?: string) {
    const request = pending.current
    if (!request) return
    setBusy(true); setError('')
    try { await api(request.endpoint, { method: 'POST', headers: { 'Idempotency-Key': request.key }, body: json({ ...request.values, ...(retryConfirmation ? { retryConfirmation } : {}) }) }); setWarning(null); onSaved() }
    catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.details && typeof e.details === 'object' && 'confirmationToken' in e.details) setWarning(e.details as NonNullable<typeof warning>)
      else setError(errorMessage(e))
    } finally { setBusy(false) }
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget); const action = (event.nativeEvent as SubmitEvent).submitter?.getAttribute('value')
    if (!window.confirm(`${action} this project? Evidence and history will be retained. Processing may incur model costs.`)) return
    const queue = action === 'run-queued' || action === 'fresh'
    pending.current = { endpoint: queue ? `${base}/queue` : `${base}/projects/${project.id}/actions`, key: crypto.randomUUID(),
      values: queue ? { action: action === 'fresh' ? 'fresh' : 'run', project: project.id, confirm: true } : { action, targetProjectId: form.get('targetProjectId'), reasonCode: form.get('reason'), expectedFactSetVersion: Number(project.publishedFactSetVersion) + 1, confirm: true } }
    setWarning(null); await send()
  }
  return <form onSubmit={event => void submit(event)}><h3>Project actions</h3>{error && <p role="alert">{error}</p>}<label>Reason<input name="reason" required maxLength={64} /></label><label>Find merge target<input type="search" value={targetSearch} onChange={event => setTargetSearch(event.target.value)} /></label><label>Merge into project (merge only)<select name="targetProjectId"><option value="">Choose canonical project</option>{targets.map(target => <option key={target.id} value={target.id}>{String(target.submittedName)}</option>)}</select></label><div className="button-row">{[['retire', 'Retire'], ['reanalyse', 'Queue document reanalysis'], ['merge', 'Merge'], ['publish', 'Publish'], ['run-queued', 'Run queued evaluations'], ['fresh', 'Fresh project evaluations']].map(([action, label]) => <button className="secondary" key={action} value={action} disabled={busy}>{label}</button>)}</div>{warning && <div className="builder-confirm"><h4>Previous evaluations failed ({warning.total})</h4><ul>{warning.failures.map((failure, index) => <li key={index}>{failure.label}: {failure.error}</li>)}</ul><p>Fresh evaluation uses the current AI settings and may incur model costs.</p><button className="primary" type="button" disabled={busy} onClick={() => void send(warning.confirmationToken)}>Confirm fresh evaluation retry</button></div>}</form>
}

function TaskActions({ task, onSaved }: { task: Row; onSaved: () => void }) {
  const [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const [options, setOptions] = useState<{ assignees: { id: number; username: string }[]; candidates: { id: string; label: string; value: unknown; page: number | null; confidence: number }[] } | null>(null)
  useEffect(() => { const scope = new AbortController(); api<NonNullable<typeof options>>(`${base}/tasks/${task.id}/actions`, { signal: scope.signal }).then(setOptions).catch(e => { if (!scope.signal.aborted) setError(errorMessage(e)) }); return () => scope.abort() }, [task.id])
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget), action = (event.nativeEvent as SubmitEvent).submitter?.getAttribute('value'); setBusy(true)
    try { await api(`${base}/tasks/${task.id}/actions`, { method: 'POST', body: json({ action, expectedVersion: task.version, reasonCode: form.get('reason'), selectedCandidateId: form.get('candidate') || undefined, correctedValue: form.get('corrected') ? JSON.parse(String(form.get('corrected'))) : undefined, assignedTo: form.get('assignedTo') || null, priority: Number(form.get('priority')) }) }); onSaved() }
    catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  const specialized = ['external_sources', 'floor_plan', 'publication'].includes(String(task.taskType))
  return <form onSubmit={event => void submit(event)}><h3>Review task action</h3>{error && <p role="alert">{error}</p>}{specialized && <p>Review this item in its project workspace. Assignment and priority can be changed here.</p>}<label>Reason<input name="reason" required maxLength={64} /></label>{!specialized && <><label>Evidence candidate<select name="candidate" defaultValue={String(task.candidate ?? '')}><option value="">Choose candidate or enter correction</option>{options?.candidates.map(candidate => <option key={candidate.id} value={candidate.id}>{candidate.label} · {display(candidate.value)} · page {candidate.page ?? 'unknown'}</option>)}</select></label><label>Corrected value (JSON)<textarea name="corrected" /></label></>}<label>Assign to administrator<select name="assignedTo" defaultValue={String(task.assignedTo ?? '')}><option value="">Unassigned</option>{options?.assignees.map(user => <option key={user.id} value={user.id}>{user.username}</option>)}</select></label><label>Priority<input name="priority" type="number" min="0" max="100" defaultValue={Number(task.priority)} /></label><div className="button-row">{(specialized ? ['assign'] : ['resolve', 'reject', 'reopen', 'assign']).map(action => <button key={action} className="secondary" disabled={busy} value={action}>{action}</button>)}</div></form>
}

function OperationsScreen() {
  const [data, setData] = useState<unknown>(null), [error, setError] = useState('')
  useEffect(() => { const scope = new AbortController(); Promise.all([api('/api/v1/admin/integration-health', { signal: scope.signal }), api('/api/v1/admin/operational-metrics', { signal: scope.signal })]).then(setData).catch(e => { if (!scope.signal.aborted) setError(errorMessage(e)) }); return () => scope.abort() }, [])
  return <section className="builder-panel"><h1>Integrations and metrics</h1><p>Provider configuration status and recorded operational metrics.</p>{error && <p role="alert">{error}</p>}{data ? <ReadableDetails value={data} /> : <p role="status">Loading operations…</p>}</section>
}

type Flag = { id: string; key: string; enabled: boolean; version: number; targetingRules: unknown; editable: boolean }
function FlagsScreen() {
  const [flags, setFlags] = useState<Flag[]>([]), [selected, setSelected] = useState<Flag | null>(null), [error, setError] = useState(''), [revision, setRevision] = useState(0)
  useEffect(() => { const scope = new AbortController(); collection<Flag>('/api/v1/admin/feature-flags/', scope.signal).then(setFlags).catch(e => { if (!scope.signal.aborted) setError(errorMessage(e)) }); return () => scope.abort() }, [revision])
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const values = new FormData(event.currentTarget)
    try { await api(`/api/v1/admin/feature-flags/${selected ? selected.id + '/' : ''}`, { method: selected ? 'PATCH' : 'POST', headers: selected ? { 'If-Match': `W/"${selected.version}"` } : {}, body: json({ ...(selected ? {} : { key: values.get('key') }), enabled: values.get('enabled') === 'on', targetingRules: JSON.parse(String(values.get('rules') || '{}')) }) }); setSelected(null); setRevision(n => n + 1) }
    catch (e) { setError(errorMessage(e)) }
  }
  return <section className="builder-panel"><h1>Feature flags</h1>{error && <p role="alert">{error}</p>}<ul>{flags.map(flag => <li key={flag.id}><button className="secondary" onClick={() => setSelected(flag)}>{flag.key} · {flag.enabled ? 'Enabled' : 'Disabled'}</button></li>)}</ul><form key={selected ? `${selected.id}-${selected.version}` : 'new'} onSubmit={event => void submit(event)}><h2>{selected ? 'Edit flag' : 'Create registered flag'}</h2><label>Key<input name="key" required readOnly={!!selected} defaultValue={selected?.key ?? ''} /></label><label>Enabled<input name="enabled" type="checkbox" defaultChecked={selected?.enabled} /></label><label>Targeting rules (JSON)<textarea name="rules" defaultValue={JSON.stringify(selected?.targetingRules ?? {}, null, 2)} /></label><button className="primary" disabled={!!selected && !selected.editable}>Save flag</button><button className="secondary" type="button" onClick={() => setSelected(null)}>New flag</button></form></section>
}
