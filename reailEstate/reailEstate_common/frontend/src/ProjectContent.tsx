import { useEffect, useRef, useState } from 'react'
import { json } from './api'
import { errorMessage, isAbortError } from './errors'
import { useProjectWorkspace } from './projectWorkspace'

type Place = { placeId: string; name: string; address: string; sourceUrl: string; attributions?: string[] }
type Content = { updatedAt: string; mapsConfigured: boolean; registryConfigured: boolean
  references: { id: string; state: string; placeId?: string; registry?: { registrationNumber: string; checkedOn?: string; evidence?: string } }[]
  amenities: { name: string; state: string }[] }
type Props = { projectId: string; version: string; registration: string; locked: boolean; onDirty: (dirty: boolean) => void; onChanged: (notice: string) => void }

export function ProjectContent({ projectId, version, registration, locked, onDirty, onChanged }: Props) {
  const { api, admin } = useProjectWorkspace()
  const [content, setContent] = useState<Content | null>(null)
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [revision, setRevision] = useState(0)
  const [query, setQuery] = useState(''), [places, setPlaces] = useState<Place[]>([]), [placeId, setPlaceId] = useState('')
  const [manual, setManual] = useState(false), [sourceUrl, setSourceUrl] = useState(''), [checkedOn, setCheckedOn] = useState(''), [evidence, setEvidence] = useState('')
  const [amenities, setAmenities] = useState(''), [confirmed, setConfirmed] = useState(false), [claimsConfirmed, setClaimsConfirmed] = useState(false)
  const request = useRef<{ payload: string; id: string } | null>(null)
  const controller = useRef<AbortController | null>(null)
  const root = `/api/v1/builder/projects/${encodeURIComponent(projectId)}/content`
  useEffect(() => {
    const scope = new AbortController(); controller.current = scope
    api<Content>(root, { signal: scope.signal }).then(setContent).catch(failure => { if (!isAbortError(failure)) setError(errorMessage(failure, 'Could not load project sources.')) })
    return () => { scope.abort(); controller.current = null }
  }, [api, root, revision, version])
  useEffect(() => { onDirty(busy || !!placeId || manual || !!amenities) }, [busy, placeId, manual, amenities, onDirty])
  async function act(action: 'search' | 'submit' | 'amenities' | 'registry-lookup') {
    if (!content || busy || locked) return
    const values = { action, expectedUpdatedAt: content.updatedAt, confirm: action === 'amenities' ? claimsConfirmed : confirmed,
      ...(action === 'search' ? { query } : action === 'amenities' ? { text: amenities } : action === 'submit' ? {
        ...(placeId ? { placeId } : {}), ...(manual ? { registry: { registration_number: registration, source_url: sourceUrl, checked_on: checkedOn, evidence } } : {}),
      } : {}) }
    const payload = json(values)
    if (request.current?.payload !== payload) request.current = { payload, id: crypto.randomUUID() }
    setBusy(true); setError('')
    try {
      const result = await api<{ places?: Place[]; registry?: unknown }>(root, { method: 'POST', signal: controller.current?.signal, body: json({ ...values, requestId: request.current.id }) })
      if (action === 'search') { setPlaces(result.places ?? []); setPlaceId('') }
      else if (action === 'registry-lookup') { setManual(true); setEvidence(JSON.stringify(result.registry, null, 2)) }
      else {
        if (action === 'amenities') { setAmenities(''); setClaimsConfirmed(false) }
        else { setPlaceId(''); setPlaces([]); setManual(false); setSourceUrl(''); setCheckedOn(''); setEvidence(''); setConfirmed(false) }
        request.current = null; setRevision(value => value + 1)
        onChanged('Claims submitted. Independent review is required before publication.')
      }
    } catch (failure) { if (!isAbortError(failure)) setError(errorMessage(failure, 'Could not save project sources.')) }
    finally { if (!controller.current?.signal.aborted) setBusy(false) }
  }
  return <section className="builder-panel project-content" aria-label="Project sources and amenities">
    <h2>Project sources and amenities</h2><p>Set a pin or coordinates in Project details above. Submit Google project matches, dated registry evidence, and amenity claims here for independent review.</p>
    {error && <div role="alert"><p>{error}</p><button className="secondary" disabled={busy} onClick={() => { setError(''); setRevision(value => value + 1) }}>Refresh sources</button></div>}
    {!content ? <p role="status">Loading sources…</p> : <>
      <form onSubmit={event => { event.preventDefault(); void act('search') }}><fieldset disabled={locked || busy || !content.mapsConfigured}>
        <legend>Find Google Maps project</legend><label>Project name and address<input required minLength={3} maxLength={500} value={query} onChange={event => setQuery(event.target.value)} /></label>
        <button className="secondary" type="submit">Find project</button>
      </fieldset></form>{!content.mapsConfigured && <p className="data-note">Google Maps lookup is not configured. A pin or coordinates can still be supplied in Project details.</p>}
      <form onSubmit={event => { event.preventDefault(); void act('submit') }}><fieldset disabled={locked || busy}>
        <legend>External source references</legend>
        {places.map(place => <label key={place.placeId}><span><input type="radio" name="project-match" checked={placeId === place.placeId} onChange={() => setPlaceId(place.placeId)} /> {place.name} · {place.address} <a href={place.sourceUrl} target="_blank" rel="noopener noreferrer">View on Google Maps</a>{place.attributions?.map((text, index) => <span key={index}> {text}</span>)}</span></label>)}
        <p><a href="https://maharera.maharashtra.gov.in/" target="_blank" rel="noopener noreferrer">Open official MahaRERA website</a></p>
        {content.registryConfigured && <button className="secondary" type="button" onClick={() => void act('registry-lookup')}>Look up registry record</button>}
        <label><span><input type="checkbox" checked={manual} onChange={event => setManual(event.target.checked)} /> Add dated registry evidence for {registration}</span></label>
        {manual && <><label>MahaRERA record URL<input type="url" required maxLength={2048} value={sourceUrl} onChange={event => setSourceUrl(event.target.value)} /></label>
          <label>Evidence date<input type="date" required value={checkedOn} onChange={event => setCheckedOn(event.target.value)} /></label>
          <label>Project details from the record<textarea required maxLength={3000} rows={4} value={evidence} onChange={event => setEvidence(event.target.value)} /></label></>}
        <label><span><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /> I confirm these source references belong to this project.</span></label>
        <button className="primary" type="submit" disabled={!confirmed || (!placeId && !manual)}>Submit source references</button>
      </fieldset></form>
      <form onSubmit={event => { event.preventDefault(); void act('amenities') }}><fieldset disabled={locked || busy}>
        <legend>Manual amenity claims</legend><label>Amenities<textarea required maxLength={3000} rows={3} placeholder="Swimming pool, gym, clubhouse…" value={amenities} onChange={event => setAmenities(event.target.value)} /></label>
        <label><span><input type="checkbox" checked={claimsConfirmed} onChange={event => setClaimsConfirmed(event.target.checked)} /> Submit these amenities as unreviewed claims.</span></label>
        <button className="primary" type="submit" disabled={!claimsConfirmed || !amenities.trim()}>Submit amenities</button>
      </fieldset></form>
      {content.amenities.length > 0 && <><h3>Saved amenities</h3><ul>{content.amenities.map(item => <li key={item.name}>{item.name} · {item.state.replaceAll('_', ' ')}</li>)}</ul></>}
      <h3>Submitted source references</h3><ul>{content.references.map(reference => <li key={reference.id}>{reference.registry?.registrationNumber ?? (reference.placeId ? 'Google project match' : 'Project location')} · {reference.state.replaceAll('_', ' ')}{reference.registry?.checkedOn && ` · ${reference.registry.checkedOn}`}</li>)}</ul>
      {admin && <div className="button-row"><a href={`/admin/platform_api/project/${encodeURIComponent(projectId)}/external-sources/`}>Review source references and station directions</a><a href={`/admin/platform_api/project/jobs/?project=${encodeURIComponent(projectId)}`}>Project evaluation jobs</a></div>}
    </>}
  </section>
}

export function ProjectCropCart() {
  const { api, admin } = useProjectWorkspace()
  const [entries, setEntries] = useState<{ projectId: string; organizationId: string; name: string; count: number }[] | null>(null)
  const [error, setError] = useState('')
  useEffect(() => { const scope = new AbortController(); api<typeof entries>('/api/v1/builder/crop-cart', { signal: scope.signal }).then(setEntries).catch(failure => { if (!isAbortError(failure)) setError(errorMessage(failure, 'Could not load crop cart.')) }); return () => scope.abort() }, [api])
  return <section className="builder-panel"><h1>Your draft crop cart</h1><p><a href={admin ? '/admin/project-workspace/' : '/builder'}>Back to project workspace</a></p>{error && <p role="alert">{error}</p>}{!entries && !error ? <p role="status">Loading cart…</p> : <ul>{entries?.map(entry => <li key={entry.projectId}><a href={`${admin ? '/admin/project-workspace/' : '/builder'}?${new URLSearchParams({ organization: entry.organizationId, project: entry.projectId, section: 'layouts' })}`}>{entry.name} · {entry.count} drafts</a></li>)}</ul>}{entries?.length === 0 && <p>Your cart is empty.</p>}</section>
}
