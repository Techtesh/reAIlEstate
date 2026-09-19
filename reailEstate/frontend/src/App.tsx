import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { BrowserRouter, Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { ArrowRight, Building2, House, LogIn, LogOut, MapPin, X } from 'lucide-react'
import { api, ApiError, cancelSessionRequests, collection, json, onSessionExpired } from './api'
import { dateLabel, draftSchema, newDraft, placesSchema, resumeDraft, type Draft, type Profile, type Project, type Shortlist, type User } from './buyer'
import { Requirements, type AmenityOption } from './Requirements'
import { Browse, Matches } from './Browse'
import { ShortlistCommute } from './CommuteLocations'
import './App.css'

export const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'This action could not be completed.'
function initialDraft() {
  try { const stored = JSON.parse(sessionStorage.getItem('mmr-safe-draft') ?? '{}'); return resumeDraft({ home: stored.home, weights: stored.weights }) } catch { return newDraft() }
}
function BuyerApp() {
  const navigate = useNavigate(), location = useLocation()
  const [user, setUser] = useState<User | null>(null)
  const [booting, setBooting] = useState(true)
  const [draft, setDraft] = useState<Draft>(initialDraft)
  const [consent, setConsent] = useState(false)
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [shortlists, setShortlists] = useState<Shortlist[]>([])
  const [amenities, setAmenities] = useState<AmenityOption[]>([])
  const [loading, setLoading] = useState(false)
  const [catalogueError, setCatalogueError] = useState('')
  const [publicLoading, setPublicLoading] = useState(true)
  const [publicError, setPublicError] = useState('')
  const [reload, setReload] = useState(0)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [saveStatus, setSaveStatus] = useState('Not saved to your account')
  const [loginOpen, setLoginOpen] = useState(false)
  const [afterLogin, setAfterLogin] = useState<'submit' | 'save' | ''>('')
  const [conflict, setConflict] = useState<Profile | null>(null)
  const activeProfile = useRef<Profile | null>(null)
  const savedPayload = useRef('')
  const saveFlight = useRef<Promise<Profile> | null>(null)
  const consentGranted = useRef(false)
  const submitKey = useRef<string | null>(null)
  const [lastMatch, setLastMatch] = useState('')
  const sessionExpired = useEffectEvent(handleError)
  useEffect(() => onSessionExpired(sessionExpired), [])
  useEffect(() => { try { sessionStorage.setItem('mmr-safe-draft', json({ home: draft.home, weights: draft.weights })) } catch {} }, [draft.home, draft.weights])
  useEffect(() => {
    let active = true
    async function bootstrap() {
      try { await api('/api/v1/auth/login'); const account = await api<User>('/api/v1/auth/me'); if (active) setUser(account) }
      catch (error) { if (active && !(error instanceof ApiError && [401, 403].includes(error.status))) setMessage(errorMessage(error)) }
      finally { if (active) setBooting(false) }
    }
    void bootstrap()
    return () => { active = false }
  }, [])
  useEffect(() => {
    if (booting) return
    const controller = new AbortController()
    setPublicLoading(true); setPublicError('')
    collection<Project>('/api/v1/projects/', controller.signal).then(catalogue => { if (!controller.signal.aborted) setProjects(catalogue) }).catch(error => { if (!controller.signal.aborted) setPublicError(errorMessage(error)) }).finally(() => { if (!controller.signal.aborted) setPublicLoading(false) })
    return () => controller.abort()
  }, [booting, user, reload])
  useEffect(() => {
    const controller = new AbortController()
    if (!user) return () => controller.abort()
    setLoading(true); setCatalogueError('')
    try { setLastMatch(sessionStorage.getItem(`mmr-match-${user.id}`) ?? '') } catch { setLastMatch('') }
    Promise.all([
      collection<Profile>('/api/v1/requirement-profiles/', controller.signal),
      collection<Shortlist>('/api/v1/shortlists/', controller.signal), api<AmenityOption[]>('/api/v1/amenities', { signal: controller.signal }),
    ]).then(([requirements, lists, taxonomy]) => { if (!controller.signal.aborted) { setProfiles(requirements.filter(profile => profile.status !== 'deleted')); setShortlists(lists); setAmenities(taxonomy) } }).catch(error => { if (!controller.signal.aborted) setCatalogueError(errorMessage(error)) }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [user, reload])
  useEffect(() => {
    if (booting) return
    document.title = `${location.pathname === '/requirements' ? 'My requirements' : location.pathname.startsWith('/matches/') ? 'My matches' : location.pathname === '/shortlist' ? 'Shortlist' : 'MMR homes'} | MMR Homefinder`
    document.querySelector<HTMLElement>('main h1')?.focus()
    window.scrollTo({ top: 0, behavior: 'instant' })
  }, [location.pathname, booting])
  const autosave = useEffectEvent(() => { void persist(draft).catch(handleError) })
  useEffect(() => {
    if (!user || !consent || conflict || busy || !draftSchema.safeParse(draft).success || location.pathname !== '/requirements') return
    if (savedPayload.current === json(draft)) return
    const timer = window.setTimeout(autosave, 1000)
    return () => window.clearTimeout(timer)
  }, [user, consent, draft, conflict, busy, location.pathname])
  function clearPrivateState() {
    cancelSessionRequests()
    saveFlight.current = null
    setBusy(false); setLoading(false); setCatalogueError('')
    setDraft(newDraft()); setConsent(false); setProfiles([]); setProjects([]); setShortlists([]); setAmenities([]); setConflict(null)
    activeProfile.current = null; savedPayload.current = ''; consentGranted.current = false; submitKey.current = null; setLastMatch(''); setSaveStatus('Not saved to your account')
    try { sessionStorage.removeItem('mmr-safe-draft') } catch {}
  }
  function handleError(error: unknown) {
    if (error instanceof DOMException && error.name === 'AbortError') return
    if (error instanceof ApiError && [401, 403].includes(error.status)) {
      const safeHome = draft.home, safeWeights = draft.weights
      clearPrivateState(); setDraft({ ...newDraft(), home: safeHome, weights: safeWeights }); setUser(null); setAfterLogin(''); setLoginOpen(true)
      setMessage('Your session could not be verified. Sensitive local entries were cleared; saved profiles can be resumed after sign-in.')
    } else setMessage(errorMessage(error))
  }
  async function persist(payload: Draft): Promise<Profile> {
    if (saveFlight.current) { await saveFlight.current; return persist(payload) }
    if (!user || !consent) throw new Error('Sign in and give consent before saving your requirements.')
    if (conflict) throw new Error('Resolve the version conflict before saving.')
    if (activeProfile.current?.status !== 'draft' && activeProfile.current) activeProfile.current = null
    const flight = (async () => {
      setSaveStatus('Saving...')
      if (!consentGranted.current) {
        await api('/api/v1/consents/', { method: 'POST', body: json({ purpose: 'saved_profile', policyVersion: '1.0', grantedAt: new Date().toISOString() }) })
        consentGranted.current = true
      }
      if (!activeProfile.current) activeProfile.current = await api<Profile>('/api/v1/requirement-profiles/', { method: 'POST', body: '{}' })
      if (savedPayload.current === json(payload)) { setSaveStatus(`Saved privately | version ${activeProfile.current.version}`); return activeProfile.current }
      try {
        const saved = await api<Profile>(`/api/v1/requirement-profiles/${activeProfile.current.id}/`, { method: 'PATCH', headers: { 'If-Match': `W/"${activeProfile.current.version}"` }, body: json(payload) })
        activeProfile.current = saved; savedPayload.current = json(payload); setSaveStatus(`Saved privately | version ${saved.version}`)
        setProfiles(current => [saved, ...current.filter(item => item.id !== saved.id)])
        return saved
      } catch (error) {
        setSaveStatus('Not saved')
        if (error instanceof ApiError && error.status === 409) setConflict(await api<Profile>(`/api/v1/requirement-profiles/${activeProfile.current.id}/`))
        throw error
      }
    })()
    saveFlight.current = flight
    try { return await flight } finally { if (saveFlight.current === flight) saveFlight.current = null }
  }
  async function submit() {
    setMessage('')
    if (!draftSchema.safeParse(draft).success || !consent) { setMessage('Complete your requirements and consent before matching.'); return }
    if (!user) { setAfterLogin('submit'); setLoginOpen(true); return }
    setBusy(true)
    try {
      const profile = await persist(draft)
      submitKey.current ??= crypto.randomUUID()
      const result = await api<{ recommendationRequestId: string; operationId: string }>(`/api/v1/requirement-profiles/${profile.id}:submit`, { method: 'POST', headers: { 'If-Match': `W/"${profile.version}"`, 'Idempotency-Key': submitKey.current }, body: '{}' })
      activeProfile.current = { ...profile, status: 'submitted' }; submitKey.current = null
      setProfiles(current => current.map(item => item.id === profile.id ? { ...item, status: 'submitted' } : item))
      const destination = `/matches/${result.recommendationRequestId}?operation=${result.operationId}`
      setLastMatch(destination)
      try { sessionStorage.setItem(`mmr-match-${user.id}`, destination) } catch {}
      navigate(destination)
    } catch (error) { handleError(error) } finally { setBusy(false) }
  }
  async function saveAndExit() {
    if (!user) { setAfterLogin('save'); setLoginOpen(true); return }
    if (!consent) { setMessage('Give consent in Priorities & review before saving private information.'); return }
    setBusy(true)
    try { await persist(draft); navigate('/profiles') } catch (error) { handleError(error) } finally { setBusy(false) }
  }
  async function saveDestinations() {
    const validation = placesSchema.safeParse(draft.places)
    if (!validation.success) { setMessage(validation.error.issues.map(issue => issue.message).join(' ')); return }
    setBusy(true); setMessage('')
    try { await persist(draft) } catch (error) { handleError(error) } finally { setBusy(false) }
  }
  function loadProfile(profile: Profile) {
    activeProfile.current = profile.status === 'draft' ? profile : null
    const loaded = resumeDraft(profile.sections)
    setDraft(loaded); savedPayload.current = profile.status === 'draft' ? json(loaded) : ''
    submitKey.current = null; setConsent(false); consentGranted.current = false
    setSaveStatus(profile.status === 'draft' ? `Saved privately | version ${profile.version}` : 'New draft based on submitted requirements')
  }
  async function toggleShortlist(project: Project) {
    if (!user) { setLoginOpen(true); return }
    setBusy(true); setMessage('')
    try {
      const containing = shortlists.filter(list => list.entries.some(entry => entry.project === project.id))
      if (containing.length) {
        for (const list of containing) await api(`/api/v1/shortlists/${list.id}/projects`, { method: 'DELETE', body: json({ projectId: project.id }) })
        setShortlists(current => current.map(list => ({ ...list, entries: list.entries.filter(entry => entry.project !== project.id) })))
      } else {
        const list = shortlists[0] ?? await api<Shortlist>('/api/v1/shortlists/', { method: 'POST', body: json({ name: 'My homes' }) })
        const saved = await api<Shortlist>(`/api/v1/shortlists/${list.id}/projects`, { method: 'POST', body: json({ projectId: project.id }) })
        setShortlists(current => [saved, ...current.filter(item => item.id !== saved.id)])
      }
    } catch (error) { handleError(error) } finally { setBusy(false) }
  }
  function startNew() { activeProfile.current = null; savedPayload.current = ''; submitKey.current = null; setDraft(newDraft()); setConsent(false); consentGranted.current = false; setSaveStatus('Not saved to your account'); navigate('/requirements') }
  const savedIds = [...new Set(shortlists.flatMap(list => list.entries.map(entry => entry.project)))]
  const browseProps = { projects, user, loading: publicLoading, error: publicError, onRetry: () => setReload(value => value + 1), savedIds, busy, toggleShortlist, onSignIn: () => setLoginOpen(true) }
  const commutePanel = user ? <ShortlistCommute places={draft.places} onChange={places => { submitKey.current = null; setDraft({ ...draft, places }) }} profiles={profiles} onLoad={loadProfile} unsaved={json(draft) !== (savedPayload.current || json(newDraft()))} disabled={busy || loading || !!conflict} consent={consent} setConsent={setConsent} onSave={() => void saveDestinations()} saveStatus={saveStatus} loadError={catalogueError} onRetry={() => setReload(value => value + 1)} /> : null
  return <>
    <a className="skip-link" href="#main">Skip to content</a>
    <header className="site-header"><Link to="/" className="brand"><House size={27} strokeWidth={1.7} /><span>MMR<span className="brand-light">Homefinder</span></span></Link><nav aria-label="Buyer navigation"><NavLink to="/" end>Discover</NavLink><NavLink to="/browse">Browse</NavLink><NavLink to="/profiles">My requirements</NavLink><NavLink to={lastMatch || '/recommendations'}>My matches</NavLink><NavLink to="/shortlist">Shortlist<span className="nav-count">{savedIds.length}</span></NavLink></nav><div className="account">{user ? <><span>{user.username}</span><button className="icon-button" title="Sign out" aria-label="Sign out" disabled={busy} onClick={async () => { try { await api('/api/v1/auth/logout', { method: 'POST' }); clearPrivateState(); setUser(null); navigate('/') } catch (error) { handleError(error) } }}><LogOut size={19} /></button></> : <button className="secondary" onClick={() => { setAfterLogin(''); setLoginOpen(true) }}><LogIn size={17} />Sign in</button>}</div></header>
    {message && <div className="global-message" role="alert"><span>{message}</span><button className="icon-button" aria-label="Dismiss notification" title="Dismiss" onClick={() => setMessage('')}><X size={18} /></button></div>}
    {conflict && ['/requirements', '/shortlist'].includes(location.pathname) && <section className="conflict page-width" role="alert"><h2>Another session changed this draft</h2><p>Changed sections: {Object.keys(conflict.sections).filter(key => json(conflict.sections[key as keyof Draft]) !== json(draft[key as keyof Draft])).join(', ') || 'Profile status'}. No changes were overwritten.</p><div className="button-row"><button className="secondary" onClick={() => { activeProfile.current = conflict; const latest = resumeDraft(conflict.sections); setDraft(latest); savedPayload.current = json(latest); setConflict(null); setMessage(''); setSaveStatus('Loaded the latest saved version') }}>Use saved version</button><button className="secondary" onClick={() => { activeProfile.current = null; savedPayload.current = ''; setConflict(null); setMessage(''); setSaveStatus('Your edits will be saved as a new profile') }}>Keep my edits as a new profile</button></div></section>}
    {booting ? <main id="main" className="empty-state" role="status">Connecting to your workspace...</main> : location.pathname === '/browse' ? <Browse {...browseProps} title="Browse properties" eyebrow="Published homes across MMR" matches={[]} /> : location.pathname === '/requirements' ? <>
      <Requirements draft={draft} setDraft={value => { submitKey.current = null; setDraft(value) }} consent={consent} setConsent={setConsent} signedIn={!!user} busy={busy} saveStatus={saveStatus} onSave={() => void saveAndExit()} onSubmit={() => void submit()} amenities={amenities} />
    </> : location.pathname === '/profiles' ? <main id="main" className="page-width profile-page"><div className="page-heading"><div><p className="eyebrow">Private to your account</p><h1 tabIndex={-1}>My requirements</h1></div><button className="primary" onClick={startNew}>New requirements<ArrowRight size={18} /></button></div>{!user ? <SignInPrompt onSignIn={() => setLoginOpen(true)} /> : loading ? <p role="status">Loading saved requirements...</p> : catalogueError ? <Empty title="Could not load requirements" detail={catalogueError} action="Retry" onAction={() => setReload(value => value + 1)} /> : profiles.length ? <div className="profile-list">{profiles.map(profile => <article key={profile.id}><div><span className="tag">{profile.status}</span><h2>{profile.sections.home?.configurations.join(', ') || 'Home requirements'}</h2><p>Version {profile.version} | {dateLabel(profile.updatedAt)}</p></div><button className="secondary" onClick={() => { loadProfile(profile); navigate('/requirements') }}>{profile.status === 'draft' ? 'Resume draft' : 'Use as new draft'}<ArrowRight size={18} /></button></article>)}</div> : <Empty title="Your requirements start here" detail="No saved profiles yet." action="Create requirements" onAction={startNew} />}</main> : location.pathname === '/privacy' ? <Privacy /> : location.pathname.startsWith('/matches/') ? <Matches key={location.pathname} {...browseProps} /> : location.pathname === '/shortlist' ? <Browse {...browseProps} title="Your shortlisted projects" eyebrow="The homes worth a closer look" projects={projects.filter(project => savedIds.includes(project.id))} matches={[]} shortlist commutePlaces={draft.places} commutePanel={commutePanel} /> : location.pathname === '/recommendations' ? <main id="main" className="page-width"><Empty title="Find your project matches" detail="Submit your requirements to receive a ranked, evidence-based score sheet." action="My requirements" onAction={() => navigate('/requirements')} /></main> : location.pathname === '/' ? <>
      <section className="home-intro"><div><p className="eyebrow"><MapPin size={14} />Mumbai Metropolitan Region</p><h1 tabIndex={-1}>MMR homes</h1><p>A home that fits your everyday life.</p></div><div className="home-start"><span>New & under-construction homes</span><Link className="primary" to="/requirements">Find my matches<ArrowRight size={19} /></Link></div></section>
      <Browse {...browseProps} title="Discover published projects" eyebrow="Explore the neighbourhoods" matches={[]} />
    </> : <main id="main"><Empty title="Page not found" detail="This address is not available." action="Discover homes" onAction={() => navigate('/')} /></main>}
    <footer className="site-footer"><span>MMR Homefinder</span><span>Evidence-backed discovery. Not a binding offer.</span><Link to="/privacy">Privacy</Link></footer>
    {loginOpen && <Login onClose={() => { setLoginOpen(false); setAfterLogin('') }} onSuccess={account => { setUser(account); setLoginOpen(false); setMessage(afterLogin === 'submit' ? 'Signed in. Review your requirements, then choose Find my matches.' : afterLogin === 'save' ? 'Signed in. Give consent and choose Save & exit.' : ''); setAfterLogin('') }} />}
  </>
}

function Login({ onClose, onSuccess }: { onClose: () => void; onSuccess: (user: User) => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [error, setError] = useState(''), [busy, setBusy] = useState(false)
  useEffect(() => { const node = dialog.current; node?.showModal(); return () => node?.close() }, [])
  return <dialog ref={dialog} className="login-dialog" onCancel={onClose} aria-labelledby="login-title"><button className="close-dialog icon-button" aria-label="Close sign in" title="Close" disabled={busy} onClick={onClose}><X size={20} /></button><LogIn size={28} /><h2 id="login-title">Welcome back</h2><p>Sign in with your workspace account.</p>{error && <p role="alert" className="field-error">{error}</p>}<form onSubmit={async event => {
    event.preventDefault(); const form = event.currentTarget, data = new FormData(form); setBusy(true); setError('')
    try { await api('/api/v1/auth/login'); const account = await api<User>('/api/v1/auth/login', { method: 'POST', body: json({ username: data.get('username'), password: data.get('password') }) }); form.reset(); onSuccess(account) }
    catch (failure) { setError(failure instanceof ApiError && failure.status === 403 ? 'Sign-in failed. Check your username and password.' : errorMessage(failure)) }
    finally { setBusy(false) }
  }}><label>Username<input name="username" autoComplete="username" required autoFocus /></label><label>Password<input name="password" type="password" autoComplete="current-password" required /></label><button className="primary" disabled={busy}>{busy ? 'Signing in...' : 'Sign in'}<ArrowRight size={18} /></button></form><p className="data-note">Need an account? Contact the workspace administrator.</p></dialog>
}
export function Empty({ title, detail, action, onAction }: { title: string; detail: string; action?: string; onAction?: () => void }) { return <div className="empty-state"><Building2 size={36} strokeWidth={1.3} /><h2>{title}</h2><p>{detail}</p>{action && <button className="primary" onClick={onAction}>{action}<ArrowRight size={18} /></button>}</div> }
export function SignInPrompt({ onSignIn }: { onSignIn: () => void }) { return <Empty title="A private place for your home search" detail="Sign in for saved requirements, personalized matches and shortlists." action="Sign in" onAction={onSignIn} /> }
function Privacy() { return <main id="main" className="page-width prose"><p className="eyebrow">Policy 1.0</p><h1 tabIndex={-1}>Your information stays yours</h1><h2>Requirements and recommendations</h2><p>With your consent, your account saves your home requirements, budget, destinations (including exact coordinates when supplied) and priorities to calculate personalized project matches. They are not public listings. Your destination markers appear only on your shortlist map.</p><h2>Guest drafts</h2><p>Budgets, destinations and additional notes stay in tab memory until saved. Closing or refreshing the tab discards these sensitive guest entries. Home configurations and scoring weights can remain in session storage until the tab closes.</p><h2>Map data</h2><p>Project and station markers come from Homefinder's public catalogue. User-added station candidates and saved references do not establish operating status; unverified records are labelled accordingly.</p><p>The map loads OpenStreetMap tiles. The tile provider receives your IP address and requested map area, which may include areas around your destinations, but not destination names or your requirement profile. Map tiles are subject to OpenStreetMap's privacy and usage policies. No Google map is embedded.</p><p>Choosing Look up location sends your search, Plus Code or Google Maps share link through our backend to Google as needed. This happens only on your request, not when opening the map. Entering coordinates directly does not call Google. Google Maps privacy and usage terms apply to these lookups.</p><h2>Control and retention</h2><p>You can sign out at any time. Saved records remain in your account under the backend's retention policy. Contact the workspace administrator for consent withdrawal, account export or deletion. Signing out clears private information from this view.</p><h2>Decision support, not an offer</h2><p>Match scores are specific to your requirements. Prices, availability, commute estimates and registry status may change. MahaRERA information is not legal certification. Adding destination markers does not calculate travel times.</p></main> }
export default function App() { return <BrowserRouter><BuyerApp /></BrowserRouter> }