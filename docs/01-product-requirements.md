# Mumbai New-Construction Discovery Platform — Product Requirements

## 1. Product vision

Build a trustworthy residential discovery and decision-support platform for Mumbai Metropolitan Region (MMR) home buyers, focused on new and under-construction projects. The product converts buyer preferences and heterogeneous developer documents into comparable, evidence-backed project recommendations.

## 2. Problem statement

Buyers currently compare projects using inconsistent brochures, incomplete listings, subjective marketing claims, and travel estimates that do not reflect their actual routines. Developers also submit data in inconsistent formats. The platform must normalize both sides, validate projects against MahaRERA, and explain why each recommendation fits the buyer.

## 3. Personas

- **Buyer:** creates requirements, compares projects, reviews commute and quality-of-life trade-offs, and shortlists homes.
- **Co-buyer/family member:** reviews and edits a shared requirement profile and shortlist.
- **Developer representative:** creates an organization/project, submits MahaRERA ID and uploads project evidence.
- **Data reviewer:** resolves extraction conflicts and approves evidence-backed project facts.
- **Platform administrator:** manages taxonomies, integrations, access, moderation, and audit records.

## 4. Goals

1. Capture structured buyer intent without forcing buyers to understand real-estate terminology.
2. Support both guided interactive onboarding and a compact editable-list experience.
3. Accept heterogeneous project documents and extract normalized facts with provenance.
4. Verify project identity and claims against MahaRERA.
5. Rank projects using transparent travel, budget, space, amenity, and micro-market metrics.
6. Show local transaction evidence from approved data partners such as IndexTap/Index2.
7. Protect personal location, financial, and identity data by default.

## 5. Non-goals for MVP

- Property booking, token payment, mortgage underwriting, legal title certification, or escrow.
- Resale and rental inventory.
- A guarantee that predicted commute time, price, possession date, or investment return is exact.
- Fully automatic publication of low-confidence extracted claims.
- Scraping sources where licensing or terms do not permit it.

## 6. Feature requests

### FR-01 Buyer authentication and consent — P0

**Story:** As a buyer, I want to sign in and control use of my personal data so that saved preferences remain private.

**Requirements**
- Support secure sign-in, session expiry, logout, and account deletion request.
- Obtain explicit consent before saving frequent places or sharing a profile.
- Allow a guest draft in local session storage; require sign-in before server persistence.
- Never expose access tokens, partner credentials, or raw internal service URLs in browser state.

**Acceptance criteria**
- Unauthorized access to another profile returns 403/404 without revealing its existence.
- Location and budget telemetry is redacted or aggregated.
- Consent version and timestamp are auditable.

### FR-02 Dual-mode requirement onboarding — P0

**Story:** As a buyer, I want either a guided wizard or an editable summary so that I can provide requirements in the manner I prefer.

**Requirements**
- Guided panels: places/visit frequency, configuration, room/area expectations, society amenities, nearby public equivalents, micro-market preferences, budget, and free-form needs.
- Summary mode shows all sections, completion status, validation errors, and direct edit actions.
- Switching modes must preserve one canonical draft.
- Autosave signed-in drafts and recover interrupted sessions.
- Support back/next, keyboard navigation, mobile layouts, and screen readers.

**Acceptance criteria**
- A user can switch modes without losing entered values.
- Invalid ranges are explained inline and block final submission, not intermediate navigation.
- Optional fields are clearly distinguished from required fields.

### FR-03 Frequent-place and commute preferences — P0

**Story:** As a buyer, I want to enter places I visit and their frequency/mode so recommendations reflect my routine.

**Requirements**
- Search/select map places; store stable place identifiers and coordinates server-side.
- Capture trips per week, preferred transport modes, arrival windows, and maximum acceptable one-way duration.
- Permit approximate area selection instead of an exact address.
- Show a weighted commute score and per-destination estimates.

**Acceptance criteria**
- Duplicate destinations can be merged.
- Ranking recomputes when frequency or travel mode changes.
- Exact personal addresses are not shown to developers.

### FR-04 Home configuration and space preferences — P0

- Capture BHK type, carpet-area range, bathroom count, balconies, parking, accessibility, orientation/floor preferences, and optional room dimension minima.
- Distinguish hard constraints from preferences.
- Use carpet area as the canonical comparison; clearly label other area definitions.
- Validate unit conversions and impossible ranges.

### FR-05 Amenity substitution model — P0

- Capture required/preferred society amenities.
- Capture whether a nearby public/private equivalent is acceptable, with maximum travel distance/time.
- Display the evidence source and distance for matched alternatives.
- Never treat a public equivalent as identical to an in-building amenity; explain the trade-off.

### FR-06 Budget and affordability — P0

