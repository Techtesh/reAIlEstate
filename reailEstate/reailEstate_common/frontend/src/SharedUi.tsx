import { ArrowRight, Building2 } from 'lucide-react'

export function Empty({ title, detail, action, onAction }: {
  title: string
  detail: string
  action?: string
  onAction?: () => void
}) {
  return <div className="empty-state">
    <Building2 size={36} strokeWidth={1.3} />
    <h2>{title}</h2>
    <p>{detail}</p>
    {action && <button className="primary" onClick={onAction}>{action}<ArrowRight size={18} /></button>}
  </div>
}

export function SignInPrompt({ onSignIn }: { onSignIn: () => void }) {
  return <Empty
    title="A private place for your home search"
    detail="Sign in for saved requirements, personalized matches and shortlists."
    action="Sign in"
    onAction={onSignIn}
  />
}
