# MVP Backlog and Requirements Traceability

## Workspace Admin TODO

Owner: workspace administrator (user). Added 2026-09-17. Related story: US-016.

- [ ] Complete admin review for the projects intended for public Browse: check project details, extracted layouts/rooms, approved facts and location references; resolve outstanding non-publication review tasks.
- [ ] Record a MahaRERA verification attempt for each project. Keep unavailable or unsuccessful verification labelled accurately; publication does not imply verified status.
- [ ] Publish the reviewed fact sets through the reviewer/admin publication workflow, preserving its version and review checks. Do not bypass it by changing the status field directly.
- [ ] Refresh Browse and confirm the published projects appear in the feed, properties with approved coordinates have map pins, and verification labels are correct.

Saving a location alone does not publish a project. This TODO does not publish records,
run ingestion or start queued jobs; those actions remain under the administrator's control.

## Expanded ingestion increment

The current implementation maps the new upload/output requirements as follows (details in [the ingestion specification](11-document-ingestion-and-project-outputs.md)):

| Requirement | Implementation / remaining dependency |
| --- | --- |
| Common document handlers | Native PDF/DOCX/text/images; validated legacy Office/RTF/ODT/presentation/spreadsheet conversion boundary. Sandbox/converter installation remains operational setup. |
| General/floor/layout/amenity images | Bounded page classification, persisted PNGs, metadata and authenticated URI lists. Heuristic classifications require review. |
| Project fields and parcel metrics | Versioned per-document JSON, explicit conflict/unknown fields, normalized totals and effective parcel calculation. |
| MahaRERA validation and developer record | Approved adapter contract with strict registration identity and official-source provenance; live licensed access is not bundled. |
| Metro/local station journeys | Adapter fields and Google Maps directions links; actual nearest-station search and journey durations require an approved provider. |
| Social sentiment | Separate aggregate contract with source/date/sample coverage; a licensed source and methodology remain required. |
| Layout metrics | Room rectangles, room sums, parcel metric, per-tower lift ratios and JSON/CSV output. Undefined adjusted/scoring metrics remain null. |
| Todo/completed batch flow | Private upload paths, durable queued requests, manual/scheduled worker, copy/verify/commit/delete relocation. |
| Viewing outputs | Project output API, protected downloads, admin report links; prior documents must be reprocessed. |

Regression tests have been added for these boundaries. Execution evidence must be recorded when the suite is explicitly run; editor diagnostics alone are not a claim of test success.
## 1. MVP release slices

### Slice A — Save a buyer requirement

- Authentication/guest migration and consent.
- Guided panels and editable summary over one canonical draft.
- Places, home/space, amenities, micro-market preferences, budget, priorities.
- Autosave, optimistic concurrency, responsive/accessibility states.

Maps to: FR-01 through FR-06.

### Slice B — Publish a verified project

- Developer organization membership and project draft.
- Mandatory MahaRERA ID and verification attempt.
- Secure upload, scanning, extraction, provenance, conflicts, review, publication.

Maps to: FR-07 through FR-09 and FR-15.

### Slice C — Explain a recommendation

- Hard filters, weighted location and other score components.
- Recommendation cards/details, missing/fresh data, comparison, shortlist.
- Versioned algorithm and reproducible snapshot.

Maps to: FR-10, FR-11, FR-13, FR-16.

### Slice D — Add local transaction evidence

- Licensed partner adapter, project match confidence, deduplication, review, summary and methodology.

Maps to: FR-12.

## 2. Story backlog

