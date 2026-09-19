import { useEffect, useId, useState } from 'react'
import { ChevronLeft, ChevronRight, ImageOff, Maximize2, RefreshCw } from 'lucide-react'
import { api, apiPath } from './api'
import { errorMessage } from './App'
import { calculatedRoomArea, dateLabel, inr, priceValue, projectPrice, type Project } from './buyer'
import { LayoutTable } from './LayoutTable'

type Layout = {
  id: string; name: string; configuration: string; carpetAreaSqFt: string | number | null;
  price: string | null;
  roomRectangleSumSqFt?: number | null;
  roomCount: number | null; bathrooms: number | null; balconies: number | null; orientation: string;
  reviewState: string; rooms: { room?: string; lengthFt?: number; widthFt?: number; areaSqFt?: number }[];
}
export type GalleryItem = {
  id: string; url: string; category: string; title: string; width: number; height: number;
  reviewState: string; layouts: Layout[];
}
type GalleryPage = { results: GalleryItem[]; next: string | null; access: 'preview' | 'review' | 'published' | 'restricted'; project?: Project }
const categories: Record<string, string> = { all: 'All media', 'floor-plans': 'Floor plans', layouts: 'Unit layouts', amenities: 'Amenities', general: 'Project images' }

function GalleryImage({ item }: { item: GalleryItem }) {
  const [failed, setFailed] = useState(false), [loaded, setLoaded] = useState(false), [retry, setRetry] = useState(0)
  return <div className="gallery-image">
    {failed ? <div className="gallery-image-state" role="alert"><ImageOff size={28} /><p>Image unavailable.</p><button className="secondary" onClick={() => { setFailed(false); setLoaded(false); setRetry(value => value + 1) }}><RefreshCw size={16} />Retry image</button></div> : <>
      {!loaded && <span className="gallery-image-state" role="status">Loading image...</span>}
      <img key={retry} src={`${apiPath(item.url)}${retry ? `?retry=${retry}` : ''}`} alt={item.title} width={item.width} height={item.height} onLoad={() => setLoaded(true)} onError={() => setFailed(true)} />
    </>}
  </div>
}

