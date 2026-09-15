# Security, Privacy, and Threat Model

## 1. Security objectives

- Prevent cross-user and cross-organization data access.
- Keep buyer destinations, budget, uploaded evidence, partner credentials, and storage credentials confidential.
- Prevent malicious documents or external URLs from turning backend workers into execution or SSRF primitives.
- Keep published facts and scoring explanations tamper-evident and auditable.
- Isolate any untrusted rich/interactive content from the main application origin.

## 2. Assets and trust boundaries

Assets include sessions/tokens, buyer profiles, precise coordinates, budgets, unpublished files, evidence, organization memberships, authoritative snapshots, partner records, scores, audit events, provider credentials, and encryption keys.

Trust boundaries exist between browser/API, buyer/developer tenants, API/storage, API/queue, worker/document parser, platform/external providers, and reviewer/admin interfaces.

## 3. Threats and mitigations

| Threat | Example | Required mitigations |
| --- | --- | --- |
| Broken object authorization | Buyer changes a route ID | Object-level policy checks; opaque IDs; tests across tenants; safe 404/403 policy |
| Privilege escalation | Developer approves own claims | Separate reviewer role; organization scope; deny self-approval; audited publication |
| XSS | Marketing content contains scripts | Render as text/sanitize strict subset; strict CSP; no untrusted `srcDoc`/raw HTML |
| Untrusted iframe escape | Interactive content reads parent | Separate document response, dedicated CSP, sandbox without `allow-same-origin`/forms/top-navigation |
| SSRF | Uploaded content or API parameter references internal URL | Never accept fetch URLs from browser/docs; provider adapters use exact HTTPS allowlists; validate ports/userinfo; redirects disabled/revalidated |
| Malicious upload | Polyglot PDF, macro, parser exploit | Quarantine, size/type/hash checks, malware scan, isolated low-privilege parser, no macros/archives in MVP |
| Credential leakage | SAS/partner URL appears in Redux or logs | Server-side resolution; scoped upload action only; redaction; no secrets in browser state or telemetry |
| Data poisoning | Developer submits false marketing facts | Provenance, authoritative comparison, confidence thresholds, human review, immutable history |
| Score manipulation | Sponsored project gets organic boost | Versioned deterministic policy; sponsorship separate and labeled; audit inputs |
| Partner compromise | Provider returns active/unbounded content | Typed parsing, body/media/charset bounds, schema validation, circuit breaker, no header forwarding |
| Enumeration | Project/profile IDs probed | Rate limits, opaque IDs, consistent safe errors, object authorization |
| CSRF | Cookie-authenticated mutation | SameSite cookies, anti-forgery token, origin checks; no state changes via GET |
| Replay/duplicate jobs | Upload event processed repeatedly | Idempotency keys, inbox/outbox, immutable hashes, version checks |
| Sensitive logging | Exact address logged in query/body | Structured allowlist logging, body suppression, field classification/redaction |
| Model prompt injection | Brochure text instructs extraction model | Treat text as data, fixed schema/tool restrictions, no autonomous external actions, output validation |

## 4. Authentication and authorization

- Use an established OIDC identity provider, secure HTTP-only cookies or correctly scoped bearer tokens, short session lifetime, rotation, and revocation.
- Require MFA/conditional access for reviewer/admin roles.
- Policy-based authorization combines role, organization membership, resource ownership, publication state, and share permission.
- Reauthenticate for account deletion, organization ownership transfer, evidence download, and high-impact admin actions.
- Service identities use least privilege and separate identities for API, workers, and deployment.

## 5. Frontend security

Following the supplied safe HTML-serving reference:

- Keep the normal app CSP strict; do not add `'unsafe-inline'` globally to make a feature work.
- Never return arbitrary HTML to React for `srcDoc` and never expose signed storage URLs.
- Do not use `dangerouslySetInnerHTML` for project claims. Use typed components and text nodes.
- If interactive generated HTML becomes a requirement, serve it from a narrowly authorized same-origin route as a separate document, with endpoint-specific CSP and `sandbox="allow-scripts allow-popups"`. Omit `allow-same-origin`, forms, downloads, top-navigation, and sandbox escape.
- Authentication failures for embedded routes return 401/403, not identity-provider pages inside the frame; the parent handles reauthentication.
- Do not trust request `Host` to construct security allowlists. Configure exact application origins per environment.

