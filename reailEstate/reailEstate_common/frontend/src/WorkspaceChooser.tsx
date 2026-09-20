import { useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { api } from './api'
import { errorMessage } from './errors'

type Organization = { id: string; legalName: string; role: string }

export function WorkspaceChooser({ onClose, onBuyer, onBuilder }: {
  onClose: () => void; onBuyer: () => void; onBuilder: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [organizations, setOrganizations] = useState<Organization[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const node = dialog.current
    node?.showModal()
    return () => node?.close()
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    api<Organization[]>('/api/v1/builder/organizations', { signal: controller.signal })
      .then(items => { if (!controller.signal.aborted) setOrganizations(items) })
      .catch(failure => {
        if (!controller.signal.aborted) setError(errorMessage(failure, 'Unable to check builder access.'))
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [attempt])
  return <dialog ref={dialog} className="login-dialog" aria-labelledby="workspace-title" onCancel={onClose}>
    <button className="close-dialog icon-button" aria-label="Close workspace selection" onClick={onClose}><X size={20} /></button>
    <h2 id="workspace-title">Choose your workspace</h2>
    <p>You are signed in. Choose how you want to use Homefinder.</p>
    <div className="workspace-options">
      <button className="primary" onClick={onBuyer}>Continue as Buyer</button>
      <p>Browse homes, save requirements and manage your shortlist.</p>
      <button className="secondary" disabled={loading || !!error || organizations.length === 0} onClick={onBuilder}>Continue as Builder</button>
      {loading ? <p role="status">Checking organization access...</p> : error ? <div role="alert"><p>{error}</p><button className="secondary" onClick={() => setAttempt(value => value + 1)}>Retry access check</button></div> : organizations.length ? <p>Manage projects for {organizations.map(item => item.legalName).join(', ')}.</p> : <p>Builder access needs an active owner or editor membership in an active organization. Ask your platform administrator to assign one.</p>}
    </div>
    <p>Choosing Builder does not grant permissions or publishing rights.</p>
  </dialog>
}