export function ProjectGallery({ projectId, processing = false }: { projectId: string; processing?: boolean }) {
  const categoryId = useId()
  const [data, setData] = useState<GalleryPage | null>(null), [error, setError] = useState(''), [retry, setRetry] = useState(0)
  const [category, setCategory] = useState('all'), [selectedId, setSelectedId] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    setData(null); setError('')
    async function load() {
      let next: string | null = `/api/v1/projects/${projectId}/gallery`
      const items: GalleryItem[] = [], visited = new Set<string>()
      let access: GalleryPage['access'] = 'restricted'
      let project: Project | undefined
      while (next) {
        const path = apiPath(next)
        if (visited.has(path)) throw new Error('The gallery could not be loaded completely. Please retry.')
        visited.add(path)
        const page: GalleryPage = await api(path, { signal: controller.signal })
        items.push(...page.results); access = page.access; project ??= page.project; next = page.next
      }
      if (!controller.signal.aborted) setData({ results: items, access, next: null, project })
    }
    void load().catch(failure => { if (!controller.signal.aborted) setError(errorMessage(failure)) })
    return () => controller.abort()
  }, [projectId, retry])
  const items = data?.results.filter(item => category === 'all' || item.category === category) ?? []
  const selectedIndex = Math.max(0, items.findIndex(item => item.id === selectedId)), selected = items[selectedIndex]
  return <section className="project-gallery" aria-label="Project media">
    <h3>Media and layouts</h3>
    {data?.access === 'preview' && <p className="processing-label">Temporary public preview. Includes unreviewed project data.</p>}
    {error ? <div role="alert"><p>{error}</p><button className="secondary" onClick={() => setRetry(value => value + 1)}><RefreshCw size={16} />Retry media</button></div> : !data ? <p role="status">Loading project media...</p> : data.access === 'restricted' ? <p>Processing media is restricted to project editors and reviewers.</p> : !data.results.length ? <p>{processing ? 'No media available for review yet.' : 'No approved media available yet.'}</p> : <>
      {processing && <p className="processing-label">Unpublished media. Extracted details may be unreviewed.</p>}
      <div className="gallery-toolbar"><div className="gallery-category"><label htmlFor={categoryId}>Media type</label><select id={categoryId} value={category} onChange={event => { setCategory(event.target.value); setSelectedId('') }}>{Object.entries(categories).filter(([key]) => key === 'all' || data.results.some(item => item.category === key)).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></div><span role="status">{items.length ? selectedIndex + 1 : 0} of {items.length}</span><div className="button-row">
        <button className="icon-button" title="Previous image" aria-label="Previous image" disabled={selectedIndex === 0} onClick={() => setSelectedId(items[selectedIndex - 1].id)}><ChevronLeft size={20} /></button>
        <button className="icon-button" title="Next image" aria-label="Next image" disabled={selectedIndex >= items.length - 1} onClick={() => setSelectedId(items[selectedIndex + 1].id)}><ChevronRight size={20} /></button>
        {selected && <a className="icon-button" title="Open full-size image" aria-label="Open full-size image" href={apiPath(selected.url)} target="_blank" rel="noopener noreferrer"><Maximize2 size={18} /></a>}
      </div></div>
      {selected && <>
        <GalleryImage key={selected.id} item={selected} />
        <div className="gallery-caption"><strong>{selected.title}</strong><span className={selected.reviewState === 'approved' ? 'tag' : 'processing-label'}>{selected.reviewState === 'approved' ? 'Approved' : 'Unreviewed'}</span></div>
        <div className="gallery-thumbnails" aria-label="Image thumbnails">{items.map((item, index) => <button key={item.id} type="button" aria-label={`Image ${index + 1}: ${item.title}`} title={item.title} aria-pressed={item.id === selected.id} onClick={() => setSelectedId(item.id)}><img src={apiPath(item.url)} alt="" loading="lazy" width={96} height={72} /><span>{index + 1}</span></button>)}</div>
        {selected.layouts.map(layout => <section className="layout-details" key={layout.id} aria-label={`Layout ${layout.name}`}>
          <h4>{layout.name}</h4><p>{layout.reviewState === 'approved' ? 'Approved layout' : 'Unreviewed layout'}</p>
          <dl className="layout-specs">{[['Configuration', layout.configuration || 'Unknown'], ['Total price (INR)', inr(priceValue(layout.price))], ['Carpet area', layout.carpetAreaSqFt == null ? 'Unknown' : `${layout.carpetAreaSqFt} sq ft`], ['Rooms', layout.roomCount ?? 'Unknown'], ['Bathrooms', layout.bathrooms ?? 'Unknown'], ['Balconies', layout.balconies ?? 'Unknown'], ['Orientation', layout.orientation || 'Unknown']].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
          <p>Calculated room area: {calculatedRoomArea(layout.roomRectangleSumSqFt)}. Sum of available room rectangles, not carpet area.</p>
          {layout.rooms.length > 0 && <div className="table-scroll"><table className="room-table"><caption>Room measurements</caption><thead><tr><th scope="col">Room</th><th scope="col">Length (ft)</th><th scope="col">Width (ft)</th><th scope="col">Area (sq ft)</th></tr></thead><tbody>{layout.rooms.map((room, index) => <tr key={index}><th scope="row">{room.room || 'Unnamed room'}</th><td>{room.lengthFt ?? 'Unknown'}</td><td>{room.widthFt ?? 'Unknown'}</td><td>{room.areaSqFt ?? 'Unknown'}</td></tr>)}</tbody></table></div>}
        </section>)}
      </>}
    </>}
    {processing && data?.project && <ProcessingProjectData project={data.project} />}
  </section>
}

function ProcessingProjectData({ project }: { project: Project }) {
  return <section aria-label="Unpublished project data">
    <h3>Project data</h3><p className="detail-price">{projectPrice(project)}</p>
    <p>MahaRERA {project.mahaReraId || 'ID unknown'}: {project.verificationStatus || 'Unknown'}. Checked {dateLabel(project.verificationCheckedAt)}.</p>
    <h4>Available layouts</h4><LayoutTable layouts={project.configurations} />
    <h4>Amenities</h4>{project.amenities.length ? <ul className="project-amenities">{project.amenities.map(code => <li key={code}>{code.replaceAll('_', ' ')}</li>)}</ul> : <p>Amenities unknown.</p>}
    <h4>Project evidence</h4>{project.facts?.length ? <dl className="facts">{project.facts.map((fact, index) => <div key={index}><dt>{fact.code}</dt><dd>{typeof fact.value === 'object' ? JSON.stringify(fact.value) : String(fact.value)} {fact.unit}<small>{fact.sourceClass} | {fact.status || 'Unreviewed'} | Reviewed {dateLabel(fact.reviewedAt)}</small></dd></div>)}</dl> : <p>No project facts extracted yet.</p>}
  </section>
}