Baseline application CSP should use explicit origins and nonce/hash-based scripts; include `object-src 'none'`, `base-uri 'none'`, restrictive `frame-ancestors`, and minimal `connect-src`, `img-src`, `style-src`, and `font-src`.

## 6. Upload security

1. API authorizes project edit and validates filename metadata, extension, declared type, byte limit, and quota.
2. API issues a short-lived single-object upload action; it cannot list or overwrite another object.
3. Object remains quarantined and cannot be downloaded by ordinary users.
4. Scanner verifies size, hash, detected type, malware status, and parser policy.
5. Worker runs with no unnecessary network access and bounded CPU/memory/time.
6. Safe derived previews are generated; originals are downloadable only where necessary and with safe disposition.
7. Rejected files expose stable reason categories, not scanner/parser internals.

## 7. External request validation

- External locations are produced only by configured adapters, not supplied as arbitrary caller URLs.
- Require absolute HTTPS and exact host allowlists; reject userinfo, fragments, unexpected ports, malformed hosts, wildcards, and IP literals unless explicitly required and reviewed.
- Disable automatic redirects. If a provider requires redirects, validate each destination and limit hops.
- Apply DNS/rebinding defenses appropriate to the HTTP stack/network: outbound firewall/proxy allowlists and private-address denial.
- Bound timeout, body size, decompression, media type, charset, and retries.
- Never forward provider `Set-Cookie`, `Location`, CSP, or cache headers to users.

## 8. Privacy design

- Offer approximate locality instead of precise addresses.
- Explain why destination frequency and budget are needed before collection.
- Do not reveal buyer data to developers; provide aggregate demand only after privacy thresholds and review.
- Separate consent for saved profiles, notifications, sharing, and analytics where required.
- Restrict analytics events to coarse categories and opaque IDs.
- Provide access/export, correction, sharing revocation, and deletion workflows.
- Conduct a DPIA/privacy review before production and review Indian data-protection requirements and cross-border provider processing.

## 9. Secret and key management

- Secrets reside in a managed secret store, never source control or frontend configuration.
- Prefer workload identities over long-lived keys.
- Rotate provider credentials and signing/encryption keys; audit secret access.
- Separate environment credentials and prevent production fallback to development values.
- Signed upload/download actions use minimal permissions, short expiry, exact object scope, and no logging.

## 10. Response and browser protections

- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin` for normal app; `no-referrer` for isolated sensitive documents
- restrictive `Permissions-Policy`
- HSTS in production
- secure, HTTP-only, SameSite cookies
- no-store for personal/profile responses; carefully versioned public catalog caching
- `Content-Disposition: attachment` for unsafe original types and `inline` only for approved safe representations
- clickjacking policy consistent with approved embedding hosts

## 11. Audit and incident response

Audit authentication changes, consent, organization membership, upload lifecycle, review decisions, publication, overrides, shares, exports/deletion, feature flags, and dead-letter replay. Events contain actor/entity/action/reason/correlation/time, not sensitive payloads.

Create alerts for authorization-denial spikes, upload malware, partner anomalies, data-export volume, admin privilege changes, stale verification, extraction error spikes, and unexpected CSP violations. Incident procedures must support credential revocation, project suspension, document quarantine, fact rollback, and affected-user assessment.

## 12. Security acceptance gates

- Threat-model review completed for uploads, provider integrations, and any embedded content.
- SAST, dependency, secret, IaC/container, and malware-scanner checks pass according to policy.
- Object authorization tests cover every resource route.
- Browser tests prove strict CSP and untrusted-content isolation.
- No raw credentials/signed URLs appear in DOM, client state, telemetry, or logs.
- Penetration testing includes IDOR, XSS, SSRF, upload/parser abuse, CSRF, role escalation, and rate-limit bypass.
- Data retention/deletion and backup-restore controls are verified before production.
