# Delivery Plan, Test Strategy, and Rollout

## 1. Delivery principles

- Build vertical slices that end in demonstrable user value.
- Put external integrations behind adapters and feature flags from the start.
- Do not publish unreviewed low-confidence data.
- Use representative Mumbai/MMR datasets with legal approval and synthetic personal profiles in lower environments.
- Release through a dedicated flight rather than changing legacy behavior in place.

## 2. Proposed phases

### Phase 0 — Discovery and governance (2–4 weeks)

Deliverables:
- Confirm buyer/developer research, terminology, and accessibility needs.
- Confirm MahaRERA and IndexTap/Index2 licensing/API access.
- Choose maps/routing provider and validate MMR coverage/quotas.
- Approve privacy classification, retention, consent, and data residency.
- Define project fact taxonomy, area definitions, metric methodology, and scoring v1.
- Produce threat model and representative document corpus.

Exit criteria: licensed data paths, approved MVP scope, measurable acceptance criteria, and no unresolved critical privacy/security blocker.

### Phase 1 — Foundation and buyer onboarding (4–6 weeks)

- Identity, roles, consent, API conventions, database migrations, audit, observability.
- Responsive React shell and strict security headers.
- Dual-mode canonical onboarding with autosave/version conflict handling.
- Frequent places, home/space, amenities, micro-market priorities, and budget.
- Synthetic catalog and basic explainable hard-filter + weighted ranking.

Exit criteria: end-to-end buyer flow works on desktop/mobile and meets core accessibility checks.

### Phase 2 — Developer ingestion and review (5–7 weeks)

- Organization membership and project draft.
- Secure upload/quarantine/scan pipeline.
- OCR/extraction adapter, evidence provenance, conflict tasks, reviewer console.
- MahaRERA adapter/fallback and verification snapshot.
- Atomic publication into buyer catalog.

Exit criteria: representative brochures/floor plans produce reviewed, evidence-backed facts with no credential exposure.

### Phase 3 — Recommendations and comparison (4–6 weeks)

- Routing matrix/cache and weighted-location score.
- Project detail, comparison, shortlist, share controls.
- Metric definitions and freshness/confidence handling.
- No-match and degraded-provider experiences.

Exit criteria: ranking is reproducible, explanations match calculations, and performance targets hold at expected MVP load.

### Phase 4 — Transactions and production hardening (4–6 weeks)

- Licensed IndexTap/Index2 adapter, normalization, matching confidence, summaries.
- Notifications where approved.
- Resilience, disaster recovery, operational dashboards/runbooks.
- Security testing, privacy verification, penetration test, and staged rollout.

Exit criteria: launch readiness review passes and rollback has been rehearsed.

## 3. Work breakdown / feature epics

| Epic | Key outputs | Dependencies |
| --- | --- | --- |
| E1 Identity and governance | auth, roles, consent, audit, deletion | identity/privacy decisions |
| E2 Buyer intent | guided + summary onboarding, autosave | taxonomy, UX research |
| E3 Catalog | normalized project read models | data model |
| E4 Developer ingestion | org/project flow, secure uploads | storage/scanner |
| E5 Evidence extraction | candidates, confidence, review queue | document corpus/provider |
| E6 RERA verification | adapter, discrepancy handling | legal/access approval |
| E7 Geo and commute | places, routing matrix, scoring | maps provider |
| E8 Matching | filters, weighted score, explanations | catalog + profile + metrics |
| E9 Transactions | partner adapter and summaries | partner contract |
| E10 Comparison and shortlist | detail/compare/share | catalog + matching |
| E11 Operations | dashboards, flags, reprocessing, runbooks | all services |

## 4. Definition of ready

A story is ready when it has user value, in/out scope, UX states, API/data changes, privacy classification, authorization rules, telemetry, accessibility behavior, test cases, dependency owner, and acceptance criteria. Integration stories also require a licensed test environment and documented quotas/failure behavior.

## 5. Definition of done

- Acceptance criteria met and reviewed.
- Unit, contract, integration, accessibility, and security tests appropriate to the change pass in CI.
- Object authorization and negative cases included.
- Telemetry is structured/redacted; dashboard or query exists for critical paths.
- API/OpenAPI and relevant design docs updated.
- Migrations are backward compatible or rollout-ordered.
- Feature is behind an environment-aware flag where risk warrants it.
- Operational runbook and rollback steps exist before production exposure.

## 6. Test strategy

### Frontend unit/component tests

- Guided and summary modes read/write the same draft.
- Switching modes preserves all values and validation state.
- Section completion, hard-vs-soft constraints, unit conversion, and inline errors.
- Loading, empty, stale, unauthorized, session-expired, no-match, and partner-degraded states.
- Correct route encoding and no untrusted `srcDoc`/raw HTML rendering.
- Sandbox token tests for any future isolated content: required tokens present and dangerous tokens absent.
- Theme, responsive breakpoints, keyboard navigation, focus management, and screen-reader announcements.

### Backend unit tests