| ID | Story | Priority | Depends on | Acceptance summary |
| --- | --- | --- | --- | --- |
| US-001 | Sign in and manage consent | P0 | Identity decision | secure session; consent versioned; unauthorized access denied |
| US-002 | Create/resume requirement draft | P0 | US-001 | draft owned, versioned, recoverable |
| US-003 | Use guided onboarding | P0 | US-002 | all required panels accessible and responsive |
| US-004 | Use editable summary | P0 | US-002 | direct section edits share canonical state |
| US-005 | Switch onboarding mode | P0 | US-003/004 | no data/validation loss |
| US-006 | Add frequent places | P0 | maps adapter | precision/frequency/modes supported; secrets server-side |
| US-007 | Define home and room needs | P0 | taxonomy | carpet area and hard/flexible needs validated |
| US-008 | Define amenities/substitutes | P0 | amenity taxonomy | substitute type/distance captured and explained |
| US-009 | Define budget | P0 | privacy approval | INR/all-in values protected and validated |
| US-010 | Submit requirement | P0 | US-003–009 | immutable version; invalid sections linked |
| US-011 | Create developer organization/project | P0 | identity/roles | scoped membership and duplicate handling |
| US-012 | Verify MahaRERA ID | P0 | approved data path | snapshot, match state, timestamp, discrepancies |
| US-013 | Upload evidence securely | P0 | storage/scanner | narrow upload action; quarantine; limits |
| US-014 | Extract facts with provenance | P0 | extraction provider | source page/region, confidence, version stored |
| US-015 | Resolve conflicts | P0 | reviewer role | reasoned, version-safe, audited resolution |
| US-016 | Publish project facts | P0 | US-012–015 | atomic immutable fact-set publication |
| US-017 | Filter hard constraints | P0 | buyer + catalog | no project violating a must-have is ranked |
| US-018 | Calculate weighted commute | P0 | routing adapter | frequency/mode/time assumptions visible |
| US-019 | Rank and explain projects | P0 | metrics | components, weights, confidence, missing data shown |
| US-020 | View project details | P0 | published catalog | source/freshness and normalized units shown |
| US-021 | Compare projects | P0 | US-020 | 2–4 aligned projects; differences/must-haves |
| US-022 | Shortlist projects | P0 | US-020 | owner-scoped idempotent changes |
| US-023 | Integrate registered sales | P1 | partner license | normalize/dedupe/match/review/attribute |
| US-024 | Share comparison | P1 | security/privacy | expiring, revocable, read-only permission |
| US-025 | Receive alerts | P1 | notification provider | opt-in, capped, unsubscribable |
| US-026 | Delete/export account data | P0 | retention policy | reauth, workflow, completion/audit |
| US-027 | Operate review/reprocessing | P0 | admin controls | audited flags, replay, version selection |

## 3. Traceability matrix

| Requirement | Stories | HLD components | Primary tests |
| --- | --- | --- | --- |
| FR-01 | US-001, US-026 | Identity/Access, Preference | auth/object-access/privacy E2E |
| FR-02 | US-002–005 | React onboarding, Preference | state-preservation, accessibility, conflicts |
| FR-03 | US-006, US-018 | Geo, Matching | provider contract, commute formula, privacy logs |
| FR-04 | US-007 | Preference, Catalog | validation/unit tests |
| FR-05 | US-008 | Preference, Geo, Matching | substitution and explanation tests |
| FR-06 | US-009 | Preference, Matching | budget boundaries, redaction |
| FR-07 | US-011, US-013 | Documents, Catalog | upload/tenant/security E2E |
| FR-08 | US-014, US-015 | Extraction, Review | golden corpus, conflict/publication tests |
| FR-09 | US-012 | Verification | adapter/status/discrepancy tests |
| FR-10 | US-017–019 | Matching, Metrics, Geo | deterministic scoring, hard constraints |
| FR-11 | US-020–022, US-024 | Catalog, frontend detail/compare | responsive, source labels, authorization |
| FR-12 | US-023 | Transactions | normalization, false-match, attribution |
| FR-13 | US-018–019 | Metrics | definition/version/reproduction |
| FR-14 | US-025 | Notifications | consent, frequency, unsubscribe |
| FR-15 | US-015, US-027 | Admin/Review | role, audit, replay |
| FR-16 | all | Observability | correlation, redaction, alerts |

## 4. Cross-cutting engineering tasks

- Establish repository, CI, environments, secrets, dependency scanning, and deployment pipeline.
- Add OpenAPI generation and typed frontend client compatibility checks.
- Add database migration strategy, outbox/inbox, audit library, redaction policy, and feature-flag provider.
- Implement strict application security headers and object-authorization test helpers.
- Establish golden document corpus and score-calculation fixtures.
- Create dashboards, alerts, SLOs, and runbooks before production.

## 5. Decisions blocking implementation

| Decision | Owner type | Needed by |
| --- | --- | --- |
| Identity provider/session model | Architecture/Security | Slice A |
| Maps/routing provider and MMR quota | Product/Procurement | US-006/018 |
| MahaRERA legal/technical access | Legal/Data | Slice B |
| IndexTap/Index2 contract and redistribution | Legal/Data | Slice D |
| Extraction provider and data residency | Architecture/Privacy | US-014 |
| Canonical taxonomy and scoring policy v1 | Product/Data Science | Slice C |
| Retention/deletion periods | Privacy/Legal | Foundation |
| Supported document types/limits | Security/Product | US-013 |
| Public vs sign-in-only catalog policy | Product/Security | US-020 |

## 6. Suggested first sprint

1. Confirm decisions and create architecture decision records.
2. Scaffold React/ASP.NET solution and CI without production deployment.
3. Implement identity abstraction, local development identity, and authorization policies.
4. Implement requirement profile schema/database/API.
5. Build canonical draft provider plus two-mode skeleton for Places and Home sections.
6. Add validation, ETag conflicts, safe telemetry, and accessibility test harness.
7. Seed synthetic projects and implement hard-filter proof of concept.

Demo: a user creates a draft, enters places/home needs in guided mode, switches to summary without data loss, saves, and sees synthetic eligible/ineligible results with reason codes.
