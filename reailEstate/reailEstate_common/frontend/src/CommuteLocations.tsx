import { useEffect, useRef, useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { api, ApiError, json } from './api'
import { coordinates, dateLabel, type CommutePlace, type LocatedPlace, type Profile } from './buyer'

function LocationPicker({ place, onChange, signedIn }: { place: CommutePlace; onChange: (place: CommutePlace) => void; signedIn: boolean }) {
  const [method, setMethod] = useState('search')
  const [query, setQuery] = useState('')
  const [latitude, setLatitude] = useState(String(place.latitude ?? ''))
  const [longitude, setLongitude] = useState(String(place.longitude ?? ''))
  const [results, setResults] = useState<LocatedPlace[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const request = useRef<AbortController | null>(null)
  const current = useRef({ place, onChange })
  useEffect(() => { current.current = { place, onChange } }, [place, onChange])
  useEffect(() => () => request.current?.abort(), [])
  function reset() { request.current?.abort(); setResults([]); setError(''); setBusy(false) }
  function choose(result: LocatedPlace) {
    const latest = current.current
    latest.onChange({ ...latest.place, ...result, label: latest.place.label.trim() || result.label.slice(0, 100) })
    reset(); setQuery('')
  }
  async function locate() {
    reset()
    if (method === 'coordinates') {
      const point = coordinates({ latitude, longitude })
      if (!point) { setError('Enter a latitude from -90 to 90 and a longitude from -180 to 180.'); return }
      choose({ placeId: `manual:${crypto.randomUUID()}`, label: 'My destination', precision: 'exact', latitude: point[0], longitude: point[1] })
      return
    }
    if (!signedIn) { setError('Sign in to look up a destination with Google Maps.'); return }
    if (!query.trim()) { setError('Enter a search, Google Maps link, or Plus Code.'); return }
    const controller = new AbortController()
    request.current = controller; setBusy(true)
    try {
      if (method === 'share' && /^https?:\/\//i.test(query.trim())) {
        const result = await api<LocatedPlace>('/api/v1/commute-locations/resolve', { method: 'POST', body: json({ mapsUrl: query.trim() }), signal: controller.signal })
        if (!controller.signal.aborted) choose(result)
      } else {
        const result = await api<{ results: LocatedPlace[] }>('/api/v1/commute-locations/search', { method: 'POST', body: json({ query: query.trim() }), signal: controller.signal })
        if (!controller.signal.aborted) { setResults(result.results); if (!result.results.length) setError('No locations found. Add a locality to your search or use coordinates.') }
      }
    } catch (failure) {
      if (!controller.signal.aborted && !(failure instanceof DOMException && failure.name === 'AbortError')) {
        setError(failure instanceof ApiError && failure.status === 503 ? 'Google Maps lookup is unavailable. Use coordinates or a full Google Maps pin link instead.'
          : failure instanceof ApiError && failure.status === 400 ? 'This location could not be resolved. Use a single Google Maps pin link, a Plus Code with its locality, or coordinates.'
          : failure instanceof Error ? failure.message : 'Location lookup failed. Please try again.')
      }
    } finally { if (!controller.signal.aborted) setBusy(false) }
  }
  return <details className="location-picker"><summary>{coordinates(place) ? 'Change map location' : 'Set map location'}</summary>
    <p className="data-note">Searches and links are sent to Google only when you choose Look up location. Coordinates stay in your private draft.</p>
    <label>Location entry method<select value={method} onChange={event => { reset(); setMethod(event.target.value); setQuery('') }}><option value="search">Google Maps search</option><option value="coordinates">Latitude / longitude</option><option value="share">Google Maps link / Plus Code</option></select></label>
    {method === 'coordinates' ? <div className="field-grid"><label>Latitude<input type="number" step="any" min="-90" max="90" value={latitude} onChange={event => setLatitude(event.target.value)} /></label><label>Longitude<input type="number" step="any" min="-180" max="180" value={longitude} onChange={event => setLongitude(event.target.value)} /></label></div>
      : <label>{method === 'share' ? 'Google Maps share link or Plus Code' : 'Search Google Maps'}<input value={query} maxLength={2048} autoComplete="off" onChange={event => { reset(); setQuery(event.target.value) }} placeholder={method === 'share' ? 'Paste a pin link or Plus Code with locality' : 'Place name and locality'} /></label>}
    <button type="button" className="secondary" disabled={busy} onClick={() => void locate()}>{busy ? 'Looking up...' : method === 'coordinates' ? 'Use coordinates' : 'Look up location'}</button>
    {error && <p className="field-error" role="alert">{error}</p>}
    {results.length > 0 && <ul className="location-results" aria-label="Location search results">{results.map(result => <li key={result.placeId}><button type="button" className="text-button" onClick={() => choose(result)}>Use {result.label}</button></li>)}</ul>}
  </details>
}

export function CommuteLocations({ places, onChange, signedIn, disabled = false }: { places: CommutePlace[]; onChange: (places: CommutePlace[]) => void; signedIn: boolean; disabled?: boolean }) {
  const [error, setError] = useState('')
  function update(index: number, place: CommutePlace) {
    if (places.some((item, current) => current !== index && item.placeId === place.placeId)) { setError('That destination is already listed. Update its trips instead of adding it twice.'); return }
    setError(''); onChange(places.map((item, current) => current === index ? place : item))
  }
  return <fieldset className="commute-editor" disabled={disabled}>
    <legend className="sr-only">Daily destinations</legend>
    {error && <p className="field-error" role="alert">{error}</p>}
    <p className="data-note">Destinations with a map location appear only on your shortlist map. Locality-only entries remain unmapped; adding a pin does not calculate travel times.</p>
    {places.map((place, index) => <fieldset className="destination" key={place.placeId}><legend>Destination {index + 1}</legend>
      <div className="field-grid"><label>{place.precision === 'exact' ? 'Destination name' : 'Destination locality'}<input value={place.label} maxLength={100} autoComplete="off" placeholder="e.g. Work, Powai" onChange={event => update(index, { ...place, label: event.target.value })} /></label>
        <label>Trips per week<input type="number" min="0" max="28" value={place.frequencyPerWeek} onChange={event => update(index, { ...place, frequencyPerWeek: Number(event.target.value) })} /></label>
        <label>Maximum one-way minutes<input type="number" min="5" max="300" value={place.maxOneWayMinutes} onChange={event => update(index, { ...place, maxOneWayMinutes: Number(event.target.value) })} /></label></div>
      <p className="data-note">{coordinates(place) ? 'Map location set' : 'No map location set'}</p>
      <LocationPicker place={place} onChange={value => update(index, value)} signedIn={signedIn} />
      <div className="destination-modes">{(['transit', 'car', 'walking', 'cycling'] as const).map(mode => <label key={mode} className="check-label"><input type="checkbox" checked={place.modes.includes(mode)} onChange={event => update(index, { ...place, modes: event.target.checked ? [...place.modes, mode] : place.modes.filter(value => value !== mode) })} />{mode === 'transit' ? 'Public transport' : mode}</label>)}
        <button className="icon-button" type="button" aria-label={`Remove destination ${index + 1}`} title="Remove destination" onClick={() => onChange(places.filter((_, current) => current !== index))}><Trash2 size={18} /></button></div>
    </fieldset>)}
    <button type="button" className="secondary" disabled={places.length >= 10} onClick={() => onChange([...places, { placeId: `manual:${crypto.randomUUID()}`, label: '', precision: 'locality', frequencyPerWeek: 5, modes: ['transit'], maxOneWayMinutes: 60 }])}><Plus size={18} />Add destination</button>
  </fieldset>
}

type ShortlistCommuteProps = {
  places: CommutePlace[]; onChange: (places: CommutePlace[]) => void; profiles: Profile[];
  onLoad: (profile: Profile) => void; unsaved: boolean; disabled: boolean;
  consent: boolean; setConsent: (value: boolean) => void; onSave: () => void; saveStatus: string;
  loadError: string; onRetry: () => void;
}
export function ShortlistCommute({ places, onChange, profiles, onLoad, unsaved, disabled, consent, setConsent, onSave, saveStatus, loadError, onRetry }: ShortlistCommuteProps) {
  const [profileId, setProfileId] = useState('')
  const [replace, setReplace] = useState(false)
  const profile = profiles.find(item => item.id === profileId)
  return <details className="shortlist-commute"><summary>Daily commute ({places.length})</summary>
    <p>Preview your destinations alongside shortlisted homes. Save privately to keep them across visits.</p>
    {loadError && <div role="alert"><p>Saved requirements could not be loaded: {loadError}</p><button type="button" className="secondary" onClick={onRetry} disabled={disabled}>Retry saved requirements</button></div>}
    {profiles.length > 0 && <div className="commute-profile"><label>Saved requirement profile<select value={profileId} disabled={disabled} onChange={event => { setProfileId(event.target.value); setReplace(false) }}><option value="">Choose a profile</option>{profiles.map(item => <option key={item.id} value={item.id}>{item.sections.home?.configurations.join(', ') || 'Home requirements'} | {item.status} | v{item.version} | {dateLabel(item.updatedAt)}</option>)}</select></label>
      <p className="data-note">Loading replaces the whole current requirement draft, not just its destinations. Submitted profiles open as a new draft.</p>
      {unsaved && <label className="check-label"><input type="checkbox" checked={replace} onChange={event => setReplace(event.target.checked)} />Replace my unsaved draft</label>}
      <button type="button" className="secondary" disabled={disabled || !profile || (unsaved && !replace)} onClick={() => { if (profile) { onLoad(profile); setReplace(false) } }}>Load requirements</button></div>}
    <CommuteLocations places={places} onChange={onChange} signedIn disabled={disabled} />
    <label className="check-label"><input type="checkbox" checked={consent} disabled={disabled} onChange={event => setConsent(event.target.checked)} />I consent to saving my requirements and destinations privately to my account.</label>
    <div className="button-row"><button type="button" className="primary" disabled={disabled || !consent} onClick={onSave}>Save destinations</button><span role="status">{unsaved ? 'Unsaved changes' : saveStatus}</span></div>
  </details>
}
