import { useCallback, useEffect, useId, useRef, useState, type PointerEvent, type ReactNode, type RefObject } from 'react'
import { Check, Maximize2, Pencil, Trash2, X } from 'lucide-react'
import { api, ApiError, apiPath, json } from './api'
import { errorMessage, isAbortError } from './errors'
import './BuilderMediaWorkspace.css'

export type BuilderLayout = { id: string; name: string; price: string | null; configurationType: string; carpetAreaSqFt: string | null; updatedAt: string; reviewState: string }
type Category = 'project-images' | 'amenities' | 'floor-plans' | 'layouts'
type RoomMeasurement = { room?: string; name?: string; lengthFt?: number | string | null; widthFt?: number | string | null; areaSqFt?: number | null }
type Evaluation = { state: string; label: string; error?: string; attempts?: number; retryAt?: string | null; canRun: boolean }
type MediaItem = {
  id: string; category: Category; label: string; imageUrl: string; documentId: string; pageNumber: number
  planId: string | null; updatedAt: string | null; sourceSha256: string; canCrop: boolean; canEvaluate: boolean
  assetId?: string; active?: boolean; reviewState?: string; displayName?: string
  configuration?: string; area?: string | number | null; dimensions?: RoomMeasurement[]
  roomRectangleSumSqFt?: number | null; roomCount?: number | null; bathrooms?: number | null; balconies?: number | null
  orientation?: string; confidence?: number | string | null; extractorVersion?: string; sourceKind?: string
  qualityWarning?: string; features?: Record<string, unknown>; evaluation?: Evaluation | null
}
type Media = {
  projectId: string; projectName: string; projectPrice: string | number | null; canEdit: boolean
  projectUpdatedAt: string; confirmNameChanges: boolean
  items: MediaItem[]; documents: MediaDocument[]
  profiles: { id: string; label: string }[]; drafts: { id: string; label: string; imageUrl: string; category?: Category; action?: string }[]
  jobs: { id: string; state: string; kind: string; error: string | null; planId?: string | null }[]
}
type MediaDocument = { id: string; name: string; pageCount: number; sha256: string; error?: string | null }
type Region = { label: string; x: number; y: number; width: number; height: number }
type Crop = { documentId: string; pageNumber: number; sourceSha256: string; region: Region; category: Category; rotationDegrees: number; target?: MediaItem; layoutRegions?: Region[] }
type MediaAction = 'rename' | 'set-active' | 'remove' | 'restore' | 'normalize' | 'reset-crop' | 'segment' | 'evaluate-amenities' | 'reprocess-all' | 'reprocess-failed'
type ActionValues = { action: MediaAction; targets: { planId: string; expectedUpdatedAt: string }[]; name?: string; active?: boolean; rotationDegrees?: number; reason?: string }
type Confirmation =
  | { kind: 'evaluate'; item: MediaItem }
  | { kind: 'pages'; source: Crop; category: 'floor-plans' | 'amenities' }
  | { kind: 'attach' | 'discard'; draftIds: string[]; replacementIds?: string[]; replacementTargets?: { planId: string; expectedUpdatedAt: string }[] }
  | { kind: 'action'; values: ActionValues; title: string; queues: boolean }
  | { kind: 'picture'; source: 'asset' | 'automatic' | 'upload'; assetId?: string; image?: File; version: string; requestId: string }
type Props = {
  projectId: string; locked: boolean; onChanged: (notice?: string) => void; onDirty: (dirty: boolean) => void
  renderLayout: (layout: BuilderLayout, locked: boolean, onSaved: (saved: BuilderLayout) => void, onDirty: (id: string, dirty: boolean) => void) => ReactNode
}
const tabs: { id: Category; label: string }[] = [
  { id: 'project-images', label: 'Project images' }, { id: 'amenities', label: 'Amenities' },
  { id: 'floor-plans', label: 'Floor plans' }, { id: 'layouts', label: 'Layouts' },
]
const emptyRegion = (): Region => ({ label: '', x: 0, y: 0, width: 1, height: 1 })
const hasRectangle = ({ x, y, width, height }: Region) => x !== 0 || y !== 0 || width !== 1 || height !== 1
const minimumLayoutArea = 0.02
function nextLayoutLabel(regions: Region[]) {
  let number = regions.length + 1
  while (regions.some(region => region.label === `Layout ${number}`)) number++
  return `Layout ${number}`
}
const aborted = isAbortError
const message = (error: unknown) => errorMessage(error, 'The media action could not be completed.')
const cropSourceAvailable = (document: MediaDocument | undefined) => !!document && !document.error && document.pageCount > 0
function measurement(value: string | number | null | undefined, unit: string) {
  return value != null && Number.isFinite(Number(value)) && Number(value) > 0
    ? `${Number(value).toLocaleString('en-IN', { maximumFractionDigits: 2 })} ${unit}` : 'Unknown'
}
function evaluationAction(item: MediaItem) {
  if (item.evaluation?.state === 'failed') return 'Requeue failed extraction'
  if (item.evaluation?.state === 'queued') return 'Extraction queued'
  if (item.evaluation?.state === 'running') return 'Extracting...'
  return item.evaluation?.state === 'not_queued' ? 'Queue extraction' : 'Re-evaluate layout'
}

function PrivateImage({ url, label, className, openInNewTab = false }: { url: string; label: string; className?: string; openInNewTab?: boolean }) {
  const [failed, setFailed] = useState(false)
  let source = ''
  try { source = apiPath(url) } catch { /* Do not send private media requests to other origins. */ }
  if (!source || failed) return <p role="status">Preview unavailable for {label}. Refresh media to check access or processing.</p>
  return <>
    <img className={className} src={source} alt={label} onError={() => setFailed(true)} />
    {openInNewTab && <a className="icon-button builder-media-open" href={source} target="_blank" rel="noopener noreferrer" title="Open image in new tab" aria-label={`Open ${label} image in new tab`}><Maximize2 size={18} aria-hidden="true" /></a>}
  </>
}

function MediaJobs({ media }: { media: Media }) {
  const list = useRef<HTMLUListElement>(null)
  useEffect(() => {
    const element = list.current
    if (!element) return
    const rows = Array.from(element.children).slice(0, 5)
    const resize = () => {
      element.style.maxHeight = `${rows.reduce((height, row) => height + row.getBoundingClientRect().height, 0)}px`
    }
    resize()
    const observer = new ResizeObserver(resize)
    rows.forEach(row => observer.observe(row))
    return () => observer.disconnect()
  }, [media.jobs])
  return <section aria-label="Media processing jobs"><h3>Media processing jobs</h3>
    <p>Jobs are not polled automatically. Use Refresh media and jobs to check progress before repeating any action.</p>
    {media.jobs.length > 5 && <p>Showing five jobs at a time. Scroll for more.</p>}
    {media.jobs.length ? <ul ref={list} className="builder-documents builder-media-jobs" aria-label="Recent media processing jobs" tabIndex={media.jobs.length > 5 ? 0 : undefined}>
      {media.jobs.map(job => <li key={job.id}><strong>{job.kind.replaceAll('_', ' ')}</strong>{job.planId && <span>{media.items.find(item => item.planId === job.planId)?.label ?? 'Layout evaluation'}</span>}<span>{job.state.replaceAll('_', ' ')}</span>{job.error && <span className="field-error">{job.error}</span>}</li>)}
    </ul> : <p>No recent media jobs.</p>}
  </section>
}

