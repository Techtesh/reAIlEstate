import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { ArrowRight, Building2, Heart, House, List, Map as MapIcon, MapPin, RefreshCw, ShieldCheck, SlidersHorizontal, Table2, X } from 'lucide-react'
import { api, collection, json } from './api'
import { componentLabel, configurationArea, coordinates, dateLabel, projectPrice, projectStatusLabel, reasonLabel, type CommutePlace, type Match, type Project, type Snapshot, type User } from './buyer'
import { errorMessage } from './errors'
import { ProjectMap } from './ProjectMap'
import { ProjectGallery } from './ProjectGallery'
import { possessionSortKey, sortProjects } from './projectSort'
import { Empty, SignInPrompt } from './SharedUi'

export type CatalogueProps = { projects: Project[]; sortMatches: Match[]; user: User | null; loading: boolean; error: string; onRetry: () => void; savedIds: string[]; busy: boolean; toggleShortlist: (project: Project) => Promise<void>; onSignIn: () => void; guestTrial?: boolean }
type BrowseProps = CatalogueProps & { title: string; eyebrow: string; matches: Match[]; shortlist?: boolean; snapshot?: Snapshot; commutePlaces?: CommutePlace[]; commutePanel?: ReactNode; ribbon?: ReactNode }
export function Browse(props: BrowseProps) { return <BrowseSession key={props.user?.id ?? 'guest'} {...props} /> }
function ProjectPicture({ project }: { project: Project }) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  return project.projectPic && failedUrl !== project.projectPic
    ? <img className="project-picture" src={project.projectPic} alt={project.submittedName} loading="lazy" onError={() => setFailedUrl(project.projectPic ?? null)} />
    : <><Building2 size={56} strokeWidth={.9} /><span>{project.submittedName}</span></>
}
function BrowseSession({ title, eyebrow, projects: publishedProjects, matches, sortMatches, user, loading, error, onRetry, savedIds, busy, toggleShortlist, onSignIn, shortlist, snapshot, commutePlaces, commutePanel, ribbon, guestTrial = false }: BrowseProps) {
  const [params, setParams] = useSearchParams()
  const pathname = useLocation().pathname
  const browsing = pathname === '/browse'
  const catalogueView = pathname === '/' || browsing
  const includeProcessing = browsing && params.get('includeProcessing') === 'true'
  const [selected, setSelected] = useState('')
  const [detail, setDetail] = useState<Project | null>(null)
  const [compare, setCompare] = useState<string[]>([])
  const [comparisonOpen, setComparisonOpen] = useState(false)
  const [view, setView] = useState('split')
  const [compact, setCompact] = useState(() => window.innerWidth <= 760)
  const [focusRequest, setFocusRequest] = useState<{ projectId: string; version: number } | null>(null)
  const locality = params.get('locality') ?? '', configuration = params.get('configuration') ?? '', sort = params.get('sort') ?? 'match'
  const verified = params.get('verified') === 'true'
  const filterKey = JSON.stringify([locality, configuration, verified, includeProcessing])
  const [filterProjects, setFilterProjects] = useState<{ key: string; items: Project[] } | null>(null)
  const [filterPending, setFilterLoading] = useState(false)
  const [filterError, setFilterError] = useState('')
  const [filterRetry, setFilterRetry] = useState(0)
  useEffect(() => {
    const update = () => setCompact(window.innerWidth <= 760)
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [])
  function filter(key: string, value: string) { const next = new URLSearchParams(params); if (value) next.set(key, value); else next.delete(key); setParams(next, { replace: true }); setSelected(''); setFocusRequest(null) }
  useEffect(() => {
    const controller = new AbortController()
    if (!locality && !configuration && !verified && !includeProcessing) { setFilterProjects(null); setFilterLoading(false); setFilterError(''); return () => controller.abort() }
    const query = new URLSearchParams()
    if (locality) query.set('locality', locality)
    if (configuration) query.set('configuration', configuration)
    if (verified) query.set('mahaReraStatus', 'verified')
    if (includeProcessing) query.set('includeProcessing', 'true')
    setFilterLoading(true); setFilterError('')
    collection<Project>(`/api/v1/projects/?${query}`, controller.signal, items => { if (!controller.signal.aborted) setFilterProjects({ key: filterKey, items }) }).then(items => { if (!controller.signal.aborted) setFilterProjects({ key: filterKey, items }) }).catch(failure => { if (!controller.signal.aborted) setFilterError(errorMessage(failure)) }).finally(() => { if (!controller.signal.aborted) setFilterLoading(false) })
    return () => controller.abort()
  }, [locality, configuration, verified, includeProcessing, filterKey, user, filterRetry])
  const activeFilterProjects = filterProjects?.key === filterKey ? filterProjects.items : null
  const filterLoading = !activeFilterProjects && (filterPending || (includeProcessing && !filterError))
  const projects = includeProcessing ? activeFilterProjects ?? [] : publishedProjects
  const filtered = sortProjects(
    projects.filter(project => !activeFilterProjects || activeFilterProjects.some(item => item.id === project.id)),
    sort, matches, sortMatches,
  )
  const visible = filterLoading || filterError || error ? [] : filtered
  const sortNote = !catalogueView ? ''
    : sort === 'complete' ? 'Completion data is not available yet; projects remain in name order.'
    : sort === 'sold' ? 'Sold percentage is not available yet; projects remain in name order.'
    : sort === 'score' && !sortMatches.length ? 'Create a match report to sort by your score; projects remain in name order.'
    : sort === 'possession' && !filtered.some(project => possessionSortKey(project.possessionDate) < Infinity)
      ? 'No reviewed possession dates are available yet; projects remain in name order.' : ''
  function select(id: string) { setSelected(id); document.getElementById(`project-${id}`)?.scrollIntoView({ block: 'nearest', behavior: 'instant' }) }
  function focusOnMap(id: string) {
    setSelected(id)
    setFocusRequest(current => ({ projectId: id, version: (current?.version ?? 0) + 1 }))
    setView(window.innerWidth <= 760 ? 'map' : 'split')
  }
  return <main id="main" className={`browse view-${view}`}>
    <div className="search-bar"><div className="filter-control"><MapPin size={17} /><label className="sr-only" htmlFor="locality-filter">Locality</label><select id="locality-filter" value={locality} onChange={event => filter('locality', event.target.value)}><option value="">All MMR localities</option>{[...new Set(projects.map(project => project.locality).filter(Boolean)), ...(locality && !projects.some(project => project.locality === locality) ? [locality] : [])].sort().map(value => <option key={value}>{value}</option>)}</select></div><div className="filter-control"><House size={17} /><label className="sr-only" htmlFor="configuration-filter">Configuration</label><select id="configuration-filter" value={configuration} onChange={event => filter('configuration', event.target.value)}><option value="">Any configuration</option>{['1RK', '1BHK', '2BHK', '2.5BHK', '3BHK', '4BHK', '5BHK'].map(value => <option key={value}>{value}</option>)}</select></div><label className="check-label verification-filter"><input type="checkbox" checked={verified} onChange={event => filter('verified', event.target.checked ? 'true' : '')} />MahaRERA verified</label>{(locality || configuration || verified) && <button className="text-button" onClick={() => { const next = new URLSearchParams(params); ['locality', 'configuration', 'verified'].forEach(key => next.delete(key)); setParams(next) }}>Clear filters</button>}<div className="view-controls segmented" aria-label="Results view"><button title="Map and feed" aria-label="Map and feed" aria-pressed={view === 'split'} className="split-control" onClick={() => setView('split')}><SlidersHorizontal size={18} /></button><button title="Map" aria-label="Map view" aria-pressed={view === 'map'} onClick={() => setView('map')}><MapIcon size={18} /></button><button title="Feed" aria-label="Feed view" aria-pressed={view === 'feed'} onClick={() => setView('feed')}><List size={18} /></button>{snapshot && <button title="Score sheet" aria-label="Score sheet" aria-pressed={view === 'scores'} onClick={() => setView('scores')}><Table2 size={18} /></button>}</div></div>
    <div className="browse-heading"><div className="browse-summary"><h1 tabIndex={-1}>{title}</h1><p className="sr-only">{includeProcessing ? 'Published listings and unverified demo pins across MMR' : eyebrow}</p><p role="status">{loading || filterLoading ? 'Loading projects...' : filterPending ? `${visible.length} projects shown; loading more...` : `${visible.length} ${visible.length === 1 ? 'project' : 'projects'}${shortlist ? ' in your shortlist' : ' available'}`}</p></div>
      {browsing && <label className="check-label processing-filter"><input type="checkbox" checked={includeProcessing} onChange={event => filter('includeProcessing', event.target.checked ? 'true' : '')} />Show unverified demo pins</label>}
      <label className="sort-control">Sort by<select value={sort} onChange={event => filter('sort', event.target.value)}><option value="match">{matches.length ? 'Best match' : 'Project name'}</option><option value="price">Lowest known price</option>{matches.length > 0 && <option value="name">Project name</option>}{catalogueView && <><option value="possession">Possession (soonest)</option><option value="score">Match score</option><option value="complete">Most complete (coming soon)</option><option value="sold">Most sold% (coming soon)</option></>}</select></label>{sortNote && <p className="sort-note" role="status">{sortNote}</p>}</div>
    {shortlist && user && commutePanel}
    {snapshot && <div className="snapshot-meta"><span>Match for your requirements</span><span>Profile v{snapshot.profileVersion} | {snapshot.algorithmVersion} | {dateLabel(snapshot.generatedAt)}</span>{snapshot.matchingContext?.layoutHardMatches === false && <strong>Advisory matching: numeric hard limits are not enforced. Review each layout’s diagnostics.</strong>}{snapshot.expiresAt && new Date(snapshot.expiresAt) < new Date() && <strong>Snapshot expired. Submit a new draft for fresh matches.</strong>}</div>}
    {ribbon && <div className="map-only-ribbon">{ribbon}</div>}
    <div className="map-feed-layout">
      {view !== 'feed' && view !== 'scores' && !(compact && view === 'split') && <ProjectMap projects={visible} matches={matches} selected={selected} onSelect={select} onInspect={setDetail} commutePlaces={shortlist && user ? commutePlaces : undefined} shortlistedIds={shortlist ? savedIds : undefined} focusRequest={focusRequest} onFit={() => setFocusRequest(null)} />}
      <section className="project-feed" aria-label="Project feed">
        {ribbon}
        {!user && shortlist && !guestTrial ? <SignInPrompt onSignIn={onSignIn} /> : loading || filterLoading ? <div className="feed-loading" role="status"><RefreshCw className="spin" />Loading projects...</div> : error || filterError ? <Empty title="Projects could not be loaded" detail={error || filterError} action="Retry" onAction={() => { onRetry(); setFilterRetry(value => value + 1) }} /> : !visible.length ? <Empty title={shortlist ? 'No shortlisted projects in this view' : 'No projects match this view'} detail={snapshot ? snapshot.matchingContext?.layoutHardMatches === false ? 'Review the exclusions below. Numeric hard limits are advisory in this report.' : 'Your hard constraints were not relaxed. Review the exclusions below or edit your requirements.' : includeProcessing ? 'No published listings or unverified demo pins match these filters.' : 'Only published projects are available here. Try changing your filters or return as more projects are published.'} /> : view === 'scores' ? <ScoreSheet projects={visible} matches={matches} onSelect={setDetail} /> : <div className="project-grid">{visible.map(project => {
          const match = matches.find(item => item.projectId === project.id), saved = savedIds.includes(project.id)
          const areas = project.configurations.flatMap(item => [Number(item.carpetAreaMin), Number(item.carpetAreaMax)]).filter(value => value > 0)
          const known = match?.components.filter(component => component.status === 'available' && component.score != null).sort((left, right) => (right.score ?? 0) - (left.score ?? 0)).slice(0, 2)
          return <article className={`project-card${selected === project.id ? ' selected' : ''}`} key={project.id} id={`project-${project.id}`} onMouseEnter={() => setSelected(project.id)} onFocus={() => setSelected(project.id)}>
            <div className="project-visual"><button className="project-open" aria-label={`View ${project.submittedName} details and media`} onClick={() => setDetail(project)}><ProjectPicture project={project} /></button>{match && <span className="rank-tag">#{match.rank} match</span>}<button className={`save-project icon-button${saved ? ' saved' : ''}`} title={project.status !== 'published' ? 'Shortlisting requires a published listing' : saved ? 'Remove from shortlist' : 'Add to shortlist'} aria-label={`${saved ? 'Remove' : 'Shortlist'} ${project.submittedName}`} aria-pressed={saved} disabled={busy || project.status !== 'published'} onClick={() => void toggleShortlist(project)}><Heart size={21} fill={saved ? 'currentColor' : 'none'} /></button></div>
            <div className="project-content">
              {project.status !== 'published' && <p className="processing-label">{projectStatusLabel(project)}</p>}
              <h2 className="project-heading"><button className="project-title" onClick={() => setDetail(project)}>{project.submittedName}</button></h2>
              <div className="price-row"><strong>{projectPrice(project)}</strong>{match && <span className="match-score">{Number(match.score).toFixed(0)}<small>% match</small></span>}</div>
              {project.locationStatus === 'provisional' && <p className="location-preview-note">Provisional map location - not reviewed</p>}
              <p className="project-locality"><MapPin size={14} />{project.locality || 'Locality unknown'}</p>{project.possessionDate && <p className="project-possession">Possession: {project.possessionDate}</p>}
              <div className="project-specs"><span>{[...new Set(project.configurations.map(item => item.configurationType))].join(', ') || 'Configuration unknown'}</span><span>{areas.length ? `${Math.min(...areas).toLocaleString('en-IN')} - ${Math.max(...areas).toLocaleString('en-IN')} sq ft carpet` : 'Carpet area unknown'}</span></div>
              <p className="verification"><ShieldCheck size={15} />MahaRERA: {project.verificationStatus || 'Unknown'}</p>
              <p className="freshness">Checked {dateLabel(project.verificationCheckedAt)} | {projectStatusLabel(project)}</p>
              {match && <div className="match-reasons"><span>{reasonLabel(match.reasonCodes[0] ?? '')}</span>{known?.map(component => <span key={component.code}>{componentLabel(component.code)}: {Math.round((component.score ?? 0) * 100)}%</span>)}<span>Evidence confidence: {match.confidence == null ? 'Unknown' : `${Math.round(match.confidence * 100)}%`}</span></div>}
              {match && <MatchedLayouts match={match} />}
              <div className="card-actions"><label className="check-label"><input type="checkbox" checked={compare.includes(project.id)} disabled={project.status !== 'published' || (!compare.includes(project.id) && compare.length >= 4)} onChange={event => setCompare(current => event.target.checked ? [...current, project.id] : current.filter(id => id !== project.id))} />Compare</label><button className="text-button" onClick={() => focusOnMap(project.id)} disabled={!coordinates(project)}>On map<ArrowRight size={15} /></button></div>
            </div>
          </article>
        })}</div>}
        {snapshot?.excludedSummary.length ? <section className="exclusions"><h2>Projects excluded from this report</h2>{snapshot.excludedSummary.map(item => <p key={item.reasonCode}><strong>{item.count}</strong> {reasonLabel(item.reasonCode)}</p>)}<Link className="text-button" to="/requirements">Edit requirements<ArrowRight size={16} /></Link></section> : null}
      </section>
    </div>
    {compare.length > 0 && <div className="compare-tray"><span>{compare.length} of 4 selected</span><button className="primary" disabled={compare.length < 2} onClick={() => user ? setComparisonOpen(true) : onSignIn()}><Table2 size={18} />Compare projects</button><button className="icon-button" title="Clear comparison" aria-label="Clear comparison" onClick={() => setCompare([])}><X size={20} /></button></div>}
    {detail && <Details key={detail.id} project={detail} match={matches.find(item => item.projectId === detail.id)} onClose={() => setDetail(null)} />}
    {user && comparisonOpen && <Comparison ids={compare} onClose={() => setComparisonOpen(false)} />}
  </main>
}
function MatchedLayouts({ match, compact = false }: { match: Match; compact?: boolean }) {
  const layouts = match.layoutMatch?.layouts ?? []
  if (!layouts.length) return null
  const fields: Record<string, string> = { carpetAreaSqFt: 'minimum carpet area', bathrooms: 'bathroom count', price: 'hard budget' }
  return <div className="matched-layouts"><strong>{match.layoutMatch?.matchedLayoutCount ?? layouts.length} matched layouts{!compact && ' · best first'}</strong><ul>{(compact ? layouts.slice(0, 1) : layouts).map(layout => <li key={layout.id}><span>{layout.name} · {layout.configurationType || 'Configuration unknown'}</span>{!compact && <><small>{layout.carpetAreaSqFt == null ? 'Carpet area unknown' : `${Number(layout.carpetAreaSqFt).toLocaleString('en-IN')} sq ft carpet`} · {layout.bathrooms == null ? 'Bathrooms unknown' : `${layout.bathrooms} bathrooms`} · {projectPrice({ priceMin: layout.price, priceMax: layout.price })}</small>{layout.violatedConstraints.length > 0 && <small>Outside limit: {layout.violatedConstraints.map(field => fields[field] ?? field).join(', ')}</small>}{layout.unverifiedConstraints.length > 0 && <small>Not verified: {layout.unverifiedConstraints.map(field => fields[field] ?? field).join(', ')}</small>}</>}</li>)}</ul>{!compact && (match.layoutMatch?.matchedLayoutCount ?? layouts.length) > layouts.length && <small>Showing the top {layouts.length} layouts.</small>}</div>
}
function ScoreSheet({ projects, matches, onSelect }: { projects: Project[]; matches: Match[]; onSelect: (project: Project) => void }) {
  const codes = ['COMMUTE', 'BUDGET', 'SPACE', 'SOCIETYAMENITIES', 'NEARBYAMENITIES', 'MICROMARKET', 'PROJECTCONFIDENCE']
  return <div className="table-scroll"><table className="score-table"><caption>Match for your requirements, not a universal project quality score</caption><thead><tr><th scope="col">Project</th><th scope="col">Match</th>{codes.map(code => <th scope="col" key={code}>{componentLabel(code)}</th>)}</tr></thead><tbody>{projects.map(project => { const match = matches.find(item => item.projectId === project.id); return <tr key={project.id}><th scope="row"><button className="text-button" onClick={() => onSelect(project)}>#{match?.rank} {project.submittedName}</button>{match && <MatchedLayouts match={match} compact />}</th><td>{match ? `${Number(match.score).toFixed(1)}%` : 'Unknown'}</td>{codes.map(code => { const component = match?.components.find(item => item.code === code); return <td key={code}>{component?.status === 'available' && component.score != null ? `${Math.round(component.score * 100)}%` : 'Unknown'}</td> })}</tr> })}</tbody></table></div>
}
function Details({ project, match, onClose }: { project: Project; match?: Match; onClose: () => void }) {
  return project.status === 'published' ? <PublishedDetails project={project} match={match} onClose={onClose} /> : <ProcessingDetails project={project} onClose={onClose} />
}
function ProcessingDetails({ project, onClose }: { project: Project; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => { const node = dialog.current, trigger = document.activeElement; node?.showModal(); return () => { node?.close(); if (trigger instanceof HTMLElement) trigger.focus({ preventScroll: true }) } }, [])
  return <dialog ref={dialog} className="detail-dialog" aria-labelledby="processing-title" onCancel={onClose}>
    <button className="close-dialog icon-button" onClick={onClose} aria-label="Close project details" title="Close"><X size={20} /></button>
    <p className="eyebrow">{project.locality || 'Locality unknown'} | {projectStatusLabel(project)}</p>
    <h2 id="processing-title">{project.submittedName}</h2><p>Project facts are not published yet.</p>
    <ProjectGallery projectId={project.id} processing />
  </dialog>
}
function PublishedDetails({ project, match, onClose }: { project: Project; match?: Match; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [detail, setDetail] = useState<Project | null>(null), [error, setError] = useState(''), [retry, setRetry] = useState(0)
  useEffect(() => { const node = dialog.current, trigger = document.activeElement; node?.showModal(); return () => { node?.close(); if (trigger instanceof HTMLElement) trigger.focus({ preventScroll: true }) } }, [])
  useEffect(() => { const controller = new AbortController(); setError(''); api<Project>(`/api/v1/projects/${project.id}/`, { signal: controller.signal }).then(setDetail).catch(failure => { if (!controller.signal.aborted) setError(errorMessage(failure)) }); return () => controller.abort() }, [project.id, retry])
  return <dialog ref={dialog} className="detail-dialog" aria-labelledby="detail-title" onCancel={onClose}>
    <button className="close-dialog icon-button" onClick={onClose} aria-label="Close project details" title="Close"><X size={20} /></button>
    <p className="eyebrow">{project.locality || 'Locality unknown'} | Published project</p><h2 id="detail-title">{project.submittedName}</h2>
    <ProjectGallery projectId={project.id} layouts={detail?.configurations} />
    {error ? <div role="alert"><p>{error}</p><button className="secondary" onClick={() => setRetry(value => value + 1)}><RefreshCw size={16} />Retry details</button></div> : !detail ? <p role="status">Loading current published details...</p> : <>
      <p className="detail-price">{projectPrice(detail)}</p><p>MahaRERA {detail.mahaReraId || 'ID unknown'}: {detail.verificationStatus}. Checked {dateLabel(detail.verificationCheckedAt)}.</p>
      <h3>Amenities</h3>{detail.amenities.length ? <ul className="project-amenities">{detail.amenities.map(code => <li key={code}>{code.replaceAll('_', ' ')}</li>)}</ul> : <p>Amenities unknown.</p>}
      {match && <><h3>Match for your requirements: {Number(match.score).toFixed(1)}%</h3><p>Evidence confidence {match.confidence == null ? 'unknown' : `${Math.round(match.confidence * 100)}%`}. Snapshot facts as of {dateLabel(match.freshness.projectFactsAsOf)}.</p><ul>{match.reasonCodes.map(code => <li key={code}>{reasonLabel(code)}</li>)}</ul><MatchedLayouts match={match} /><div className="component-list">{match.components.map(component => <div key={component.code}><span>{componentLabel(component.code)}</span><span>Weight {Math.round(component.weight * 100)}</span><strong>{component.status === 'available' && component.score != null ? `${Math.round(component.score * 100)}%` : 'Unknown'}</strong></div>)}</div><p className="data-note">{match.confidence == null ? 'Local beta scores normalize the available published budget, space and society-amenity components. Commute, nearby distances, market quality and evidence confidence are unavailable.' : 'Available weights are normalized. The weighted score is multiplied by evidence confidence.'}{match.confidence != null && ' The current method uses a 50% confidence fallback when no confidence metric is available; this is not verified evidence.'}{match.layoutMatch?.layouts?.length ? ' Layout summaries capture the values used for this report.' : ' Raw scoring inputs are not included in this legacy snapshot.'}</p></>}
      <h3>Project evidence</h3>{detail.facts?.length ? <dl className="facts">{detail.facts.map((fact, index) => <div key={index}><dt>{fact.code}</dt><dd>{typeof fact.value === 'object' ? JSON.stringify(fact.value) : String(fact.value)} {fact.unit}<small>{fact.sourceClass}{fact.status ? ` | ${fact.status}` : ''} | Reviewed {dateLabel(fact.reviewedAt)}</small></dd></div>)}</dl> : <p>Evidence details unknown.</p>}
      <p className="data-note">Prices may exclude taxes and other charges. MahaRERA status is not legal certification. Unknown data is not proof that a requirement has been met.</p>
    </>}
  </dialog>
}
function Comparison({ ids, onClose }: { ids: string[]; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [projects, setProjects] = useState<Project[]>([]), [error, setError] = useState('')
  useEffect(() => { const node = dialog.current; node?.showModal(); const controller = new AbortController(); api<{ projects: Project[] }>('/api/v1/project-comparisons', { method: 'POST', body: json({ projectIds: ids }), signal: controller.signal }).then(result => setProjects(result.projects)).catch(failure => { if (!controller.signal.aborted) setError(errorMessage(failure)) }); return () => { controller.abort(); node?.close() } }, [ids])
  return <dialog ref={dialog} className="detail-dialog comparison-dialog" aria-labelledby="comparison-title" onCancel={onClose}><button className="close-dialog icon-button" title="Close comparison" aria-label="Close comparison" onClick={onClose}><X size={20} /></button><h2 id="comparison-title">Compare projects</h2>{error ? <p role="alert">{error}</p> : !projects.length ? <p role="status">Loading published comparisons...</p> : <div className="table-scroll"><table><thead><tr><th scope="col">Attribute</th>{projects.map(project => <th scope="col" key={project.id}>{project.submittedName}</th>)}</tr></thead><tbody>{['Locality', 'Price range', 'Configuration', 'Carpet area', 'MahaRERA', 'Registry checked', 'Published facts updated'].map(label => <tr key={label}><th scope="row">{label}</th>{projects.map(project => <td key={project.id}>{label === 'Locality' ? project.locality || 'Unknown' : label === 'Price range' ? projectPrice(project) : label === 'Configuration' ? [...new Set(project.configurations.map(item => item.configurationType).filter(Boolean))].join(', ') || 'Unknown' : label === 'Carpet area' ? project.configurations.map(item => `${item.name || item.configurationType || 'Unnamed layout'}: ${configurationArea(item)}`).join(', ') || 'Unknown' : label === 'MahaRERA' ? project.verificationStatus || 'Unknown' : dateLabel(label === 'Registry checked' ? project.verificationCheckedAt : project.updatedAt)}</td>)}</tr>)}</tbody></table></div>}</dialog>
}
export function Matches(props: CatalogueProps) { return <MatchesSession key={props.user?.id ?? 'guest'} {...props} /> }
function MatchesSession(props: CatalogueProps) {
  const location = useLocation(), operation = new URLSearchParams(location.search).get('operation'), snapshotId = location.pathname.split('/')[2]
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null), [error, setError] = useState(''), [retry, setRetry] = useState(0), [waiting, setWaiting] = useState(false)
  useEffect(() => {
    if (!props.user || !snapshotId) return
    const controller = new AbortController()
    let timer = 0, attempts = 0
    setError(''); setWaiting(false)
    async function poll() {
      try {
        const result = await api<Snapshot>(`/api/v1/recommendation-requests/${encodeURIComponent(snapshotId)}/`, { signal: controller.signal })
        if (controller.signal.aborted) return
        setSnapshot(result)
        if (['ready', 'partially_ready', 'expired'].includes(result.status)) return
        if (result.status === 'failed') { setError('Matching failed. Your submitted requirements are still saved.'); return }
        if (operation) { const state = await api<{ status: string }>(`/api/v1/operations/${encodeURIComponent(operation)}`, { signal: controller.signal }); if (state.status === 'failed') { setError('Matching failed. Your submitted requirements are still saved.'); return } }
        if (++attempts >= 40) { setWaiting(true); return }
        timer = window.setTimeout(poll, 3000)
      } catch (failure) { if (!controller.signal.aborted) setError(errorMessage(failure)) }
    }
    void poll()
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [snapshotId, operation, props.user, retry])
  if (!props.user) return <main id="main"><SignInPrompt onSignIn={props.onSignIn} /></main>
  if (error) return <main id="main"><Empty title="Could not load matches" detail={error} action="Retry" onAction={() => setRetry(value => value + 1)} /></main>
  if (!snapshot || !['ready', 'partially_ready', 'expired'].includes(snapshot.status)) return <main id="main" className="empty-state"><RefreshCw className={waiting ? '' : 'spin'} size={32} /><h1 tabIndex={-1}>{waiting ? 'Your matches are still queued' : 'Finding your project matches'}</h1><p>{waiting ? 'The recommendation worker has not completed this request. Your requirements are saved.' : 'Checking hard constraints and calculating your weighted score sheet.'}</p><button className="secondary" onClick={() => setRetry(value => value + 1)}>Check status</button><Link to="/profiles">My requirements</Link></main>
  return <Browse {...props} title="Your project matches" eyebrow="A shortlist shaped around you" projects={props.projects.filter(project => snapshot.items.some(item => item.projectId === project.id))} matches={snapshot.items} snapshot={snapshot} />
}
