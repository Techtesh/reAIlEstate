import { useCallback, useEffect, useState } from 'react'
import { MapContainer, Marker, Polyline, Popup, TileLayer, useMap, useMapEvents } from 'react-leaflet'
import { divIcon, latLngBounds } from 'leaflet'
import { LocateFixed, RefreshCw, ShieldCheck, TrainFront, TramFront } from 'lucide-react'
import { api } from './api'
import { coordinates, dateLabel, projectStatusLabel, type CommutePlace, type Project, type Match, type MapStation, type MapLine, type MapConnection } from './buyer'
import 'leaflet/dist/leaflet.css'

function lineKey(kind: MapStation['kind'], name: string) { return `${kind}:${name.trim().toLowerCase()}` }
function lineColor(kind: MapStation['kind'], name: string) {
  const colors: Record<string, string> = {
    'line 1': '#0072bc', 'line 2': '#ffcc00', 'line 2a': '#ffcc00', 'line 2b': '#ffcc00',
    'line 3': '#00b5ad', 'line 4': '#00843d', 'line 4a': '#00843d', 'line 5': '#f58220',
    'line 6': '#f06292', 'line 7': '#d9232e', 'line 7a': '#d9232e', 'line 8': '#000000',
    'line 9': '#d9232e', 'line 10': '#000000', 'line 11': '#000000', 'line 12': '#f37a48',
  }
  return (kind === 'metro' && colors[name.trim().toLowerCase()]) || (kind === 'metro' ? '#52636b' : '#75494c')
}


type FocusRequest = { projectId: string; version: number }

function Controls({ positions, focused, focusVersion, onFit }: { positions: string; focused: Project | undefined; focusVersion: number; onFit: () => void }) {
  const map = useMap()
  const fit = useCallback(() => {
    const points: [number, number][] = JSON.parse(positions)
    if (points.length) map.fitBounds(latLngBounds(points).pad(.22), { maxZoom: 14, animate: false })
    else map.setView([19.14, 72.96], 11)
  }, [map, positions])
  const focus = useCallback(() => {
    const point = focused && coordinates(focused)
    if (!point) return false
    map.setView(point, Math.max(map.getZoom(), 14), { animate: false })
    return true
  }, [map, focused])
  useEffect(() => {
    map.invalidateSize()
    if (!focus()) fit()
  }, [map, fit, focus, focusVersion])
  useEffect(() => {
    let wasVisible = map.getContainer().clientWidth > 0
    let resizeFrame = 0
    const observer = new ResizeObserver(() => {
      const visible = map.getContainer().clientWidth > 0
      if (visible && !wasVisible) {
        cancelAnimationFrame(resizeFrame)
        resizeFrame = requestAnimationFrame(() => {
          map.invalidateSize({ animate: false })
          if (!focus()) fit()
        })
      }
      wasVisible = visible
    })
    observer.observe(map.getContainer())
    return () => {
      cancelAnimationFrame(resizeFrame)
      observer.disconnect()
    }
  }, [map, fit, focus, focusVersion])
  return <button type="button" className="map-fit icon-button" title="Fit map locations" aria-label="Fit map locations" onClick={() => { onFit(); fit() }}><LocateFixed size={20} /></button>
}

function MapSelectionClearer({ onClear }: { onClear: () => void }) {
  useMapEvents({ click: onClear })
  return null
}

type ProjectMapProps = {
  projects: Project[]
  matches: Match[]
  selected: string
  onSelect: (id: string) => void
  onInspect: (project: Project) => void
  commutePlaces?: CommutePlace[]
  shortlistedIds?: string[]
  focusRequest?: FocusRequest | null
  onFit?: () => void
}