function CropImage({ url, rotation, pageNumber, imageRef, onReady, onError }: {
  url: string; rotation: number; pageNumber: number; imageRef: RefObject<HTMLImageElement | null>
  onReady: () => void; onError: () => void
}) {
  const [rotated, setRotated] = useState('')
  useEffect(() => {
    if (!rotation) return
    let cancelled = false
    const source = new Image()
    source.onload = () => {
      if (cancelled) return
      const canvas = document.createElement('canvas')
      const swapped = Math.abs(rotation) === 90
      canvas.width = swapped ? source.height : source.width
      canvas.height = swapped ? source.width : source.height
      const context = canvas.getContext('2d')
      if (!context) { onError(); return }
      context.translate(canvas.width / 2, canvas.height / 2)
      context.rotate(-rotation * Math.PI / 180)
      context.drawImage(source, -source.width / 2, -source.height / 2)
      setRotated(canvas.toDataURL('image/png'))
    }
    source.onerror = () => { if (!cancelled) onError() }
    source.src = apiPath(url)
    return () => { cancelled = true }
  }, [url, rotation, onError])
  if (rotation && !rotated) return <p role="status">Rotating source preview...</p>
  return <img ref={imageRef} src={rotation ? rotated : apiPath(url)} alt={`Original source page ${pageNumber}`} draggable={false} onLoad={onReady} onError={onError} />
}

