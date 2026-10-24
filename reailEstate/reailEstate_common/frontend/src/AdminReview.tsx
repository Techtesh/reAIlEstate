import { useEffect, useState } from 'react'
import { api, json } from './api'
import { errorMessage } from './errors'
import { useUnsavedChanges } from './useUnsavedChanges'

type Amenity = { id: string; name: string; state: string; updatedAt: string; canReview: boolean; reason: string; evidence: unknown; availabilityState: string; distanceMeters: number | null; travelMinutes: number | null }
type Reference = { id: string; state: string; version: number; canReview: boolean; reason: string; evidence: unknown }
type Review = { projectVersion: string; amenities: Amenity[]; references: Reference[] }

export function ProjectReview({ projectId, disabled = false, onDirty }: { projectId: string; disabled?: boolean; onDirty: (dirty: boolean) => void }) {
  const [data, setData] = useState<Review | null>(null), [error, setError] = useState(''), [revision, setRevision] = useState(0)
  const [busy, setBusy] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [transitDirty, setTransitDirty] = useState(false)
  useUnsavedChanges(dirty)
  useEffect(() => { onDirty(dirty || busy || transitDirty); return () => onDirty(false) }, [dirty, busy, transitDirty, onDirty])
  const root = `/api/v1/admin/projects/${encodeURIComponent(projectId)}/review`
  useEffect(() => {
    const scope = new AbortController()
    api<Review>(root, { signal: scope.signal }).then(setData).catch(e => { if (!scope.signal.aborted) setError(errorMessage(e)) })
    return () => scope.abort()
  }, [root, revision])
  async function review(event: React.FormEvent<HTMLFormElement>, item: Amenity | Reference, kind: 'amenity' | 'reference') {
    event.preventDefault(); const values = new FormData(event.currentTarget)
    const decision = (event.nativeEvent as SubmitEvent).submitter?.getAttribute('value')
    setBusy(true); setError('')
    try {
      await api(root, { method: 'POST', body: json({ kind, id: item.id, decision, reason: values.get('reason'),
        expectedUpdatedAt: 'updatedAt' in item ? item.updatedAt : undefined, expectedVersion: 'version' in item ? item.version : undefined,
        projectVersion: data?.projectVersion, ...('availabilityState' in item ? {
          availability_state: values.get('availability_state'), distance_meters: values.get('distance_meters') ? Number(values.get('distance_meters')) : null,
          travel_minutes: values.get('travel_minutes') ? Number(values.get('travel_minutes')) : null,
        } : {}) }) })
      setRevision(value => value + 1)
      setDirty(false)
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  return <section className="builder-panel admin-review" aria-label="Project evidence review"><h2>Review project evidence</h2>
    {error && <p role="alert">{error}</p>}{!data ? <p role="status">Loading review evidence…</p> : <>
      <h3>Amenities</h3>{!data.amenities.length && <p>No amenity claims.</p>}
      {data.amenities.map(item => <details key={`${item.id}-${item.updatedAt}`} className="admin-review-row"><summary>{item.name} · {item.state}</summary><form onChange={() => setDirty(true)} onSubmit={event => void review(event, item, 'amenity')}>
        <h4>{item.name} <span className="data-note">{item.state}</span></h4><details><summary>Evidence and history</summary><pre>{JSON.stringify(item.evidence, null, 2)}</pre></details>
        <fieldset disabled={disabled || busy || !item.canReview}><div className="builder-form-grid"><label>Availability<input name="availability_state" required maxLength={32} defaultValue={item.availabilityState} /></label>
          <label>Distance (m)<input name="distance_meters" type="number" min="0" defaultValue={item.distanceMeters ?? ''} /></label><label>Travel (minutes)<input name="travel_minutes" type="number" min="0" defaultValue={item.travelMinutes ?? ''} /></label>
          <label>Review reason<input name="reason" required maxLength={1000} /></label></div><div className="button-row"><button className="primary" value="accept">Accept amenity</button><button className="secondary" value="reject">Reject amenity</button></div></fieldset>
        {!item.canReview && <p>{item.reason}</p>}
      </form></details>)}
      <h3>Source references</h3>{!data.references.length && <p>No source references.</p>}
      {data.references.map(item => <details key={`${item.id}-${item.version}`} className="admin-review-row"><summary>Source reference · {item.state}</summary><form onChange={() => setDirty(true)} onSubmit={event => void review(event, item, 'reference')}>
        <h4>Source reference · {item.state}</h4><details><summary>Inspect evidence</summary><pre>{JSON.stringify(item.evidence, null, 2)}</pre></details>
        <fieldset disabled={disabled || busy || !item.canReview || item.state !== 'unreviewed'}><label>Review reason<input name="reason" required maxLength={1000} /></label>
          <div className="button-row"><button className="primary" value="accept">Accept reference</button><button className="secondary" value="reject">Reject reference</button></div></fieldset>{!item.canReview && <p>{item.reason}</p>}
      </form></details>)}
      <TransitReview projectId={projectId} references={data.references} disabled={disabled} onDirty={setTransitDirty} />
    </>}
  </section>
}

type Transit = { projectVersion: string; revision: string; routes: unknown; choices: Record<'railway' | 'metro', { value: string; label: string }[]>; selected: Record<'railway' | 'metro', string> }
function TransitReview({ projectId, references, disabled, onDirty }: { projectId: string; references: Reference[]; disabled: boolean; onDirty: (dirty: boolean) => void }) {
  const [data, setData] = useState<Transit | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false), [revision, setRevision] = useState(0)
  const root = `/api/v1/admin/console/projects/${projectId}/transit`
  useEffect(() => { const scope = new AbortController(); api<Transit>(root, { signal: scope.signal }).then(setData).catch(e => { if (!scope.signal.aborted) setError(errorMessage(e)) }); return () => scope.abort() }, [root, revision])
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!data) return
    const values = new FormData(event.currentTarget), action = (event.nativeEvent as SubmitEvent).submitter?.getAttribute('value')
    if (action === 'retry-routes' && !window.confirm('Refresh routes using the configured provider? This may incur provider costs.')) return
    setBusy(true); setError('')
    try { await api(root, { method: 'POST', body: json({ action, confirm: true, projectVersion: data.projectVersion, revision: data.revision,
      railway: values.get('railway'), metro: values.get('metro'), referenceId: values.get('referenceId'), radius: 10000 }) }); onDirty(false); setRevision(n => n + 1) }
    catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  return <section><h3>Stations and route references</h3>{error && <p role="alert">{error}</p>}{data && <form key={data.projectVersion + data.revision} onChange={() => onDirty(true)} onSubmit={event => void submit(event)}><fieldset disabled={disabled || busy}>
    <div className="builder-form-grid">{(['railway', 'metro'] as const).map(kind => <label key={kind}>{kind === 'railway' ? 'Railway station' : 'Metro station'}<select name={kind} defaultValue={data.selected[kind]}>{data.choices[kind].map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>)}</div><button className="secondary" value="save-stations">Save station choices</button>
    <label>Google reference for route refresh<select name="referenceId"><option value="">Choose reference</option>{references.filter(item => !!(item.evidence as Record<string, unknown>)?.googlePlaceId).map(item => <option key={item.id} value={item.id}>Google project reference · {item.state}</option>)}</select></label><button className="secondary" value="retry-routes">Refresh saved routes</button>
  </fieldset><details><summary>Saved route evidence</summary><pre>{JSON.stringify(data.routes, null, 2)}</pre></details></form>}</section>
}

export type ReviewPlan = { planId: string | null; updatedAt: string | null; configuration?: string; area?: string | number | null; roomCount?: number | null; bathrooms?: number | null; balconies?: number | null; orientation?: string; dimensions?: { room?: string; name?: string; lengthFt?: number | string | null; widthFt?: number | string | null }[]; canReview?: boolean; reviewReason?: string }

export function PlanReview({ plan, onSaved, onDirty, disabled = false }: { plan: ReviewPlan; onSaved: () => void; onDirty: (dirty: boolean) => void; disabled?: boolean }) {
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [extractionDirty, setExtractionDirty] = useState(false)
  useUnsavedChanges(dirty)
  function changed(event: React.FormEvent<HTMLFormElement>) { setDirty(true); onDirty(true); if ((event.target as HTMLInputElement).name !== 'reason') setExtractionDirty(true) }
  const [rooms, setRooms] = useState(() => (plan.dimensions ?? []).map((room, index) => ({ source_index: index as number | null, room: room.room || room.name || '', length_ft: String(room.lengthFt ?? ''), width_ft: String(room.widthFt ?? ''), DELETE: false })))
  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); const values = new FormData(event.currentTarget)
    const decision = (event.nativeEvent as SubmitEvent).submitter?.getAttribute('value')
    setBusy(true); setError(''); setNotice('')
    try {
      await api(`/api/v1/admin/plans/${encodeURIComponent(plan.planId!)}/review`, { method: 'POST', body: json({ decision, reason: values.get('reason'), expectedUpdatedAt: plan.updatedAt,
        fields: Object.fromEntries(['configuration_type', 'carpet_area_sqft', 'room_count', 'bathroom_count', 'balcony_count', 'orientation'].map(name => [name, values.get(name)])), rooms }) })
      setNotice(decision === 'save-correction' ? 'Corrections saved for independent review.' : 'Review decision saved.'); setDirty(false); setExtractionDirty(false); onDirty(false); onSaved()
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  return <form className="admin-review" onChange={changed} onSubmit={event => void save(event)}><h3>Review and correct extraction</h3>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <fieldset disabled={busy || disabled}><div className="builder-form-grid">
      <label>Configuration<input name="configuration_type" defaultValue={plan.configuration} maxLength={32} /></label>
      <label>Carpet area (sq ft)<input name="carpet_area_sqft" type="number" step="0.01" min="0.01" defaultValue={plan.area ?? ''} /></label>
      <label>Bedrooms<input name="room_count" type="number" min="0" max="100" defaultValue={plan.roomCount ?? ''} /></label>
      <label>Bathrooms<input name="bathroom_count" type="number" min="0" max="100" defaultValue={plan.bathrooms ?? ''} /></label>
      <label>Balconies<input name="balcony_count" type="number" min="0" max="100" defaultValue={plan.balconies ?? ''} /></label>
      <label>Orientation<input name="orientation" maxLength={32} defaultValue={plan.orientation} /></label>
    </div><div className="builder-media-table"><table><caption>Room measurements</caption><thead><tr><th>Room</th><th>Length (ft)</th><th>Width (ft)</th><th>Area (sq ft)</th><th>Remove</th></tr></thead><tbody>
      {rooms.map((room, index) => <tr key={index}><td><input aria-label={`Room ${index + 1} name`} required={!room.DELETE} value={room.room} onChange={event => setRooms(rows => rows.map((row, i) => i === index ? { ...row, room: event.target.value } : row))} /></td>
        {(['length_ft', 'width_ft'] as const).map(axis => <td key={axis}><input aria-label={`Room ${index + 1} ${axis}`} type="number" step="any" min="0.001" max="1000" value={room[axis]} onChange={event => setRooms(rows => rows.map((row, i) => i === index ? { ...row, [axis]: event.target.value } : row))} /></td>)}
        <td>{room.length_ft && room.width_ft ? (Number(room.length_ft) * Number(room.width_ft)).toFixed(2) : 'Unknown'}</td><td><input type="checkbox" aria-label={`Remove room ${index + 1}`} checked={room.DELETE} onChange={event => setRooms(rows => rows.map((row, i) => i === index ? { ...row, DELETE: event.target.checked } : row))} /></td></tr>)}
    </tbody></table></div><button type="button" className="secondary" disabled={rooms.length >= 100} onClick={() => { setDirty(true); setExtractionDirty(true); onDirty(true); setRooms(rows => [...rows, { source_index: null, room: '', length_ft: '', width_ft: '', DELETE: false }]) }}>Add room</button>
    <label>Correction / review reason<input name="reason" required maxLength={1000} /></label><div className="button-row">
      <button className="secondary" value="save-correction">Save corrections for review</button><button className="primary" disabled={!plan.canReview || extractionDirty} value="approve">Approve saved extraction</button><button className="secondary" disabled={!plan.canReview || extractionDirty} value="reject">Reject</button>
    {extractionDirty && <p>Save these corrections for review before approving or rejecting the saved extraction.</p>}
    </div>{!plan.canReview && <p>{plan.reviewReason || 'Review permission or independent review is required.'}</p>}</fieldset>
    <button className="secondary" type="button" disabled={!dirty || busy} onClick={event => {
      event.currentTarget.form?.reset()
      setRooms((plan.dimensions ?? []).map((room, index) => ({ source_index: index, room: room.room || room.name || '', length_ft: String(room.lengthFt ?? ''), width_ft: String(room.widthFt ?? ''), DELETE: false })))
      setDirty(false); setExtractionDirty(false); onDirty(false); setError('')
    }}>Discard unsaved review</button>
  </form>
}

type FloorEvaluation = { flatsPerFloor?: number | null; liftCount?: number | null; staircaseCount?: number | null; evaluatedAt?: string; visualEvidence?: string; assetId?: string; refugeArea?: { presence: string; area?: number | null; unit?: string | null; evidence?: string }; commonAreas?: { kind: string; label: string; count: number | null; area: number | null; unit: string | null; evidence: string }[] }
export function FloorSummary({ features }: { features?: Record<string, unknown> }) {
  const summary = features?.floorEvaluation as FloorEvaluation | undefined
  const refuge = summary?.refugeArea
  return <section className="floor-summary" aria-label="Floor summary"><h3>Floor summary</h3><dl className="builder-layout-facts">
    <div><dt>Flats on this floor</dt><dd>{summary?.flatsPerFloor ?? 'Unknown'}</dd></div><div><dt>Lifts</dt><dd>{summary?.liftCount ?? 'Unknown'}</dd></div>
    <div><dt>Staircases</dt><dd>{summary?.staircaseCount ?? 'Unknown'}</dd></div><div><dt>Refuge area</dt><dd>{refuge?.presence === 'present' ? refuge.area != null ? `${refuge.area} ${refuge.unit ?? ''}` : 'Present · area unknown' : refuge?.presence === 'absent' ? 'Absent (source evidence)' : 'Unknown'}</dd></div>
    <div><dt>Flats per lift on this floor</dt><dd>{summary?.flatsPerFloor != null && summary.liftCount != null && summary.liftCount > 0 ? (summary.flatsPerFloor / summary.liftCount).toFixed(2) : 'Unknown'}</dd></div>
  </dl>{summary?.commonAreas?.length ? <details><summary>Other common-floor features</summary><ul>{summary.commonAreas.map((item, index) => <li key={index}>{item.label || item.kind.replaceAll('_', ' ')} · Count: {item.count ?? 'Unknown'} · Area: {item.area != null && item.unit ? `${item.area} ${item.unit}` : 'Unknown'}<p>{item.evidence}</p></li>)}</ul></details> : <p className="data-note">Additional common-floor features have not been recorded.</p>}<p className="data-note">Source observations, subject to review. Flat count is not the number of generated crops.</p>{summary?.visualEvidence && <details><summary>Floor evidence</summary><p>{summary.visualEvidence}</p><p>{summary.evaluatedAt || 'Analysis date unknown'}</p></details>}</section>
}