function StationMarkers({ stations }: { stations: MapStation[] }) {
  const map = useMap()
  const [zoom, setZoom] = useState(() => map.getZoom())
  useMapEvents({ zoomend: () => setZoom(map.getZoom()) })
  const compact = zoom < 12
  const size = compact ? 10 : 32
  return <>{stations.map(station => {
    const label = document.createElement('span')
    const lineNumber = /^line\s+(\d+[a-z]?)$/i.exec(station.line.trim())?.[1].toUpperCase() ?? ''
    label.textContent = `${station.kind === 'metro' ? 'M' : 'R'}${lineNumber}`
    const color = lineColor(station.kind, station.line)
    label.style.backgroundColor = color
    label.style.color = ['#ffcc00', '#00b5ad', '#f58220', '#f06292', '#f37a48'].includes(color) ? '#172621' : '#ffffff'
    const icon = divIcon({ html: label, className: `station-pin ${station.kind}${compact ? ' compact' : ''}${station.serviceStatus === 'unverified' ? ' unverified' : ''}`, iconSize: [size, size], iconAnchor: [size / 2, size / 2] })
    const status = { unverified: 'Status unverified', operational: 'Operational', planned: 'Planned', under_construction: 'Under construction' }[station.serviceStatus]
    return <Marker key={station.id} position={[station.location.latitude, station.location.longitude]} icon={icon} title={`${station.name} (${station.kind})`} alt={`${station.name}, ${station.kind}, ${station.line || 'Line unknown'}, ${status}`} zIndexOffset={-100}>
      <Popup maxWidth={245}><div className="station-details"><strong>{station.name}</strong><span>{station.kind === 'metro' ? 'Metro' : 'Railway'}{station.line ? ` | ${station.line}` : ''}</span><span className={`station-status ${station.serviceStatus}`}>{status}</span><span>{station.source === 'user_candidate' ? 'User-added candidate' : station.source === 'authority' ? 'Authority catalogue' : 'Saved station reference'}</span>{station.asOf && <span>As of {dateLabel(station.asOf)}</span>}{station.sourceUrl && <a href={station.sourceUrl} target="_blank" rel="noopener noreferrer">Source</a>}</div></Popup>
    </Marker>
  })}</>
}

type MapCluster = { key: string; projects: Project[]; position: [number, number] }

function projectIcon(project: Project, selected: string, shortlisted: Set<string>) {
  const label = document.createElement('span')
  label.textContent = project.submittedName
  const isShortlisted = shortlisted.has(project.id)
  return divIcon({ html: label, className: `price-pin${project.verificationStatus === 'verified' ? ' registry-verified' : ''}${selected === project.id ? ' selected' : ''}${isShortlisted ? ' shortlisted' : ''}${project.locationStatus === 'provisional' ? ' provisional' : ''}`, iconSize: [180, 38], iconAnchor: [90, 38] })
}