export function BuilderMediaWorkspace({ projectId, locked, onChanged, onDirty, renderLayout }: Props) {
  const projectPath = `/api/v1/builder/projects/${encodeURIComponent(projectId)}`
  const root = `${projectPath}/media`
  const prefix = useId()
  const [media, setMedia] = useState<Media | null>(null), [layouts, setLayouts] = useState<BuilderLayout[]>([])
  const [tab, setTab] = useState<Category>('project-images'), [selectedId, setSelectedId] = useState('')
  const [layoutSearch, setLayoutSearch] = useState(''), [layoutSort, setLayoutSort] = useState('default')
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false)
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [conflict, setConflict] = useState(false)
  const [layoutDirty, setLayoutDirty] = useState<Set<string>>(() => new Set())
  const [crop, setCrop] = useState<Crop | null>(null), [cropReady, setCropReady] = useState(false), [cropImageError, setCropImageError] = useState(false)
  const [cropWarning, setCropWarning] = useState('')
  const [selectedDrafts, setSelectedDrafts] = useState<string[]>([]), [profile, setProfile] = useState('')
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null)
  const [selectedPlans, setSelectedPlans] = useState<string[]>([])
  const [rename, setRename] = useState<{ itemId: string; value: string } | null>(null)
  const [previewVersion, setPreviewVersion] = useState(0)
  const scope = useRef<AbortController | null>(null), pending = useRef(false)
  const drag = useRef<{ x: number; y: number; before: Crop; editing: Crop; dropped?: Region } | null>(null)
  const cropImage = useRef<HTMLImageElement>(null)
  const cropEditor = useRef<HTMLElement>(null), confirmationPanel = useRef<HTMLDivElement>(null)
  const nameButton = useRef<HTMLButtonElement>(null), wasRenaming = useRef(false)
  useEffect(() => {
    if (!rename && wasRenaming.current) nameButton.current?.focus()
    wasRenaming.current = !!rename
  }, [rename])
  const cropOpened = !!crop
  useEffect(() => { if (cropOpened) cropEditor.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }, [cropOpened])
  useEffect(() => { if (confirmation) confirmationPanel.current?.focus() }, [confirmation])
  const sourceImageReady = useCallback(() => setCropReady(true), [])
  const sourceImageError = useCallback(() => { setCropReady(false); setCropImageError(true) }, [])
  const cropRequest = useRef<{ payload: string; requestId: string } | null>(null)
  const actionRequest = useRef<{ payload: string; requestId: string } | null>(null)
  const changed = layoutDirty.size > 0
  const blocked = locked || !media?.canEdit || busy || loading || conflict
  const mutationBlocked = blocked || changed
  const reportLayoutDirty = useCallback((id: string, dirty: boolean) => {
    setLayoutDirty(current => {
      if (current.has(id) === dirty) return current
      const next = new Set(current)
      if (dirty) next.add(id); else next.delete(id)
      return next
    })
  }, [])
  useEffect(() => { onDirty(changed || !!rename || !!crop || !!confirmation || busy) }, [changed, rename, crop, confirmation, busy, onDirty])
  useEffect(() => {
    const controller = new AbortController()
    scope.current = controller
    setLoading(true)
    Promise.all([
      api<Media>(root, { signal: controller.signal }),
      api<BuilderLayout[]>(`${projectPath}/layouts`, { signal: controller.signal }),
    ]).then(([data, plans]) => {
      controller.signal.throwIfAborted()
      if (data.projectId !== projectId) throw new Error('Media does not belong to the selected project.')
      setMedia(data); setLayouts(plans)
    }).catch(failure => { if (!aborted(failure)) setError(message(failure)) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => { controller.abort(); scope.current = null }
  }, [root, projectId, projectPath])

  async function refresh(includeLayouts = !changed) {
    const controller = scope.current
    if (!controller) return
    const data = await api<Media>(root, { signal: controller.signal })
    if (data.projectId !== projectId) throw new Error('Media does not belong to the selected project.')
    const plans = includeLayouts ? await api<BuilderLayout[]>(`${projectPath}/layouts`, { signal: controller.signal }) : null
    controller.signal.throwIfAborted()
    setMedia(data)
    setPreviewVersion(current => current + 1)
    if (plans) setLayouts(plans)
    setSelectedDrafts(current => current.filter(id => data.drafts.some(draft => draft.id === id)))
    setSelectedPlans(current => current.filter(id => data.items.some(item => item.planId === id && item.reviewState !== 'removed_by_user')))
  }
  async function refreshClicked() {
    if (pending.current) return
    pending.current = true; setLoading(true); setError('')
    try { await refresh(); setConflict(false); setConfirmation(null); setNotice('Media and jobs refreshed. Local crop and layout entries have been kept. Choose an action again to confirm against the refreshed version.') }
    catch (failure) { if (!aborted(failure)) setError(message(failure)) }
    finally { pending.current = false; if (scope.current) setLoading(false) }
  }
  async function layoutSaved(saved: BuilderLayout) {
    setLayouts(current => current.map(item => item.id === saved.id ? saved : item))
    setMedia(current => current ? { ...current, items: current.items.map(item => item.planId === saved.id ? { ...item, label: saved.name, updatedAt: saved.updatedAt } : item) } : current)
    onChanged()
    pending.current = true; setLoading(true); setError('')
    try { await refresh(false) }
    catch (failure) { if (!aborted(failure)) setError(`${message(failure)} The layout was saved. Refresh media; do not repeat the save.`) }
    finally { pending.current = false; if (scope.current) setLoading(false) }
  }
  async function mutate(endpoint: string, payload: unknown, success: string, completed: () => void) {
    if (mutationBlocked || pending.current || !scope.current) { setError('Save local edits and resolve access or version conflicts before changing media.'); return }
    pending.current = true; setBusy(true); setError(''); setNotice('')
    let written = false
    try {
      await api(`${root}/${endpoint}`, { method: 'POST', body: payload instanceof FormData ? payload : json(payload), signal: scope.current.signal })
      written = true; completed(); setNotice(success)
      onChanged('Media updated. Independent review is required.')
      await refresh(true)
    } catch (failure) {
      if (!aborted(failure)) {
        if (failure instanceof ApiError && failure.status === 409) setConflict(true)
        setError(`${message(failure)} ${written ? 'The action succeeded, but refresh failed. Refresh media; do not repeat the action.' : 'No automatic retry was made. Refresh media and jobs before explicitly trying again.'}`)
      }
    } finally { pending.current = false; if (scope.current) setBusy(false) }
  }
  function startCrop(item?: MediaItem, { update = false, multipleLayouts = false }: { update?: boolean; multipleLayouts?: boolean } = {}) {
    if (crop && !window.confirm('Discard your unsaved crop selection and open another source?')) return
    const document = item ? media?.documents.find(document => document.id === item.documentId) : media?.documents.find(cropSourceAvailable)
    if (!document || !cropSourceAvailable(document)) { setError(document?.error || 'No readable source pages are available for cropping. Upload valid evidence and refresh media.'); return }
    editCrop({ documentId: document.id, pageNumber: item?.pageNumber ?? 1, sourceSha256: item?.sourceSha256 ?? document.sha256, region: { ...emptyRegion(), label: multipleLayouts ? '' : (item?.label ?? '').slice(0, 64) }, category: multipleLayouts ? 'layouts' : item?.category ?? (tab === 'project-images' ? 'layouts' : tab), rotationDegrees: 0, ...(update && item ? { target: item } : {}), ...(multipleLayouts ? { layoutRegions: [] } : {}) })
    setCropReady(false); setCropImageError(false); setError(''); setCropWarning('')
  }
  function replaceSource(documentId: string, pageNumber: number) {
    const document = media?.documents.find(document => document.id === documentId)
    if (!document || !cropSourceAvailable(document)) { setError(document?.error || 'The selected source document has no readable pages. Refresh media.'); return }
    if (crop && (json(crop.region) !== json(emptyRegion()) || crop.layoutRegions?.length) && !window.confirm('Discard all unsaved crop rectangles and labels to change the source page?')) return
    editCrop({ documentId, pageNumber, sourceSha256: document.sha256, region: emptyRegion(), category: crop?.category ?? 'layouts', rotationDegrees: 0, ...(crop?.layoutRegions ? { layoutRegions: [] } : {}) })
    setCropReady(false); setCropImageError(false); setCropWarning('')
  }
  function editCrop(next: Crop | null) {
    if (json(next) !== json(crop)) cropRequest.current = null
    setCrop(next)
  }
  function point(event: PointerEvent<HTMLDivElement>) {
    const box = cropImage.current?.getBoundingClientRect()
    if (!box?.width || !box.height) return null
    return { x: Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)), y: Math.max(0, Math.min(1, (event.clientY - box.top) / box.height)) }
  }
  function move(event: PointerEvent<HTMLDivElement>) {
    const position = point(event), origin = drag.current
    if (!position || !origin || !crop || mutationBlocked) return
    editCrop({ ...origin.editing, region: { ...origin.editing.region, x: Math.min(origin.x, position.x), y: Math.min(origin.y, position.y), width: Math.abs(origin.x - position.x), height: Math.abs(origin.y - position.y) } })
  }
  function beginRectangle(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || mutationBlocked || confirmation || !cropReady || !crop || drag.current) return
    const position = point(event)
    if (!position) return
    let regions = crop.layoutRegions
    let dropped: Region | undefined
    const keepCurrent = !!regions && hasRectangle(crop.region)
    if (regions && keepCurrent) {
      const region = { ...crop.region, label: crop.region.label.trim() || nextLayoutLabel(regions) }
      if (undersizedLayout(region)) dropped = region
      else {
        if (!validRegion(region)) return
        regions = [...regions, region]
      }
    }
    if (regions && regions.length >= 20) { setError('Save these 20 layout crops before drawing more.'); return }
    const label = regions ? (!keepCurrent && crop.region.label.trim()) || nextLayoutLabel(regions) : crop.region.label
    const editing = { ...crop, ...(regions ? { layoutRegions: regions } : {}), region: { ...crop.region, label, ...position, width: 0, height: 0 } }
    event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId)
    drag.current = { ...position, before: crop, editing, dropped }
    editCrop(editing)
  }
  function finishRectangle(event: PointerEvent<HTMLDivElement>) {
    const position = point(event), origin = drag.current
    if (origin) {
      if (!position || position.x === origin.x || position.y === origin.y || mutationBlocked) editCrop(origin.before)
      else { move(event); setError(''); if (origin.dropped) warnDropped([origin.dropped]) }
    }
    drag.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
  }
  function cancelRectangle() {
    if (drag.current) editCrop(drag.current.before)
    drag.current = null
  }
  function undersizedLayout(region: Region) {
    return !crop?.target && crop?.category === 'layouts' &&
      [region.width, region.height].every(value => Number.isFinite(value) && value >= 0) &&
      region.width * region.height + Number.EPSILON < minimumLayoutArea
  }
  function warnDropped(regions: Region[]) {
    if (regions.length) setCropWarning(`Dropped ${regions.length} crop(s) below 2% of the source image area: ${regions.map(region => region.label.trim() || 'Unnamed crop').join(', ')}. Other selections are kept.`)
  }
  function validRegion(region: Region) {
    const { x, y, width, height, label } = region
    if (!label.trim() || ![x, y, width, height].every(Number.isFinite) || x < 0 || y < 0 || width <= 0 || height <= 0 || x + width > 1.00000001 || y + height > 1.00000001) {
      setError('Enter a label and positive bounds within the page (X + width and Y + height must not exceed 100%).'); return false
    }
    if (!crop?.target && x === 0 && y === 0 && width === 1 && height === 1) {
      setError('Draw a crop rectangle or reduce its width/height before saving. To keep an entire page, use Add full page below.'); return false
    }
    const minimumArea = crop?.category === 'layouts' ? minimumLayoutArea : 0.01
    if (!crop?.target && (width * height + Number.EPSILON < minimumArea || width * height > 0.70)) {
      setError(`"${label.trim()}" covers ${(width * height * 100).toFixed(2)}% of the source page. Each new crop must cover between ${minimumArea * 100}% and 70%. Resize this rectangle before saving; your other selections are kept.`); return false
    }
    return true
  }
  function addLayoutRegion() {
    if (!crop?.layoutRegions || !cropReady) return
    if (undersizedLayout(crop.region)) {
      warnDropped([crop.region]); editCrop({ ...crop, region: emptyRegion() }); setError(''); return
    }
    if (!validRegion(crop.region)) return
    if (crop.layoutRegions.length >= 20) { setError('Save these 20 layout crops before adding more.'); return }
    editCrop({ ...crop, layoutRegions: [...crop.layoutRegions, { ...crop.region, label: crop.region.label.trim() }], region: emptyRegion() })
    setError('')
  }
  function saveCrop() {
    if (!crop || !cropReady) { setError('Load the original source page before saving a crop.'); return }
    const source = media?.documents.find(document => document.id === crop.documentId)
    if (!source || !cropSourceAvailable(source) || crop.pageNumber < 1 || crop.pageNumber > source.pageCount) { setError(source?.error || 'This source page is unavailable. Choose a readable source document and page.'); return }
    const dropped = (crop.layoutRegions ?? []).filter(undersizedLayout)
    const retained = (crop.layoutRegions ?? []).filter(region => !undersizedLayout(region))
    const regions = [...retained]
    const dropCurrent = undersizedLayout(crop.region)
    if (!crop.layoutRegions || hasRectangle(crop.region)) {
      if (!dropCurrent) regions.push({ ...crop.region, label: crop.region.label.trim() })
      else if (!crop.layoutRegions) dropped.push(crop.region)
    }
    warnDropped(dropped)
    if (dropped.length || dropCurrent) editCrop({ ...crop, ...(crop.layoutRegions ? { layoutRegions: retained } : {}), ...(dropCurrent ? { region: emptyRegion() } : {}) })
    if (!regions.length) { setError('Draw and label at least one layout crop covering 2% or more of the source image before saving.'); return }
    if (regions.length > 20) { setError('Save at most 20 layout crops at a time. Remove a crop or clear the current selection.'); return }
    if (!regions.every(validRegion)) return
    const payload = { documentId: crop.documentId, pageNumber: crop.pageNumber, sourceSha256: crop.sourceSha256, category: crop.category, regions, rotationDegrees: crop.rotationDegrees,
      ...(crop.target ? { targetPlanId: crop.target.planId, expectedUpdatedAt: crop.target.updatedAt } : {}) }
    const fingerprint = json(payload)
    if (!cropRequest.current || cropRequest.current.payload !== fingerprint) cropRequest.current = { payload: fingerprint, requestId: crypto.randomUUID() }
    void mutate('crops', { ...payload, requestId: cropRequest.current.requestId }, crop.layoutRegions ? `${regions.length} layout draft(s) saved. Select them below and attach to queue evaluation.` : 'Crop draft saved. No AI evaluation was started.', () => { editCrop(null) })
  }
  function withRequestId(endpoint: string, payload: Record<string, unknown>) {
    const fingerprint = json({ endpoint, payload })
    if (!actionRequest.current || actionRequest.current.payload !== fingerprint) actionRequest.current = { payload: fingerprint, requestId: crypto.randomUUID() }
    return { ...payload, requestId: actionRequest.current.requestId }
  }
  function saveMediaAction(values: ActionValues, queues: boolean) {
    void mutate('actions', withRequestId('actions', { confirm: true, ...(profile ? { visionProfileId: profile } : {}), ...values }), queues ? 'Processing queued. Refresh jobs to follow progress.' : 'Media updated. Independent review is required.', () => { actionRequest.current = null; setConfirmation(null); setRename(null); setSelectedPlans([]) })
  }
  function confirmAction() {
    if (!confirmation) return
    const options = { confirm: true, ...(profile ? { visionProfileId: profile } : {}) }
    if (confirmation.kind === 'picture') {
      const payload = { source: confirmation.source, expectedUpdatedAt: confirmation.version, requestId: confirmation.requestId, ...(confirmation.assetId ? { assetId: confirmation.assetId } : {}) }
      if (confirmation.image) {
        const form = new FormData()
        for (const [key, value] of Object.entries(payload)) form.append(key, value)
        form.append('image', confirmation.image)
        void mutate('picture', form, 'Project picture saved. Independent review is required.', () => setConfirmation(null))
      } else void mutate('picture', payload, 'Project picture saved. Independent review is required.', () => setConfirmation(null))
    } else if (confirmation.kind === 'action') {
      saveMediaAction(confirmation.values, confirmation.queues)
    } else if (confirmation.kind === 'pages') {
      const { source, category } = confirmation
      void mutate('pages', withRequestId('pages', { ...options, documentId: source.documentId, sourceSha256: source.sourceSha256, pages: [source.pageNumber], category }), 'Source page added; processing queued.', () => { actionRequest.current = null; setConfirmation(null); editCrop(null) })
    } else if (confirmation.kind === 'evaluate') {
      void mutate('evaluate', withRequestId('evaluate', { ...options, planId: confirmation.item.planId, expectedUpdatedAt: confirmation.item.updatedAt }), 'Layout re-evaluation queued. Refresh jobs to see progress.', () => { actionRequest.current = null; setConfirmation(null) })
    } else {
      const attaching = confirmation.kind === 'attach'
      void mutate('drafts', withRequestId('drafts', { ...(attaching ? options : { confirm: true }), action: confirmation.kind, draftIds: confirmation.draftIds, ...(confirmation.replacementIds ? { replacementIds: confirmation.replacementIds, replacementTargets: confirmation.replacementTargets } : {}) }), attaching ? 'Selected drafts attached; evaluation queued.' : 'Selected crop drafts discarded.', () => { actionRequest.current = null; setConfirmation(null); setSelectedDrafts([]); setSelectedPlans([]) })
    }
  }
  const directory = media?.items.filter(item => item.category === 'layouts' && item.label.toLocaleLowerCase().includes(layoutSearch.trim().toLocaleLowerCase())) ?? []
  if (layoutSort !== 'default') directory.sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true, sensitivity: 'base' }) * (layoutSort === 'name-desc' ? -1 : 1))
  const items = tab === 'layouts' ? directory : media?.items.filter(item => item.category === tab) ?? []
  const selected = items.find(item => item.id === selectedId) ?? items[0]
  const index = selected ? items.indexOf(selected) : -1
  function action(action: MediaAction, title: string, targets: MediaItem[] = [], extra: Partial<ActionValues> = {}, queues = false) {
    if (targets.some(item => !item.planId || !item.updatedAt)) { setError('Refresh media before changing these plans.'); return }
    const values = { ...extra, action, targets: targets.flatMap(item => item.planId && item.updatedAt ? [{ planId: item.planId, expectedUpdatedAt: item.updatedAt }] : []) }
    if (action === 'rename' && media?.confirmNameChanges === false) saveMediaAction(values, false)
    else setConfirmation({ kind: 'action', values, title, queues })
  }
  function changeActivity(item: MediaItem, active: boolean) {
    action('set-active', active
      ? item.category === 'floor-plans' ? 'Activate this floor plan? Derived layouts will stay inactive.' : 'Activate this layout?'
      : item.category === 'floor-plans' ? 'Deactivate this floor plan and its derived layouts?' : 'Deactivate this layout?',
    [item], { active })
  }
  function selectImage(item: MediaItem) {
    if (rename && !window.confirm('Discard the unsaved media name?')) return
    setRename(null); setSelectedId(item.id)
  }
  function selectTab(category: Category) {
    if (rename && !window.confirm('Discard the unsaved media name?')) return
    setRename(null); setTab(category); setSelectedId('')
  }
  const cropUrl = crop ? `${root}/documents/${encodeURIComponent(crop.documentId)}/pages/${crop.pageNumber}?sha256=${encodeURIComponent(crop.sourceSha256)}` : ''
  const cropDocument = media?.documents.find(document => document.id === crop?.documentId)
  const cropPageAvailable = !!cropDocument && cropSourceAvailable(cropDocument) && !!crop && crop.pageNumber >= 1 && crop.pageNumber <= cropDocument.pageCount
  const chosenPlans = media?.items.filter(item => item.planId && selectedPlans.includes(item.planId)) ?? []
  const confirmationTitle = !confirmation ? '' : confirmation.kind === 'action' ? confirmation.title
    : confirmation.kind === 'picture' ? 'Change the project picture?'
    : confirmation.kind === 'pages' ? `Add full source page ${confirmation.source.pageNumber} as ${confirmation.category === 'floor-plans' ? 'a floor plan' : 'amenities'}?`
    : confirmation.kind === 'evaluate' ? `${evaluationAction(confirmation.item)}: ${confirmation.item.label}?`
    : confirmation.kind === 'attach' ? `${confirmation.replacementIds ? 'Replace selected layouts with' : 'Attach'} ${confirmation.draftIds.length} selected draft(s)?`
    : `Discard ${confirmation.draftIds.length} selected draft(s)?`
  const queues = confirmation && (['attach', 'evaluate', 'pages'].includes(confirmation.kind) || (confirmation.kind === 'action' && confirmation.queues))
  return <section className="builder-panel builder-media" aria-label="Project media workspace">
    <h2>Project media workspace</h2>
    <p>{media?.projectName} {media && <>· Project price: {media.projectPrice ?? 'contact to know'}</>}</p>
    <p>Preview evidence, edit layout aliases and prices, or stage missing layouts from source pages. Changes require independent review.</p>
    {error && !crop && <p role="alert" className="field-error">{error}</p>}
    {notice && <p role="status" className="builder-notice">{notice}</p>}
    {cropWarning && !crop && <p role="alert" className="builder-notice">Warning: {cropWarning}</p>}
    {locked && <p role="status">Save project edits or resolve the project conflict before changing media. Retired and merged projects are read-only.</p>}
    {media && !media.canEdit && <p role="status">Your organization access permits viewing only. Media changes are unavailable.</p>}
    {changed && <p role="status">Save or explicitly reload your layout entries before cropping, attaching or re-evaluating. Refresh keeps these entries.</p>}
    {conflict && <p role="alert">The source or layout changed. Your local entries were kept. Refresh media, then reopen the source or confirmation using the latest version.</p>}
    {media?.documents.filter(document => !cropSourceAvailable(document)).map(document => <p key={document.id} role="alert" className="field-error">{document.name}: {document.error || 'No readable source pages are available.'} Cropping is unavailable for this document.</p>)}
    <div className="button-row"><button className="secondary" disabled={loading || busy} onClick={() => void refreshClicked()}>Refresh media and jobs</button><button className="secondary" disabled={mutationBlocked || !!rename || !media?.documents.some(cropSourceAvailable) || !!confirmation} onClick={() => startCrop()}>Crop source page</button></div>
    {loading && <p role="status">Loading media...</p>}
    <div className="builder-media-tabs" role="tablist" aria-label="Project media categories">
      {tabs.map((item, position) => <button key={item.id} id={`${prefix}-${item.id}`} role="tab" aria-selected={tab === item.id} aria-controls={`${prefix}-${item.id}-panel`} tabIndex={tab === item.id ? 0 : -1} onClick={() => selectTab(item.id)} onKeyDown={event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
        event.preventDefault()
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (position + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length
        selectTab(tabs[next].id)
        document.getElementById(`${prefix}-${tabs[next].id}`)?.focus()
      }}>{item.label}</button>)}
    </div>
    <div className={tab === 'layouts' ? 'builder-layout-explorer' : undefined}>
    {tab === 'layouts' && <aside className="builder-layout-directory" aria-label="Layout directory">
      <h3>Layouts <span>{directory.length}</span></h3>
      <label>Find a layout<input type="search" value={layoutSearch} disabled={busy || !!confirmation || !!rename} onChange={event => setLayoutSearch(event.target.value)} /></label>
      <label>Sort layouts<select value={layoutSort} disabled={busy || !!confirmation || !!rename} onChange={event => setLayoutSort(event.target.value)}><option value="default">Default order</option><option value="name">Name (A-Z)</option><option value="name-desc">Name (Z-A)</option></select></label>
      <ul aria-label="Layout list">{directory.map(item => <li key={item.id}><button className="secondary" aria-pressed={selected?.id === item.id} aria-controls={`${prefix}-layout-detail`} disabled={busy || !!confirmation} onClick={() => selectImage(item)}>
        <strong>{item.label}</strong><span>{item.active === false ? 'Inactive · ' : ''}{item.evaluation?.state?.replaceAll('_', ' ') ?? item.reviewState?.replaceAll('_', ' ') ?? 'Awaiting review'}{item.planId && layoutDirty.has(item.planId) ? ' · Unsaved edits' : ''}</span>
      </button></li>)}</ul>
      {!directory.length && <p role="status">{layoutSearch ? 'No matching layouts.' : 'No layouts available yet.'}</p>}
    </aside>}
    <div id={`${prefix}-layout-detail`} className="builder-layout-detail">
    {tabs.map(category => <div key={category.id} id={`${prefix}-${category.id}-panel`} role="tabpanel" aria-labelledby={`${prefix}-${category.id}`} hidden={tab !== category.id}>
      {tab === category.id && <>
        {selected ? <div className="builder-media-preview">
          <PrivateImage key={`${selected.id}-${selected.imageUrl}-${previewVersion}`} url={selected.imageUrl} label={selected.label} openInNewTab />
          <div className="builder-media-heading">
            {selected.planId && rename?.itemId === selected.id ? <form className="builder-media-name" aria-label="Edit media title" onSubmit={event => {
              event.preventDefault()
              if (!mutationBlocked && !confirmation) action('rename', 'Save this plan name?', [selected], { name: rename.value.trim() })
            }}>
              <input aria-label={selected.category === 'floor-plans' ? 'Floor plan name' : 'Selected layout name'} autoFocus required pattern={'.*\\S.*'} title="Enter a nonblank name." disabled={mutationBlocked || !!confirmation} maxLength={120} value={rename.value} onChange={event => setRename({ itemId: selected.id, value: event.target.value })} onKeyDown={event => {
                if (event.key === 'Escape') { event.preventDefault(); setRename(null) }
              }} />
              <button className="icon-button" type="submit" disabled={mutationBlocked || !!confirmation} title="Save media name" aria-label="Save media name"><Check size={18} aria-hidden="true" /></button>
              <button className="icon-button" type="button" disabled={busy || !!confirmation} onClick={() => setRename(null)} title="Cancel name edit" aria-label="Cancel name edit"><X size={18} aria-hidden="true" /></button>
            </form> : <h3>{selected.planId ? <button ref={nameButton} className="builder-media-title" type="button" disabled={mutationBlocked || !!confirmation} aria-label={`Edit ${selected.category === 'floor-plans' ? 'floor plan' : 'layout'} name: ${selected.label}`} title="Edit name" onClick={() => setRename({ itemId: selected.id, value: selected.displayName || selected.label })}>
              <span>{selected.label}</span><Pencil size={16} aria-hidden="true" />
            </button> : selected.label}</h3>}
          </div>
          <p>Page {selected.pageNumber} · {index + 1} of {items.length}</p>
          <div className="button-row">
            <button className="secondary" disabled={items.length < 2 || busy || !!confirmation} onClick={() => selectImage(items[(index - 1 + items.length) % items.length])}>Previous image</button>
            <button className="secondary" disabled={items.length < 2 || busy || !!confirmation} onClick={() => selectImage(items[(index + 1) % items.length])}>Next image</button>
            {selected.canCrop && cropSourceAvailable(media?.documents.find(document => document.id === selected.documentId)) && <button className="secondary" disabled={mutationBlocked || !!rename || !!confirmation} onClick={() => startCrop(selected)}>Crop this image</button>}
            {selected.category === 'floor-plans' && selected.canCrop && selected.reviewState !== 'removed_by_user' && cropSourceAvailable(media?.documents.find(document => document.id === selected.documentId)) && <button className="primary" disabled={mutationBlocked || !!rename || !!confirmation} onClick={() => startCrop(selected, { multipleLayouts: true })}>Manually crop layouts</button>}
            {selected.canEvaluate && selected.planId && <button className="secondary" disabled={mutationBlocked || !!rename || !!confirmation || !selected.updatedAt || selected.evaluation?.canRun === false || ['queued', 'running'].includes(selected.evaluation?.state ?? '')} onClick={() => setConfirmation({ kind: 'evaluate', item: selected })}>{evaluationAction(selected)}</button>}
            {selected.assetId && <button className="secondary" disabled={mutationBlocked || !!rename || !!confirmation} onClick={() => media && setConfirmation({ kind: 'picture', source: 'asset', assetId: selected.assetId, version: media.projectUpdatedAt, requestId: crypto.randomUUID() })}>Use as project picture</button>}
          </div>
          {selected.planId && <>
            <p>Status: {selected.reviewState?.replaceAll('_', ' ') ?? 'Awaiting review'} · {selected.active === false ? 'Inactive' : 'Active'}</p>
            <div className="button-row">
              {selected.reviewState === 'removed_by_user' ? <button className="secondary" disabled={mutationBlocked || !!rename || !!confirmation} onClick={() => action('restore', 'Restore this removed plan without restarting cancelled jobs?', [selected], { reason: 'Builder restored media' })}>Restore plan</button> : <>
                <button className="secondary" disabled={mutationBlocked || !!rename || !!confirmation} onClick={() => changeActivity(selected, selected.active === false)}>{selected.active === false ? 'Activate plan' : 'Deactivate plan'}</button>
                {selected.canCrop && <button className="secondary" disabled={mutationBlocked || !!rename || !!confirmation} onClick={() => startCrop(selected, { update: true })}>Reset / update crop</button>}
                {selected.canCrop && <button className="secondary" disabled={mutationBlocked || !!rename || !!confirmation} onClick={() => action('reset-crop', 'Reset this crop to its original source and re-evaluate?', [selected], {}, true)}>Restore original crop</button>}
                {selected.category === 'floor-plans' && <button className="secondary" disabled={mutationBlocked || !!rename || !!confirmation || selected.active === false} onClick={() => action('segment', 'Extract individual layouts from this floor plan?', [selected], {}, true)}>Extract layouts</button>}
                {selected.category === 'layouts' && <button className="secondary" disabled={mutationBlocked || !!rename || !!confirmation || selected.active === false} onClick={() => action('normalize', 'Rotate this layout counterclockwise by 90 degrees and re-evaluate?', [selected], { rotationDegrees: 90 }, true)}>Rotate layout 90° counterclockwise</button>}
              </>}
            </div>
          </>}
          {tab !== 'layouts' && <div className="builder-media-thumbnails" aria-label="Select preview">{items.map(item => <button key={item.id} disabled={busy || !!confirmation} aria-label={`Preview ${item.label}`} aria-pressed={selected.id === item.id} onClick={() => selectImage(item)}><PrivateImage key={`${item.imageUrl}-${previewVersion}`} url={item.imageUrl} label={item.label} /><span>{item.label}</span></button>)}</div>}
        </div> : <p>{tab === 'layouts' && layoutSearch ? 'No layout matches this search. Clear the search to see all layouts.' : `No ${category.label.toLowerCase()} available. Upload evidence or crop a source page for a missing layout.`}</p>}
      </>}
    </div>)}
    {crop && <section ref={cropEditor} className="builder-crop" aria-label="Crop source editor">
      <h3>{crop.target ? `Update crop: ${crop.target.label}` : crop.layoutRegions ? 'Manually crop layouts' : 'Crop source page'}</h3><p>Drag a rectangle on the original page, or enter percentage bounds. Saving creates a draft only, without model costs. {crop.target && 'Committing this draft updates the existing plan, keeping its identity and alias.'}</p>
      {cropPageAvailable && !cropImageError && <p><a href={apiPath(cropUrl)} target="_blank" rel="noopener noreferrer">Open original source page in new tab</a></p>}
      {crop.layoutRegions && <p>Draw each layout one after another (up to 20). Starting the next rectangle automatically keeps the previous one; no Add button is required. Overlap is allowed for shared walls and adjacent homes. Each layout must cover at least 2% of the source image area. Smaller added crops are dropped with a warning; a smaller current rectangle is silently ignored when saving. Default labels can be renamed below. Save layout drafts includes the valid current rectangle. This creates new layouts; it does not replace the floor plan.</p>}
      <fieldset disabled={mutationBlocked || !!confirmation}>
        <div className="builder-form-grid">
          <label>Source document<select value={crop.documentId} disabled={!!crop.target} onChange={event => replaceSource(event.target.value, 1)}>{media?.documents.map(document => <option key={document.id} value={document.id} disabled={!cropSourceAvailable(document)}>{document.name}{!cropSourceAvailable(document) ? ' (unavailable)' : ''}</option>)}</select></label>
          <label>Source page<select value={crop.pageNumber} disabled={!!crop.target || !cropSourceAvailable(cropDocument)} onChange={event => replaceSource(crop.documentId, Number(event.target.value))}>{Array.from({ length: cropDocument?.pageCount ?? 0 }, (_, page) => <option key={page + 1} value={page + 1}>{page + 1}</option>)}</select></label>
          <label>Crop category<select value={crop.category} disabled={!!crop.target || !!crop.layoutRegions} onChange={event => { const category = tabs.find(tab => tab.id === event.target.value)?.id; if (category) editCrop({ ...crop, category }) }}>{tabs.map(category => <option key={category.id} value={category.id}>{category.label}</option>)}</select></label>
          <label>Source rotation<select value={crop.rotationDegrees} disabled={!!crop.layoutRegions?.length} onChange={event => { setCropReady(false); setCropImageError(false); editCrop({ ...crop, rotationDegrees: Number(event.target.value), region: { ...emptyRegion(), label: crop.region.label } }) }}><option value={0}>Original</option><option value={90}>90 degrees counterclockwise</option><option value={180}>180 degrees</option><option value={-90}>90 degrees clockwise</option></select></label>
        </div>
        {crop.layoutRegions && <p>Choose rotation before drawing. Remove retained rectangles before changing rotation; their coordinates belong to the current orientation.</p>}
        {!cropPageAvailable ? <p role="alert">The selected source page is unavailable. Your crop entries have been kept; choose a readable source document and page.</p> : cropImageError ? <p role="alert">Original page preview could not be loaded. Check document access, refresh media and reopen the crop editor.</p> : <div className={`builder-crop-canvas${mutationBlocked || confirmation ? ' is-disabled' : ''}`} onPointerDown={beginRectangle} onPointerMove={move} onPointerUp={finishRectangle} onPointerCancel={cancelRectangle} onLostPointerCapture={cancelRectangle}>
          <CropImage key={`${cropUrl}-${crop.rotationDegrees}`} url={cropUrl} rotation={crop.rotationDegrees} pageNumber={crop.pageNumber} imageRef={cropImage} onReady={sourceImageReady} onError={sourceImageError} />
          {cropReady && crop.layoutRegions?.map((region, index) => <span key={index} className="builder-crop-region builder-crop-added" style={{ left: `${region.x * 100}%`, top: `${region.y * 100}%`, width: `${region.width * 100}%`, height: `${region.height * 100}%` }}><span>{index + 1}</span></span>)}
          {cropReady && (!crop.layoutRegions || json(crop.region) !== json(emptyRegion())) && <span className="builder-crop-region" style={{ left: `${crop.region.x * 100}%`, top: `${crop.region.y * 100}%`, width: `${crop.region.width * 100}%`, height: `${crop.region.height * 100}%` }} />}
        </div>}
        <label>Crop label<input value={crop.region.label} maxLength={64} onChange={event => editCrop({ ...crop, region: { ...crop.region, label: event.target.value } })} /></label>
        <div className="builder-form-grid">{(['x', 'y', 'width', 'height'] as const).map(field => <label key={field}>{field.toUpperCase()} (%)<input type="number" min="0" max="100" step="0.1" value={Number.isFinite(crop.region[field]) ? Number((crop.region[field] * 100).toFixed(4)) : ''} onChange={event => editCrop({ ...crop, region: { ...crop.region, [field]: event.target.value === '' ? NaN : Number(event.target.value) / 100 } })} /></label>)}</div>
        {crop.layoutRegions && <>
          <div className="button-row">
            <button className="secondary" disabled={!cropReady || !cropPageAvailable || crop.layoutRegions.length >= 20} onClick={addLayoutRegion}>Add layout crop</button>
            <button className="secondary" onClick={() => editCrop({ ...crop, region: emptyRegion() })}>Clear current rectangle</button>
          </div>
          <ol aria-label="Unsaved layout crops">{crop.layoutRegions.map((region, index) => <li key={index}><label>{region.label}<input aria-label={`Layout crop ${index + 1} label`} maxLength={64} value={region.label} onChange={event => editCrop({ ...crop, layoutRegions: crop.layoutRegions?.map((item, position) => position === index ? { ...item, label: event.target.value } : item) })} /></label> <button className="secondary" aria-label={`Remove crop ${index + 1}: ${region.label}`} onClick={() => editCrop({ ...crop, layoutRegions: crop.layoutRegions?.filter((_, position) => position !== index) })}>Remove</button></li>)}</ol>
          <p>{crop.layoutRegions.length} of 20 crops added. These are local selections, not saved drafts yet.</p>
        </>}
        {error && <p role="alert" className="field-error">Media action failed: {error}</p>}
        {cropWarning && <p role="alert" className="builder-notice">Warning: {cropWarning}</p>}
        {busy && <p role="status">Saving your media action. Please wait for confirmation before attaching drafts.</p>}
        <button className="primary" disabled={!cropReady || !cropPageAvailable} onClick={saveCrop}>{crop.layoutRegions ? 'Save layout drafts' : 'Save crop draft'}</button>
        <p>To process the entire source page instead of the rectangle, add it as a floor plan or amenities page.</p>
        <div className="button-row">
          <button className="secondary" disabled={!cropReady || !cropPageAvailable} onClick={() => setConfirmation({ kind: 'pages', source: crop, category: 'floor-plans' })}>Add full page as floor plan</button>
          <button className="secondary" disabled={!cropReady || !cropPageAvailable} onClick={() => setConfirmation({ kind: 'pages', source: crop, category: 'amenities' })}>Add full page as amenities</button>
        </div>
      </fieldset>
      <button className="secondary" disabled={busy} onClick={() => { if (window.confirm('Discard this unsaved crop selection?')) { editCrop(null); drag.current = null } }}>Discard local crop</button>
    </section>}
    {tab === 'layouts' && selected && <section aria-label="Selected layout evaluation" className="builder-layout-evaluation">
      <h3>Extracted room sizes</h3>
      <p role="status"><strong>{selected.evaluation?.label ?? 'Refresh media to load the evaluation status.'}</strong></p>
      {selected.evaluation?.error && <p className="field-error">Evaluation error: {selected.evaluation.error}{selected.evaluation.error === 'MANUAL_CROP_NOT_SINGLE_LAYOUT' && '. This crop appears to contain multiple homes. Update its boundary to one layout before re-evaluating.'}</p>}
      {selected.qualityWarning && <p className="field-error">Boundary review: {selected.qualityWarning}</p>}
      {selected.evaluation?.state === 'queued' && selected.evaluation.retryAt && new Date(selected.evaluation.retryAt).getTime() > Date.now() && <p>Scheduled for: {new Date(selected.evaluation.retryAt).toLocaleString()}</p>}
      {!!selected.dimensions?.length ? <div className="builder-media-table"><table><caption>Room measurements for {selected.label}</caption><thead><tr><th scope="col">Room</th><th scope="col">Length</th><th scope="col">Width</th><th scope="col">Area</th></tr></thead><tbody>
        {selected.dimensions.map((room, index) => <tr key={index}><th scope="row">{room.room || room.name || 'Room'}</th><td>{measurement(room.lengthFt, 'ft')}</td><td>{measurement(room.widthFt, 'ft')}</td><td>{measurement(room.areaSqFt, 'sq ft')}</td></tr>)}
      </tbody></table></div> : <p>{['queued', 'running'].includes(selected.evaluation?.state ?? '') ? 'Room dimensions are pending extraction, not yet missing.' : ['failed', 'cancelled'].includes(selected.evaluation?.state ?? '') ? 'Room dimensions are unavailable because evaluation did not complete. Review the error or crop boundary.' : selected.evaluation?.state === 'not_queued' ? 'No room dimensions are saved. Queue extraction to evaluate this layout.' : selected.evaluation?.state === 'crop_remaining' ? 'Finish cropping this image to an individual layout before evaluating room sizes.' : 'No readable room dimensions were extracted. Treat this as unknown, not zero.'}</p>}
      <p>Calculated room area: {measurement(selected.roomRectangleSumSqFt, 'sq ft')}. Sum of available room rectangles, not carpet area.</p>
      <dl className="builder-layout-facts">
        <div><dt>Configuration</dt><dd>{selected.configuration || 'Unknown'}</dd></div>
        <div><dt>Carpet area</dt><dd>{measurement(selected.area, 'sq ft')}</dd></div>
        <div><dt>Rooms</dt><dd>{selected.roomCount ?? 'Unknown'}</dd></div>
        <div><dt>Bathrooms</dt><dd>{selected.bathrooms ?? 'Unknown'}</dd></div>
        <div><dt>Balconies</dt><dd>{selected.balconies ?? 'Unknown'}</dd></div>
        <div><dt>Orientation</dt><dd>{selected.orientation || 'Unknown'}</dd></div>
      </dl>
      <details><summary>Features</summary><pre>{JSON.stringify(selected.features ?? {}, null, 2)}</pre></details>
      <details><summary>Extraction provenance</summary>
        <p>Source: {media?.documents.find(document => document.id === selected.documentId)?.name ?? 'Unknown'} · Page {selected.pageNumber}</p>
        <p>Image type: {selected.sourceKind?.replaceAll('_', ' ') || 'Unknown'} · Extractor: {selected.extractorVersion || 'Unknown'} · Confidence: {selected.confidence ?? 'Unknown'}</p>
        <p>Evaluation attempts: {selected.evaluation?.attempts ?? 0} · Review state: {selected.reviewState?.replaceAll('_', ' ') ?? 'Unknown'}</p>
        {cropSourceAvailable(media?.documents.find(document => document.id === selected.documentId)) && <a href={apiPath(`${root}/documents/${encodeURIComponent(selected.documentId)}/pages/${selected.pageNumber}?sha256=${encodeURIComponent(selected.sourceSha256)}`)} target="_blank" rel="noopener noreferrer">Open source page</a>}
      </details>
      <p>Extracted measurements remain subject to independent review. Use Refresh media and jobs to load processing results.</p>
    </section>}
    <section hidden={tab !== 'layouts' || !selected} aria-label="Layout editing">
      <h3>Layout price</h3><p>Edit the name in the title beneath the image. Total INR prices are editable here; extracted room sizes, carpet area and review state are read-only.</p>
      <div className="builder-media-table"><table><caption>Layout review summary</caption><thead><tr><th>Alias</th><th>Total price (INR)</th><th>Carpet area (sq ft)</th><th>Configuration</th><th>Review state</th></tr></thead><tbody>{layouts.filter(layout => layout.id === selected?.planId).map(layout => <tr key={layout.id}><th scope="row">{layout.name}</th><td>{layout.price ?? 'contact to know'}</td><td>{layout.carpetAreaSqFt ?? 'Unknown'}</td><td>{layout.configurationType || 'Unknown'}</td><td>{layout.reviewState.replaceAll('_', ' ')}</td></tr>)}</tbody></table></div>
      {layouts.map(layout => <div key={`${layout.id}-${layout.updatedAt}`} hidden={selected?.planId !== layout.id}>{renderLayout(layout, blocked || !!rename || !!confirmation, saved => void layoutSaved(saved), reportLayoutDirty)}</div>)}
    </section>
    </div>
    </div>
    {confirmation && <div ref={confirmationPanel} tabIndex={-1} className="builder-confirm" role="group" aria-label="Confirm media action">
      <h3>{confirmationTitle}</h3>
      <p>{queues ? 'This explicitly queues AI evaluation and may incur model costs. Results still require independent review; this does not publish or approve anything.' : confirmation.kind === 'discard' ? 'This removes only the selected crop drafts, not their source documents.' : 'This changes project media, not the original evidence. Independent review is still required; this does not approve or publish anything.'}</p>
      {queues && <p>Evaluation profile: {media?.profiles.find(item => item.id === profile)?.label ?? 'Server default'}</p>}
      <div className="button-row"><button className="primary" disabled={mutationBlocked} onClick={confirmAction}>{confirmation.kind === 'discard' ? 'Confirm discard' : queues ? 'Confirm and queue evaluation' : 'Confirm media change'}</button><button className="secondary" disabled={busy} onClick={() => setConfirmation(null)}>Cancel media action</button></div>
    </div>}
    {tab === 'project-images' && media && <section aria-label="Project picture settings">
      <h3>Project picture</h3><p>Select any available preview with Use as project picture, upload a picture, or restore the automatic first-page cover.</p>
      <fieldset disabled={mutationBlocked || !!rename || !!confirmation}>
        <label>Upload project picture<input type="file" accept="image/png,image/jpeg,image/webp" onChange={event => {
          const image = event.target.files?.[0]
          event.target.value = ''
          if (!image) return
          if (image.size > 10 * 1024 * 1024 || !['image/png', 'image/jpeg', 'image/webp'].includes(image.type)) { setError('Choose a PNG, JPEG or WebP project picture up to 10 MB.'); return }
          setConfirmation({ kind: 'picture', source: 'upload', image, version: media.projectUpdatedAt, requestId: crypto.randomUUID() })
        }} /></label>
        <button className="secondary" onClick={() => setConfirmation({ kind: 'picture', source: 'automatic', version: media.projectUpdatedAt, requestId: crypto.randomUUID() })}>Use automatic project picture</button>
      </fieldset>
    </section>}
    {media && <>
      <section className="builder-media-drafts" aria-label="Crop drafts"><h3>Crop drafts</h3><p>Attach new media or commit existing crop updates, or discard drafts. Categories are retained: amenity crops remain amenities, not layouts. Draft previews do not run AI.</p>
        <div className="builder-media-thumbnails">{media.drafts.map(draft => <div className="builder-media-draft" key={draft.id}><PrivateImage key={`${draft.imageUrl}-${previewVersion}`} url={draft.imageUrl || `${root}/drafts/${encodeURIComponent(draft.id)}`} label={draft.label} openInNewTab /><label><input type="checkbox" disabled={mutationBlocked || !!confirmation} checked={selectedDrafts.includes(draft.id)} onChange={event => setSelectedDrafts(current => event.target.checked ? [...current, draft.id] : current.filter(id => id !== draft.id))} />{draft.label}</label></div>)}</div>
        {!media.drafts.length && <p>No crop drafts.</p>}
        <label>Evaluation profile<select value={profile} disabled={mutationBlocked || !!confirmation} onChange={event => setProfile(event.target.value)}><option value="">Server default</option>{media.profiles.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
        <div className="button-row"><button className="primary" disabled={mutationBlocked || !selectedDrafts.length || !!confirmation} onClick={() => setConfirmation({ kind: 'attach', draftIds: [...selectedDrafts] })}>Attach selected drafts</button><button className="secondary" disabled={mutationBlocked || !selectedDrafts.length || !!confirmation} onClick={() => setConfirmation({ kind: 'discard', draftIds: [...selectedDrafts] })}>Discard selected drafts</button></div>
      </section>
      <section aria-label="Plan management"><h3>Plan management</h3><p>Checked means enabled. Uncheck a plan to disable it, then use its trash icon to delete it from the active collection. Deactivating a floor plan also deactivates its derived layouts; activating it does not reactivate them. Original evidence is retained.</p>
        <fieldset disabled={mutationBlocked || !!rename || !!confirmation}>
          {media.items.filter(item => item.planId).map(item => <div className="builder-plan-row" key={item.id}>
            <label className="builder-check"><input type="checkbox" aria-label={`Enabled: ${item.label}`} checked={item.active !== false && item.reviewState !== 'removed_by_user'} disabled={!item.updatedAt || item.reviewState === 'removed_by_user'} onChange={event => changeActivity(item, event.target.checked)} /><span>{item.label} ({item.reviewState?.replaceAll('_', ' ') ?? 'awaiting review'}, {item.active === false ? 'disabled' : 'enabled'})</span></label>
            {item.reviewState === 'removed_by_user' ? <button className="secondary" disabled={!item.updatedAt} aria-label={`Restore ${item.label}`} onClick={() => action('restore', 'Restore this removed plan without restarting cancelled jobs?', [item], { reason: 'Builder restored media' })}>Restore</button>
              : item.active === false && <button className="icon-button" disabled={!item.updatedAt} title={`Delete ${item.label}`} aria-label={`Delete ${item.label}`} onClick={() => action('remove', `Delete ${item.label}? Its source evidence will be retained.`, [item], { reason: 'Builder removed disabled media' })}><Trash2 size={18} aria-hidden="true" /></button>}
          </div>)}
          {!!selectedDrafts.length && <div className="builder-plan-replacement">
            <label>Layouts to replace with drafts<select multiple value={selectedPlans} onChange={event => setSelectedPlans(Array.from(event.target.selectedOptions, option => option.value))}>{media.items.flatMap(item => item.planId && item.category === 'layouts' && item.reviewState !== 'removed_by_user' ? [<option key={item.id} value={item.planId}>{item.label}</option>] : [])}</select></label>
            <p>Replacement is separate from enabled state. Only the layouts selected here will be replaced.</p>
            <button className="secondary" disabled={!selectedDrafts.length || !chosenPlans.length || chosenPlans.some(item => item.category !== 'layouts' || !item.updatedAt)} onClick={() => setConfirmation({ kind: 'attach', draftIds: [...selectedDrafts], replacementIds: [...selectedPlans], replacementTargets: chosenPlans.flatMap(item => item.planId && item.updatedAt ? [{ planId: item.planId, expectedUpdatedAt: item.updatedAt }] : []) })}>Replace selected layouts with drafts</button>
          </div>}
        </fieldset>
      </section>
      <section aria-label="Project processing"><h3>Project processing</h3><p>These actions queue the standard pipelines. Workers run automatically; opening media never starts extraction.</p>
        <div className="button-row">
          <button className="secondary" disabled={mutationBlocked || !!rename || !!confirmation} onClick={() => action('evaluate-amenities', 'Evaluate saved amenity pages and retry failed extraction?', [], {}, true)}>Evaluate amenity pages</button>
          <button className="secondary" disabled={mutationBlocked || !!rename || !!confirmation} onClick={() => action('reprocess-failed', 'Reprocess failed documents for this project?', [], {}, true)}>Reprocess failed documents</button>
          <button className="secondary" disabled={mutationBlocked || !!rename || !!confirmation} onClick={() => action('reprocess-all', 'Reprocess all documents for this project?', [], {}, true)}>Reprocess all documents</button>
        </div>
      </section>
      <MediaJobs media={media} />
    </>}
  </section>
}