- Requirement validation and optimistic concurrency.
- Hard-filter behavior and score component formulas/boundaries.
- Missing-data confidence policy and deterministic algorithm versioning.
- Fact conflict precedence, publication versioning, and self-approval denial.
- URI/host validation, media/charset/size limits, redirects, cancellation, and timeout mapping.
- Upload type/hash/size/quota policy and idempotency.
- Sensitive values never enter structured logs.

### Integration/contract tests

- Database constraints and migrations.
- Object storage quarantine/promotion lifecycle.
- Queue outbox/inbox, retries, dead letter, and replay.
- Provider adapters against recorded contract fixtures approved for storage.
- Raw/changed/missing partner fields and 401/403/404/429/5xx behavior.
- Authentication/authorization for every API route and tenant boundary.
- OpenAPI compatibility between generated frontend client and backend.

### Extraction evaluation

Maintain a versioned, licensed golden corpus covering:
- text and scanned PDFs;
- rotated/noisy pages and mixed languages;
- brochures, floor plans, tables, and price sheets;
- conflicting area/price/date claims;
- adversarial text/prompt injection;
- unsupported and malicious files.

Report precision, recall, exact match, unit accuracy, provenance-region accuracy, and reviewer correction rate per key field. Publication thresholds are field specific.

### End-to-end tests

- Buyer completes each onboarding mode, switches modes, submits, explains a recommendation, compares, and shortlists.
- Developer creates a project, verifies MahaRERA ID, uploads, monitors extraction, corrects, and submits.
- Reviewer resolves conflicts and publishes.
- Account share revocation and deletion workflow.
- Provider outage and recovery without fabricated results.

### Browser/security validation

- Normal application rejects nonce-less/unapproved scripts.
- Marketing text cannot execute HTML/JavaScript.
- Uploaded files cannot navigate or read application state.
- Any isolated interactive frame cannot access parent DOM/cookies/storage or navigate top level.
- No storage/partner credential appears in DOM, global state, network-visible app payloads, telemetry, or logs.
- CSRF, IDOR, role escalation, XSS, SSRF, upload abuse, rate limits, and cache privacy are tested.

### Performance tests

- Profile autosave bursts and conflict behavior.
- Catalog filtering and recommendation p50/p95/p99.
- Routing cache hit/miss under provider quotas.
- Concurrent upload and extraction throughput; queue-age alerting.
- Large document rejection before full buffering.
- Review queue pagination and project publication.

## 7. Environments and test data

- Local: emulators/fakes and synthetic projects.
- Development: provider sandboxes, no production personal data.
- Staging: production-like topology and approved representative documents.
- Production: separate credentials, exact allowlists, protected administrative access.

Seed data scripts must be deterministic and clearly mark synthetic records. Do not copy raw production documents or buyer locations to lower environments.

## 8. Rollout plan

1. Staff dogfood with synthetic/internal approved data.
2. Targeted developer and reviewer pilot.
3. Small invited buyer cohort in selected Mumbai micro-markets.
4. Expand catalog and traffic gradually while monitoring quality and provider limits.
5. Enable transaction summaries after matching accuracy and licensing checks.
6. Remove fallback/legacy paths only after the rollback window and data migration verification.

Use independent flags for buyer onboarding, developer uploads, automated extraction, MahaRERA verification, commute scoring, transaction summaries, and new scoring versions.

## 9. Launch gates

- No open critical/high security findings.
- Privacy/legal approval for personal, MahaRERA, maps, and transaction data.
- WCAG 2.2 AA audit for core flows.
- Recommendation explanations validated against calculations.
- Extraction quality above approved thresholds for mandatory fields.
- Backup restore, queue replay, provider outage, key rotation, and rollback rehearsed.
- On-call ownership, dashboards, alerts, runbooks, and escalation contacts complete.
- Capacity and quota headroom validated.

## 10. SLOs and alerts

Initial targets, subject to product approval:
- Buyer read/API availability: 99.9% monthly.
- p95 catalog read: <500 ms server time.
- p95 recommendation generation: <3 s with warm routing data.
- p95 profile save: <750 ms.
- 95% eligible documents begin extraction within 5 minutes under normal load.
- Critical review queue age and verification staleness have explicit alerts.

Alert on SLO burn rate, provider failures/latency, authorization anomaly, queue age, extraction failures, publication failures, cache degradation, database saturation, storage scan backlog, and CSP violations.

## 11. Rollback

- Disable the affected feature flag first.
- Stop new ingestion while preserving queued work.
- Roll back application using compatible database migrations.
- Revert published fact-set pointer to the previous immutable version.
- Revert scoring by selecting the prior algorithm version; do not overwrite snapshots.
- Revoke leaked credentials and signed actions if exposure is suspected.
- Communicate user impact using correlation/timeline data without sensitive payloads.

## 12. Required runbooks

- Provider outage/rate-limit exhaustion.
- Upload malware and parser compromise.
- Extraction backlog/dead-letter replay.
- Incorrect project publication/fact rollback.
- MahaRERA discrepancy/source unavailable.
- Transaction mis-match correction.
- Authorization/data exposure incident.
- Account deletion failure.
- Database restore and storage recovery.
- Feature-flag rollback and scoring-version rollback.