function ProjectMarkers({ projects, matches, selected, shortlisted, onSelect }: { projects: Project[]; matches: Match[]; selected: string; shortlisted: Set<string>; onSelect: (id: string) => void }) {
  const map = useMap()
  const [, setRevision] = useState(0)
  useMapEvents({ moveend: () => setRevision(value => value + 1), zoomend: () => setRevision(value => value + 1), resize: () => setRevision(value => value + 1) })
  const bounds = map.getBounds().pad(.06)
  const zoom = map.getZoom()
  const cellSize = zoom >= 15 ? 1 : zoom >= 13 ? 28 : 58
  const clusters = new Map<string, MapCluster>()
  const selectedProject = projects.find(project => project.id === selected)
  for (const project of projects) {
    if (project.id === selectedProject?.id) continue
    const point = coordinates(project)
    if (!point || !bounds.contains(point)) continue
    const projected = map.latLngToContainerPoint(point)
    const key = `${Math.floor(projected.x / cellSize)}:${Math.floor(projected.y / cellSize)}`
    const cluster = clusters.get(key) ?? { key, projects: [], position: [0, 0] }
    cluster.projects.push(project)
    cluster.position = [cluster.position[0] + point[0], cluster.position[1] + point[1]]
    clusters.set(key, cluster)
  }
  const matchByProject = new Map(matches.map(match => [match.projectId, match]))
  const projectMarker = (project: Project, position: [number, number], spreadOrder = 0) => {
    const match = matchByProject.get(project.id), isShortlisted = shortlisted.has(project.id)
    const title = `${project.submittedName}${isShortlisted ? ' - shortlisted' : ''}${project.locationStatus === 'provisional' ? ' - unverified demo location' : ''}`
    return <Marker key={project.id} position={position} icon={projectIcon(project, selected, shortlisted)} title={title} alt={`${title}${match ? `, rank ${match.rank}, ${Number(match.score).toFixed(0)}% match` : ''}`} zIndexOffset={(isShortlisted ? 600 : 0) + spreadOrder} eventHandlers={{ click: () => onSelect(project.id), keydown: event => { if (event.originalEvent.key === 'Enter') onSelect(project.id) } }} />
  }
  return <>{[...clusters.values()].map(cluster => {
    const count = cluster.projects.length
    const position: [number, number] = [cluster.position[0] / count, cluster.position[1] / count]
    if (count > 1 && zoom < 15) {
      const label = document.createElement('span')
      label.textContent = String(count)
      const icon = divIcon({ html: label, className: 'project-cluster', iconSize: [44, 44], iconAnchor: [22, 22] })
      return <Marker key={cluster.key} position={position} icon={icon} title={`${count} projects in this area`} alt={`${count} projects in this area`} zIndexOffset={200} eventHandlers={{ click: () => map.fitBounds(latLngBounds(cluster.projects.map(coordinates).filter((point): point is [number, number] => point !== null)).pad(.35), { maxZoom: Math.min(zoom + 3, 16), animate: true }) }}><Popup><div className="station-details"><strong>{count} unverified projects</strong><span>Zoom in to explore individual projects.</span></div></Popup></Marker>
    }
    if (count > 1) {
      const radius = Math.min(100, 44 + count * 20)
      return cluster.projects.map((project, index) => {
        const point = coordinates(project)!
        const center = map.latLngToContainerPoint(point)
        const angle = Math.PI * 2 * index / count - Math.PI / 2
        const spread = map.containerPointToLatLng([center.x + Math.cos(angle) * radius, center.y + Math.sin(angle) * radius])
        return projectMarker(project, [spread.lat, spread.lng], index)
      })
    }
    return projectMarker(cluster.projects[0], position)
  })}{selectedProject && coordinates(selectedProject) && <Marker position={coordinates(selectedProject)!} icon={projectIcon(selectedProject, selected, shortlisted)} title={selectedProject.submittedName} alt={selectedProject.submittedName} zIndexOffset={1000} eventHandlers={{ click: () => onSelect(selectedProject.id) }} />}</>
}
export function ProjectMap({ projects, matches, selected, onSelect, onInspect, commutePlaces = [], shortlistedIds = [], focusRequest = null, onFit = () => {} }: ProjectMapProps) {
  const [mapLayer, setMapLayer] = useState<'satellite' | 'commute'>('satellite')
  const [tileError, setTileError] = useState(false)
  const [satelliteUnavailable, setSatelliteUnavailable] = useState(false)
  const [retry, setRetry] = useState(0)
  const [stations, setStations] = useState<MapStation[]>([])
  const [lines, setLines] = useState<MapLine[]>([])
  const [connections, setConnections] = useState<MapConnection[]>([])
  const [selectedLine, setSelectedLine] = useState('')
  const [stationLoading, setStationLoading] = useState(true)
  const [stationError, setStationError] = useState(false)
  const [stationWarnings, setStationWarnings] = useState<string[]>([])
  const [stationRetry, setStationRetry] = useState(0)
  const [layers, setLayers] = useState({ metro: true, railway: true, unverified: true })
  useEffect(() => {
    const controller = new AbortController()
    setStationLoading(true); setStationError(false)
    api<{ stations: MapStation[]; lines?: MapLine[]; connections?: MapConnection[]; warnings: string[] }>('/api/v1/map-stations', { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) { setStations(result.stations); setLines(result.lines ?? []); setConnections(result.connections ?? []); setStationWarnings(result.warnings) }
    }).catch(() => { if (!controller.signal.aborted) setStationError(true) }).finally(() => { if (!controller.signal.aborted) setStationLoading(false) })
    return () => controller.abort()
  }, [stationRetry])
  const lineOptions = [...new Map(stations.map(station => [lineKey(station.kind, station.line), { kind: station.kind, name: station.line.trim() }])).entries()].sort((left, right) => left[0].localeCompare(right[0], 'en', { numeric: true }))
  const layerStations = stations.filter(station => layers[station.kind] && (layers.unverified || station.serviceStatus !== 'unverified'))
  const layerById = new Map(layerStations.map(station => [station.id, station]))
  const activeConnections = layers.unverified ? connections.filter(connection =>
    connection.source === 'user_requested' && connection.geometryStatus === 'schematic_unverified' && connection.stationIds.length === 2
    && (!selectedLine || connection.lineNames.some(name => selectedLine === lineKey(connection.kind, name)))
    && connection.stationIds.every(id => {
      const station = layerById.get(id)
      return station && station.kind === connection.kind && connection.lineNames.some(name => lineKey(station.kind, station.line) === lineKey(connection.kind, name))
    })) : []
  const connectingIds = new Set(activeConnections.flatMap(connection => connection.stationIds))
  const visibleStations = layerStations.filter(station => !selectedLine || selectedLine === lineKey(station.kind, station.line) || connectingIds.has(station.id))
  const visibleById = new Map(visibleStations.map(station => [station.id, station]))
  const visibleSegments = layers.unverified ? lines.flatMap(line => {
    if (line.geometryStatus !== 'schematic_unverified') return []
    return line.stationIds.slice(1).flatMap((stationId, index) => {
      const start = visibleById.get(line.stationIds[index]), end = visibleById.get(stationId)
      if (!start || !end || [start, end].some(station => lineKey(station.kind, station.line) !== lineKey(line.kind, line.name))) return []
      const points: [number, number][] = [[start.location.latitude, start.location.longitude], [end.location.latitude, end.location.longitude]]
      return [{ key: `${line.id}:${index}`, line, points }]
    })
  }) : []
  const connectionSegments = activeConnections.flatMap(line => {
    const start = visibleById.get(line.stationIds[0]), end = visibleById.get(line.stationIds[1])
    if (!start || !end) return []
    const points: [number, number][] = [[start.location.latitude, start.location.longitude], [end.location.latitude, end.location.longitude]]
    return [{ key: line.id, line, points }]
  })
  const segments = [...visibleSegments, ...connectionSegments]
  const projectPositions = projects.map(coordinates).filter(Boolean)
  const commuteMarkers = commutePlaces.flatMap((place, index) => {
    const point = place.precision === 'exact' ? coordinates(place) : null
    return point ? [{ place, point, number: index + 1 }] : []
  })
  const homePositions = [...projectPositions, ...commuteMarkers.map(marker => marker.point)]
  const positions = JSON.stringify([...homePositions, ...visibleStations.map(station => [station.location.latitude, station.location.longitude])])
  const unmapped = projects.filter(project => !coordinates(project)).length
  const provisional = projects.filter(project => project.locationStatus === 'provisional').length
  const shortlisted = new Set(shortlistedIds)
  const shortlistedCount = projects.filter(project => shortlisted.has(project.id) && coordinates(project)).length
  const activeProject = projects.find(project => project.id === selected)
  const focusedProject = projects.find(project => project.id === focusRequest?.projectId)
  const showSatellite = mapLayer === 'satellite' && !satelliteUnavailable
  return <section className="map-region" aria-label="Projects and stations map">
    <div className="map-layer-bar">
      <fieldset className="map-layers"><legend>Map layers</legend>
        <label className="check-label"><input type="checkbox" checked={layers.metro} onChange={event => setLayers({ ...layers, metro: event.target.checked })} /><TramFront size={17} className="metro-color" />Metro <span>{stations.filter(station => station.kind === 'metro').length}</span></label>
        <label className="check-label"><input type="checkbox" checked={layers.railway} onChange={event => setLayers({ ...layers, railway: event.target.checked })} /><TrainFront size={17} className="railway-color" />Railway <span>{stations.filter(station => station.kind === 'railway').length}</span></label>
        <label className="check-label"><input type="checkbox" checked={layers.unverified} onChange={event => setLayers({ ...layers, unverified: event.target.checked })} />Unverified stations</label>
      </fieldset>
      <div className="transit-line-controls">
        <label className="transit-line-select">Transit line<select aria-label="Transit line" value={selectedLine} onChange={event => setSelectedLine(event.target.value)}><option value="">All lines</option>{lineOptions.map(([key, line]) => <option key={key} value={key}>{line.kind === 'metro' ? 'Metro' : 'Railway'} | {line.name || 'Line unknown'}</option>)}</select></label>
      </div>
      <details className="transit-line-legend"><summary>Line colours</summary><ul>{lineOptions.map(([key, line]) => <li key={key}><span className="line-swatch" style={{ backgroundColor: lineColor(line.kind, line.name) }} aria-hidden="true" />{line.kind === 'metro' ? 'Metro' : 'Railway'} | {line.name || 'Line unknown'}</li>)}</ul></details>
      {(lines.length > 0 || connections.length > 0) && <p className="line-evidence-note">Schematic station order and requested branch connections, not verified tracks or service. Crossings do not confirm transfers.</p>}
      <div className="map-data-status" role="status"><span><ShieldCheck size={14} />{projects.filter(project => project.verificationStatus === 'verified').length} MahaRERA verified</span>
        <span>{projectPositions.length ? `${projectPositions.length} project locations shown` : 'No reviewed project locations available in this view'}</span>
        {stationLoading ? <span>Loading stations...</span> : stationError ? <span>Stations unavailable<button className="icon-button" title="Retry stations" aria-label="Retry stations" onClick={() => setStationRetry(value => value + 1)}><RefreshCw size={16} /></button></span> : <span>{visibleStations.length} stations shown</span>}
        {stationWarnings.length > 0 && <span>Incomplete or conflicting station records withheld.</span>}
        {provisional > 0 && <span>{provisional} provisional project locations - development preview, not reviewed.</span>}
        {shortlistedCount > 0 && <span><i className="shortlist-swatch" aria-hidden="true" />{shortlistedCount} shortlisted {shortlistedCount === 1 ? 'project' : 'projects'} shown</span>}
        {commutePlaces.length > 0 && <span className="commute-legend"><i className="commute-swatch" aria-hidden="true" />{commuteMarkers.length} daily destinations mapped{commuteMarkers.length < commutePlaces.length && `; ${commutePlaces.length - commuteMarkers.length} without exact locations`}</span>}
      </div>
    </div>
    <div className={`map-canvas map-canvas-${mapLayer}`}>
    <div className="map-style-switch" role="group" aria-label="Map layer">
      {(['satellite', 'commute'] as const).map(layer => <button key={layer} type="button" aria-pressed={mapLayer === layer} onClick={() => { setMapLayer(layer); setTileError(false); setSatelliteUnavailable(false) }}>{layer === 'satellite' ? 'Satellite' : 'Commute'}</button>)}
    </div>
    <MapContainer center={[19.14, 72.96]} zoom={11} className="project-map" zoomAnimation fadeAnimation scrollWheelZoom attributionControl zoomControl>
      <TileLayer key={`${retry}:${mapLayer}:${satelliteUnavailable}`} url={showSatellite ? 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}' : 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'} maxZoom={19}
        attribution={showSatellite ? 'Tiles &copy; Esri' : '&copy; OpenStreetMap contributors'}
        keepBuffer={6} updateWhenZooming={false} updateWhenIdle
        eventHandlers={{ tileerror: () => { if (showSatellite) setSatelliteUnavailable(true); else setTileError(true) } }} />
      <MapSelectionClearer onClear={() => onSelect('')} />
      <Controls positions={positions} focused={focusedProject} focusVersion={focusRequest?.version ?? 0} onFit={onFit} />
      {segments.map(segment => <Polyline key={`outline:${segment.key}`} className="transit-line-outline" positions={segment.points} interactive={false} pathOptions={{ color: '#ffffff', weight: 11, opacity: .9 }} />)}
      {segments.map(segment => <Polyline key={segment.key} className={`transit-line${segment.line.source === 'user_requested' ? ' transit-connection' : ''}`} positions={segment.points} interactive={false} pathOptions={{ color: lineColor(segment.line.kind, segment.line.name), weight: 7, opacity: 1 }} />)}
      {<ProjectMarkers projects={projects} matches={matches} selected={selected} shortlisted={shortlisted} onSelect={onSelect} />}
      {mapLayer === 'commute' && commuteMarkers.map(({ place, point, number }) => {
        const label = document.createElement('span')
        label.textContent = String(number)
        const icon = divIcon({ html: label, className: 'commute-pin', iconSize: [32, 32], iconAnchor: [16, 36] })
        return <Marker key={place.placeId} position={point} icon={icon} title={`Daily commute ${number}: ${place.label}`} alt={`Daily commute ${number}: ${place.label}`} zIndexOffset={500}>
          <Popup><div className="station-details"><strong>{place.label}</strong><span>Daily destination {number} | Private to your account</span><span>{place.frequencyPerWeek} trips per week | {place.maxOneWayMinutes} minute target</span><span>Travel time not calculated</span></div></Popup>
        </Marker>
      })}
      <StationMarkers stations={visibleStations} />
    </MapContainer>
    <div className="map-caption">Mumbai Metropolitan Region{unmapped > 0 && <span>{unmapped} {unmapped === 1 ? 'project has' : 'projects have'} no {projects.some(project => project.status !== 'published') ? 'accepted' : 'published'} coordinates</span>}</div>
    {activeProject && <div className="map-preview"><strong>{activeProject.submittedName}</strong><span>{projectStatusLabel(activeProject)}</span>{activeProject.locationStatus === 'provisional' && <span>Provisional map location - not reviewed</span>}<span>{activeProject.locality} | MahaRERA: {activeProject.verificationStatus || 'Unknown'}</span><button className="text-button" onClick={() => onInspect(activeProject)}>View project</button></div>}
    {(tileError || satelliteUnavailable) && <div className="map-warning" role="status">{tileError ? 'Basemap unavailable. Project locations and the feed remain available.' : 'Satellite imagery unavailable. Showing the street map instead.'}<button className="icon-button" title="Retry basemap" aria-label="Retry basemap" onClick={() => { setTileError(false); setSatelliteUnavailable(false); setRetry(value => value + 1) }}><RefreshCw size={18} /></button></div>}
    </div>
  </section>
}
