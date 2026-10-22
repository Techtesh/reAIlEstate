# Launch Review: Scope and Decisions

## Current project picture

This repository contains a multi-service real-estate platform with a Django backend, React frontends, and a substantial set of product specifications in `docs/`.

At the time this review was initiated:

- The active branch is `hj/develop`.
- `origin/main` is an older mainline baseline.
- The working tree contains uncommitted backend and frontend changes.
- The repository has no existing `CONTEXT.md` glossary or architecture-decision record layout.

## Decisions required before the detailed review

### 1. Review baseline

Which fixed point should the diff-based code review use?

**Selected:** `origin/main`, to review the current branch against the mainline baseline. Uncommitted changes were examined separately.

Alternatives:

- `HEAD` for working-tree-only review.
- A named commit or branch for a narrower comparison.

### 2. Launch target

Are we assessing readiness for a real production launch, a staging/demo launch, or an internal beta?

**Selected:** demo launch for perspective clients. The review prioritizes a safe, credible buyer/builder/reviewer demonstration. CORS, CQRS, and production-grade authentication are not launch requirements unless they block that demonstration.

### 3. Documentation output

Should the domain-modeling pass create a new `CONTEXT.md` glossary and ADRs for durable, agreed architectural decisions, or report findings only?

**Selected:** create the glossary and only genuinely durable ADRs.

## Review results

### Scope and evidence

- **Branch review:** `git diff origin/main...HEAD`, covering the current `hj/develop` branch.
- **Working-tree review:** the modified Django admin files were reviewed separately.
- **Product baseline:** `docs/01-product-requirements.md`, `docs/04-user-flows.md`, `docs/05-api-contracts.md`, `docs/07-security-privacy-threat-model.md`, and `docs/09-frontend-ux-specification.md`.
- **Implementation standards:** `MAINTAINERS.md`, `WORKFLOWS.md`, service READMEs, and the documented architecture invariants.

### Demo-launch decision

**Do not demo the reviewer/admin workflow until the two blockers below are fixed and their focused backend tests pass.** The buyer frontend builds and its unit tests pass, but the reviewer/admin surface currently fails on ordinary project actions.

## Standards and implementation review

### Blocker — the project admin change page returns HTTP 500

`ProjectAdmin.fieldsets` includes `manual_amenities`, but `ProjectAdmin.form` is the generic `forms.ModelForm` rather than `ProjectAdminForm`, which declares that custom field. Django consequently rejects the field set before rendering any Project change page.

- Evidence: `reailEstate/reailEstate_common/backend/platform_api/admin.py:700`, `:778`, and `:787`.
- Reproduction: isolated SQLite test run failed at `platform_api/tests/test_admin_navigation.py::test_sidebar_quick_queue_link` with `FieldError: Unknown field(s) (manual_amenities) specified for Project`.
- Demo impact: an administrator or reviewer cannot open a Project to inspect, correct, or prepare it for the client demonstration.

### Blocker — project retirement and manual-amenity save paths are mis-scoped

`delete_view` stops after computing `cancel_url`, so it returns no response. Its POST confirmation and retirement code is instead nested inside `save_model`; that code refers to `project` and `cancel_url`, neither of which exists in that method. After the form issue is corrected, a manual amenity save can therefore persist data and then raise an exception; deletion/retirement cannot render its confirmation flow.

- Evidence: `reailEstate/reailEstate_common/backend/platform_api/admin.py:864` through `:927`.
- Demo impact: the documented safe-retirement flow is unavailable, and the admin can leave users unsure whether an amenity edit succeeded.
- Architecture note: this is a shallow, overloaded admin module failure. HTTP/admin adaptation and retirement use-case orchestration need distinct, testable methods; they should not share an indentation-sensitive control flow.

### High — uncommitted changes are not a releasable unit

The working tree includes modifications to the two affected admin modules plus an untracked alternate frontend tree. These are not part of `origin/main...HEAD` and will not be represented by the branch diff, commits, or a normal deployment built from HEAD.

- Evidence: `git status --short` during this review.
- Demo impact: commit or otherwise deliberately stage the selected demo state after the blockers are fixed. Do not treat a local working tree as the reviewed artifact.

### Medium — frontend maintainability warnings

`npm run lint` succeeds but reports 15 warnings, concentrated in `BuilderPortal.tsx`, `Browse.tsx`, `App.tsx`, `ProjectMap.tsx`, and `ProjectGallery.tsx`. They include state updates inside effects, mutable state, and ref reads during render. They are judgement-call maintainability findings rather than demo blockers; address them before broad frontend restructuring.

