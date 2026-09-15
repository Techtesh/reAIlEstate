# User Flows

## 1. Buyer onboarding and recommendations

```mermaid
flowchart TD
    A[Landing page] --> B{Continue as guest or sign in}
    B -->|Guest| C[Create local draft]
    B -->|Sign in| D[Create or resume server draft]
    C --> E[Choose guided panels or summary]
    D --> E
    E --> F[Add routine destinations and frequency]
    F --> G[Set home configuration and space]
    G --> H[Select society and nearby amenities]
    H --> I[Set micro-market preferences]
    I --> J[Set budget and hard constraints]
    J --> K[Review priorities, consent, and summary]
    K --> L{Valid and signed in?}
    L -->|No, invalid| M[Focus first invalid section]
    M --> E
    L -->|Guest| N[Sign in and migrate draft]
    N --> L
    L -->|Yes| O[Submit immutable profile version]
    O --> P[Generate ranked recommendations]
    P --> Q[Inspect explanations]
    Q --> R[View details / compare / shortlist]
```

### Alternate and failure paths

- Place search unavailable: allow manual locality selection and clearly mark commute as pending.
- Autosave conflict: fetch latest version, show changed sections, and ask user to merge rather than overwrite.
- Session expired: preserve non-sensitive draft state, trigger parent sign-in, then retry once.
- No matches: show which hard constraints eliminated projects and allow direct edits; do not silently relax constraints.
- Partial metrics: display available recommendations with explicit unavailable/stale components.

## 2. Switch onboarding modes

```mermaid
sequenceDiagram
    actor Buyer
    participant UI as React UI
    participant Draft as Canonical Draft Store
    participant API as Preference API

    Buyer->>UI: Enter values in guided panel
    UI->>Draft: Normalize and update section
    Draft-->>UI: Updated completion state
    Draft->>API: Debounced save with version
    Buyer->>UI: Switch to summary mode
    UI->>Draft: Read same draft
    Draft-->>UI: Render all editable sections
    Buyer->>UI: Edit budget inline
    UI->>Draft: Update budget
    Draft->>API: Save with next expected version
```

There is no conversion between two independent forms; both modes use the same model and validation rules.

## 3. Project search, details, and comparison

1. Buyer opens recommendations or search.
2. Filters modify URL query state and request server-side results.
3. Cards show key facts, match score components, MahaRERA state, and freshness.
4. Buyer opens project details.
5. Detail view shows evidence-backed project facts, commute per destination, price definitions, amenities and alternatives, developer history, and local transaction summary.
6. Buyer adds 2–4 projects to comparison.
7. Comparison aligns units, highlights hard-constraint failures and differences, and preserves missing-data labels.
8. Buyer shortlists or creates an expiring read-only share link.

Failure behavior: a removed/unpublished project is shown as unavailable without leaking draft or moderation details.

## 4. Developer project onboarding

```mermaid
flowchart TD
    A[Developer signs in] --> B{Organization membership verified?}
    B -->|No| C[Request/join organization verification]
    B -->|Yes| D[Create project draft]
    D --> E[Enter MahaRERA ID]
    E --> F[Server resolves authoritative record]
    F --> G{Match found?}
    G -->|No/unavailable| H[Save draft and create assisted-review path]
    G -->|Yes| I[Confirm matched project]
    I --> J[Upload brochures, plans, price sheets]
    J --> K[Scan and extract asynchronously]
    K --> L[Show file-level processing status]
    L --> M[Review extracted fields and discrepancies]
    M --> N[Submit for platform review]
    N --> O{Reviewer decision}
    O -->|Changes requested| M
    O -->|Approved| P[Publish versioned project facts]
```

Developer users cannot approve their own evidence or alter authoritative snapshots.

## 5. Evidence review flow

```mermaid
sequenceDiagram
    actor Reviewer
    participant Console as Review Console
    participant API as Review API
    participant Evidence as Evidence Service
    participant Catalog as Catalog

    Reviewer->>Console: Open prioritized task
    Console->>API: Get candidate facts and conflict set
    API->>Evidence: Get authorized page/image references
    Evidence-->>API: Same-origin evidence references
    API-->>Console: Facts, confidence, provenance, history
    Reviewer->>Console: Select/correct value and enter reason
    Console->>API: Resolve task with expected version
    API->>Catalog: Save reviewed fact and audit event
    API-->>Console: Resolution result
```

Reviewers must see source location, extraction version, competing values, and prior decisions. Bulk approval is limited to low-risk fields with equivalent provenance and confidence.

## 6. MahaRERA discrepancy flow

1. Verification worker fetches/resolves an authoritative snapshot.
2. Comparison rules identify name, promoter, address, date, or configuration discrepancies.
3. Project status becomes `NeedsVerificationReview`; existing published data remains until an explicit policy says otherwise.
4. Reviewer compares source and developer evidence.
5. Reviewer confirms authoritative value, marks a legitimate alias, requests correction, or records source unavailability.
6. Publication includes status and verification timestamp; all decisions are audited.

## 7. Local-sales summary flow

1. Enrichment worker requests licensed records with server-held credentials.
2. Adapter normalizes and deduplicates records.
3. Project matcher assigns confidence using MahaRERA ID, normalized name, address, and building identifiers.
4. Low-confidence matches enter review; only approved/high-confidence records enter summaries.
5. Metrics worker computes count, median/percentiles, rate per carpet square foot where source supports it, and period coverage.
6. UI labels source, coverage, freshness, and distinction from asking prices.

## 8. Theme and responsive frontend flow

1. Application theme changes through the central theme provider.
2. Native React components update without reload.
3. Charts and maps consume semantic design tokens rather than hard-coded colors.
4. Any future isolated document receives only a validated `light`/`dark` enum in its same-origin URL and reloads; arbitrary style URLs are forbidden.
5. Mobile onboarding uses one panel per viewport; summary mode uses collapsible sections with persistent completion indicators.

## 9. Account privacy and deletion flow

1. Buyer opens privacy settings and reviews stored profile categories.
2. Buyer may remove a destination, delete requirement history according to policy, revoke shares, export data, or request account deletion.
3. Destructive requests require reauthentication and confirmation.
4. API creates an auditable deletion workflow; immediately revokes sessions/shares and schedules deletion/anonymization across stores.
5. Legal/audit retention exceptions are disclosed and access-restricted.
6. User receives completion status without exposing internal storage details.

## 10. Administrator operational flow

- View integration health, stale datasets, queue backlog, and failed jobs.
- Update taxonomies/configuration through versioned changes and feature flags.
- Reprocess selected documents by extraction version.
- Replay dead-letter jobs only after root-cause annotation.
- Suspend a project or organization with reason and notification workflow.
- Access to personal profiles is exceptional, time-bound, purpose-recorded, and audited.

## 11. Status models

### Requirement profile

`Draft → Submitted → Active → Superseded → Deleted/Anonymized`

### Project

`Draft → Verifying → Ingestion → NeedsReview → Approved → Published → Suspended/Archived`

### Document

`Uploaded → Quarantined → Ready → Extracting → NeedsReview/Approved → Published`, with terminal `Rejected` and retryable `ExtractionFailed`.

### Recommendation

`Requested → Computing → Ready`, with `PartiallyReady`, `Failed`, and `Expired` outcomes.
