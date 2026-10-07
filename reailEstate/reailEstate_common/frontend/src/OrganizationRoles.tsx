import { useEffect, useState, type FormEvent } from 'react'
import { api, json } from './api'
import { errorMessage } from './errors'

type Role = { id: string; name: string; description: string; baseRole: 'owner' | 'editor' | 'viewer' }
type RolesData = { roles: Role[]; members: { username: string; roleName: string; role: string; status: string }[] }

export function OrganizationRoles({ organizationId }: { organizationId: string }) {
  const [opened, setOpened] = useState(false), [data, setData] = useState<RolesData | null>(null)
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false), [reload, setReload] = useState(0)
  const root = `/api/v1/builder/organizations/${encodeURIComponent(organizationId)}/roles`
  useEffect(() => {
    if (!opened) return
    const controller = new AbortController()
    api<RolesData>(root, { signal: controller.signal }).then(result => { if (!controller.signal.aborted) setData(result) }).catch(failure => { if (!controller.signal.aborted) setError(errorMessage(failure)) })
    return () => controller.abort()
  }, [root, opened, reload])
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget, values = new FormData(form)
    setBusy(true); setError(''); setNotice('')
    try {
      await api(root, { method: 'POST', body: json({ name: values.get('name'), description: values.get('description'), baseRole: values.get('baseRole') }) })
      form.reset(); setNotice('Role created. Choose it below to onboard members.'); setReload(value => value + 1)
    } catch (failure) { setError(errorMessage(failure)) } finally { setBusy(false) }
  }
  async function assign(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget, values = new FormData(form)
    const usernames = [...new Set(String(values.get('usernames')).split(/[\s,]+/).filter(Boolean))]
    if (!usernames.length || usernames.length > 100) { setError('Enter between 1 and 100 existing usernames.'); return }
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await api<{ assigned: number }>(`${root}/${encodeURIComponent(String(values.get('roleId')))}/members`, { method: 'POST', body: json({ usernames }) })
      form.reset(); setNotice(`${result.assigned} members onboarded.`); setReload(value => value + 1)
    } catch (failure) { setError(errorMessage(failure)) } finally { setBusy(false) }
  }
  return <details className="builder-panel builder-roles" onToggle={event => setOpened(event.currentTarget.open)}>
    <summary>Roles &amp; member onboarding</summary><p>Create a reusable role, then apply it to several existing platform accounts. Access is limited to this organization.</p>
    {error && <p role="alert" className="field-error">{error}</p>}{notice && <p role="status">{notice}</p>}
    {!data ? <p role="status">Loading roles…</p> : <><div className="builder-role-forms">
      <form onSubmit={event => void create(event)}><h2>Create a role</h2><fieldset disabled={busy}><label>Role name<input name="name" maxLength={80} placeholder="Project team" required /></label><label>Description<input name="description" maxLength={255} /></label><label>Access<select name="baseRole" defaultValue="editor"><option value="editor">Editor — manage projects and evidence</option><option value="viewer">Viewer — no builder editing access</option><option value="owner">Owner — also manage roles and members</option></select></label><button className="primary" type="submit">Create role</button></fieldset></form>
      <form onSubmit={event => void assign(event)}><h2>Onboard members</h2><fieldset disabled={busy || !data.roles.length}><label>Role<select name="roleId" required><option value="">Choose a role</option>{data.roles.map(role => <option key={role.id} value={role.id}>{role.name} ({role.baseRole})</option>)}</select></label><label>Existing usernames<textarea name="usernames" rows={4} required placeholder="One username per line, or separated by commas" /></label><p className="data-note">Up to 100 active accounts. Selecting an existing member updates their organization role. At least one owner must remain.</p><button className="primary" type="submit">Apply role to members</button></fieldset>{!data.roles.length && <p>Create a role first.</p>}</form>
    </div><div className="builder-member-list"><table><caption>Current organization members</caption><thead><tr><th scope="col">Username</th><th scope="col">Role</th><th scope="col">Status</th></tr></thead><tbody>{data.members.map(member => <tr key={member.username}><td>{member.username}</td><td>{member.roleName}</td><td>{member.status}</td></tr>)}</tbody></table></div></>}
  </details>
}