- Capture purchase budget range, all-in budget, financing intent, and tolerance for additional statutory/maintenance charges.
- Display price freshness and included/excluded components.
- Treat budget as sensitive personal data.
- Flag estimates; do not present them as binding offers.

### FR-07 Project and developer onboarding — P0

- Authorized developer users create organizations and project drafts.
- MahaRERA ID is mandatory before submission for publication.
- Upload supported brochures, price sheets, floor plans, layout images, and marketing material.
- Every uploaded file has malware scanning, type validation, size limits, retention policy, and audit metadata.
- Developer must attest that uploaded content may be processed and displayed.

### FR-08 Document extraction and human review — P0

- Extract project name, location, towers, configurations, carpet areas, room dimensions, amenities, pricing, possession dates, approvals, and floor-plan metadata.
- Store each extracted value with source document, page/region, extraction model/version, confidence, and review status.
- Conflicting values become review tasks; low-confidence facts do not auto-publish.
- Original evidence remains immutable; corrected facts preserve history.

### FR-09 MahaRERA verification — P0

- Validate the identifier format and resolve the authoritative project record through an approved API/data workflow.
- Compare submitted name, promoter, address, registration/validity dates, possession date, and declared inventory attributes.
- Classify matches as verified, discrepancy, unavailable, or expired/revoked where source supports it.
- Show verification timestamp and authoritative-source link; do not imply legal certification.

### FR-10 Search, matching, and explainable ranking — P0

- Filter by hard constraints before weighted ranking.
- Score commute, budget, configuration/space, society amenities, nearby amenities, micro-market quality, project confidence, and optional user priorities.
- Show score components, disqualifiers, stale/missing evidence, and configurable user weights.
- Prevent sponsored placement from changing the organic score; label sponsored content.

### FR-11 Project details and comparison — P0

- Show normalized facts, MahaRERA status, price/area ranges, floor plans, commute, nearby amenities, micro-market metrics, developer history, local transactions, evidence timestamps, and disclaimers.
- Compare 2–4 projects side by side using identical units and definitions.
- Permit shortlist, notes, and shareable read-only comparison links with expiry/revocation.

### FR-12 Local sales integration — P1

- Integrate with licensed transaction sources such as IndexTap/Index2 through a server-side adapter.
- Normalize registration date, consideration, area, rate per square foot, unit type, and project match confidence.
- Deduplicate records and disclose coverage/freshness.
- Summaries must link to evidence and distinguish registered transactions from asking prices.

### FR-13 Project metrics — P1

- Calculate price per carpet square foot, price percentile, commute fit, amenity fit, configuration fit, evidence completeness/freshness, developer delivery history, and micro-market indicators.
- Version metric definitions and retain the input snapshot used for each calculation.
- Label predictive or subjective metrics and explain methodology.

### FR-14 Notifications — P1

- Opt-in alerts for price updates, new matching projects, MahaRERA changes, possession updates, and document corrections.
- Channel preferences, frequency caps, unsubscribe, and audit trail are required.

### FR-15 Administration and data quality — P0

- Manage taxonomies, source allowlists, feature flags, review queues, duplicate projects, user roles, and integration health.
- All privileged changes require audit records.
- Provide reprocessing controls by extraction/model version.

### FR-16 Observability and support — P0

- Correlation IDs across browser, API, workers, and integrations.
- Metrics for onboarding completion, extraction failures, review backlog, ranking latency, source freshness, and partner availability.
- User-facing errors contain a correlation ID but no internal details.

## 7. Quality attributes

- **Security:** OWASP ASVS-aligned controls, least privilege, strict CSP, server-side partner calls, encrypted data in transit/at rest.
- **Privacy:** data minimization, purpose limitation, retention/deletion controls, approximate-location option.
- **Availability target:** 99.9% monthly for buyer read flows in production.
- **Performance:** p95 API reads under 500 ms excluding partner calls; initial recommendations under 3 seconds where caches are warm.
- **Accessibility:** WCAG 2.2 AA target.
- **Responsive UI:** supported on current desktop and mobile browsers.
- **Auditability:** all published facts and scores trace to versioned evidence and calculations.

## 8. Success metrics

- Onboarding completion rate and median completion time.
- Percentage of recommendations with a viewed explanation.
- Shortlist and comparison conversion rates.
- Percentage of published facts backed by reviewed evidence.
- MahaRERA verification success/discrepancy rates.
- Extraction precision/recall for key fields and reviewer correction rate.
- Search-to-detail latency, integration error rates, and stale-source percentage.

## 9. Dependencies and open decisions

- Licensed access and permitted use for MahaRERA and transaction data.
- Mapping/routing provider, quotas, traffic model, and India data residency requirements.
- Identity provider and developer-organization verification process.
- Final scoring weights and policy for missing data.
- Retention periods for uploaded documents, personal places, and audit logs.
- Definition and licensed sources for subjective micro-market quality metrics.