### Medium — buyer bundle size

The buyer build produces a 595 kB minified JavaScript chunk (179 kB gzip), exceeding Vite's 500 kB warning threshold. It is acceptable for a controlled demo but should be split before a performance-sensitive public release.

## Product/spec review

### High — precise buyer coordinates are stored in the profile payload

The requirement profile accepts exact coordinates and stores them in its JSON payload. The separate `FrequentPlace.encrypted_coordinates` field is not populated, while matching reads the coordinates directly from the payload.

- Evidence: `platform_api/services.py:84-98`, `:149-162`; `platform_api/models.py:83-106`; `platform_api/travel.py:130-140` and `:175-185`.
- Requirement conflict: FR-03 and the privacy model classify precise locations as sensitive and require server-side protection.
- Demo action: use locality-only or synthetic destinations until exact-coordinate storage is protected; never enter a prospect's real home or work coordinates.

### High — organization verification is not enforced before developer submission

The documented developer flow requires verified organization access, but project creation and submission only check active organization/membership and edit permission. A pending organization can reach human publication review.

- Evidence: `platform_api/models.py:24-40`, `platform_api/views.py:644-648`, and `:982-997`.
- Requirement conflict: `docs/04-user-flows.md:77-80` and `docs/05-api-contracts.md:280-282`.
- Demo action: use only deliberately seeded, verified organizations and avoid claiming that self-service verification is enforced. Fix the gate before any open developer onboarding.

### Medium — buyer requirements are narrower than the product narrative

The buyer form and canonical draft capture configuration, carpet area, and bathrooms, but omit balconies, parking, accessibility, orientation/floor preferences, and room minima. Ranking therefore cannot reflect those criteria.

- Evidence: `frontend/src/buyer.ts:30-42`, `frontend/src/Requirements.tsx:36-40`, and `platform_api/services.py:100-132`.
- Requirement conflict: FR-04 in `docs/01-product-requirements.md`.
- Demo action: position matching as a configuration, size, bathroom, budget, and currently available amenity prototype—not a complete home-preference engine.

### Medium — amenity substitution has no bound

The form records whether a nearby equivalent is acceptable, but does not record the maximum distance or time for that substitute. A distance setting exists only for a separately preferred nearby amenity.

- Evidence: `frontend/src/buyer.ts:37` and `frontend/src/Requirements.tsx:52-61`.
- Requirement conflict: FR-05 requires an explicit maximum travel distance/time for substitutions.

### Medium — buyer-facing privacy/share controls are absent

The API supports profile sharing, export, deletion, and comparison sharing, but the buyer interface does not expose those operations; its privacy page directs users to an administrator.

- Evidence: `platform_api/views.py:365-413`, `:1188-1217`, `:1285-1321`; `frontend/src/App.tsx:222`.
- Requirement conflict: FR-11 and the account privacy/deletion flow in `docs/04-user-flows.md`.
- Demo action: do not promise self-service export, deletion, or revocable sharing until the UI exposes them.

## Validation performed

| Check | Result |
| --- | --- |
| Frontend lint | Passed with 15 warnings |
| Frontend type-check and buyer build | Passed |
| Frontend buyer unit tests | Passed: 18 tests |
| Backend suite with local PostgreSQL configuration | Could not start: local PostgreSQL connection timed out |
| Backend suite with isolated SQLite configuration | Reproduced the admin Project-page blocker after 5 passing tests |
| `git diff --check` | No whitespace errors reported |

The initial frontend build failure was caused by sandbox-denied writes to transient Vite/TypeScript cache files. The same build passed when those normal cache writes were permitted, so it is not a source-level finding.

## Architecture and domain conclusions

The existing perforated-monolith shape is coherent for this product: independently runnable admin, builder, and buyer entrypoints share one Django domain, PostgreSQL schema, media store, and durable-work model. It provides useful demo-launch leverage without prematurely splitting the domain into remote systems.

The highest structural risk is the large Django admin module, which combines unrelated admin adaptation, media work, location review, retirement, and manual editing paths. The two confirmed blockers demonstrate weak locality there. After the demo gate is cleared, extract each existing use case behind its current interface and add narrow regression tests; do not change URLs, authorization, audit, or concurrency contracts during that extraction.

No ADR was created in this pass. The current architecture is already documented, but the reviewed material does not record the original alternatives and trade-off decision with enough certainty to create a faithful ADR rather than reconstructing history.
