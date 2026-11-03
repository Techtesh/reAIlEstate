import { useEffect, useState } from 'react'
import { apiPath } from './api'
import { useProjectWorkspace } from './projectWorkspace'
import { errorMessage } from './errors'

type Document = { id: string; originalName: string; byteLength: number; status: string; scanStatus: string; errorCategory?: string }
type Metadata = { pageCount: number; previewError: string; mediaType: string; sha256: string }
export function DocumentBrowser({ documents, projectId }: { documents: Document[]; projectId: string }) {
  const { api, path } = useProjectWorkspace()
  const [selectedId, setSelectedId] = useState(''), [metadata, setMetadata] = useState<Metadata | null>(null)
  const [page, setPage] = useState(1), [zoom, setZoom] = useState(1), [error, setError] = useState(''), [imageError, setImageError] = useState(false)
  const [revision, setRevision] = useState(0)
  const selected = documents.find(document => document.id === selectedId) ?? documents[0]
  const original = selected ? path(`/api/v1/builder/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(selected.id)}/original`) : ''
  useEffect(() => {
    const scope = new AbortController(); setMetadata(null); setPage(1); setZoom(1); setError(''); setImageError(false)
    if (original && selected?.scanStatus === 'clean') api<Metadata>(`${original}?metadata=1`, { signal: scope.signal }).then(setMetadata).catch(e => { if (!scope.signal.aborted) setError(errorMessage(e)) })
    return () => scope.abort()
  }, [original, selected?.scanStatus, api, revision])
  const image = selected && metadata?.pageCount && metadata.sha256 ? path(`/api/v1/builder/projects/${encodeURIComponent(projectId)}/media/documents/${encodeURIComponent(selected.id)}/pages/${page}?sha256=${encodeURIComponent(metadata.sha256)}`) : ''
  return <div className="document-browser"><div className="document-list" role="group" aria-label="Project document list" onKeyDown={event => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
    const rows = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('.document-row'))
    const index = rows.indexOf(event.target as HTMLButtonElement)
    if (index < 0) return
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? rows.length - 1 : Math.max(0, Math.min(rows.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)))
    event.preventDefault(); rows[next]?.focus(); rows[next]?.click()
  }}>
    {documents.map(document => <button key={document.id} className="document-row" tabIndex={document.id === selected?.id ? 0 : -1} aria-pressed={document.id === selected?.id} onClick={() => setSelectedId(document.id)} title={document.originalName}>
      <strong>{document.originalName}</strong><span>{document.byteLength < 1024 * 1024 ? `${(document.byteLength / 1024).toFixed(1)} KB` : `${(document.byteLength / 1024 / 1024).toFixed(1)} MB`} · {document.status.replaceAll('_', ' ')}</span><span>Scan: {document.scanStatus}{document.errorCategory ? ` · ${document.errorCategory}` : ''}</span>
    </button>)}
  </div><section className="document-preview" aria-label="Selected document preview"><header><h3>{selected?.originalName || 'Select a document'}</h3>
    {selected?.scanStatus === 'clean' && <a href={apiPath(original)} target="_blank" rel="noopener noreferrer">Open document in new tab</a>}</header>
    {!selected ? <p>No evidence uploaded yet.</p> : selected.scanStatus !== 'clean' ? <p>Preview unavailable until a clean malware scan completes.</p> : error ? <div role="alert"><p>{error}</p><button className="secondary" onClick={() => setRevision(value => value + 1)}>Retry preview</button></div> : !metadata ? <p role="status">Loading preview…</p> : metadata.pageCount ? <>
      <div className="document-preview-toolbar">{metadata.pageCount > 1 ? <><button className="secondary" disabled={page <= 1} onClick={() => { setPage(p => p - 1); setImageError(false) }}>Previous</button><label>Page<input aria-label="Preview page" type="number" min="1" max={metadata.pageCount} value={page} onChange={event => { const next = Number(event.target.value); if (Number.isInteger(next) && next >= 1 && next <= metadata.pageCount) { setPage(next); setImageError(false) } }} /></label><span>of {metadata.pageCount}</span><button className="secondary" disabled={page >= metadata.pageCount} onClick={() => { setPage(p => p + 1); setImageError(false) }}>Next</button></> : <span>Image preview</span>}
        <label>Preview zoom<select value={zoom} onChange={event => setZoom(Number(event.target.value))}><option value="1">Fit width</option><option value="1.5">150%</option><option value="2">200%</option></select></label></div>
      <div className={`document-preview-image preview-zoom-${zoom * 100}`}>{imageError || !image ? <div role="alert"><p>Preview could not load. Open the original or try again.</p><button className="secondary" onClick={() => setRevision(value => value + 1)}>Retry preview</button></div> : <img key={image} src={apiPath(image)} alt={`${selected.originalName}, page ${page}`} onError={() => setImageError(true)} />}</div>
    </> : <p>{metadata.previewError} Formats your browser cannot display will download.</p>}
  </section></div>
}
