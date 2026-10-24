# Mumbai New-Construction Discovery Platform

This platform helps Mumbai Metropolitan Region home buyers discover and compare new or under-construction residential projects. It turns buyer intent and heterogeneous project evidence into transparent, evidence-backed recommendations.

## Agent handoff (updated 2026-10-07)

This is a working product for discovering and comparing new or under-construction homes in the Mumbai Metropolitan Region. The glossary below defines product language. For exact runtime commands and service workflows, use [WORKFLOWS.md](WORKFLOWS.md); for deeper architecture and the dated regression baseline, use [reailEstate/MAINTAINERS.md](reailEstate/MAINTAINERS.md). Check the code and current test results before treating a dated status here as current.

### Architecture and ownership

- Three Django entrypoints share `platform_api`, PostgreSQL schema, migrations, and media: admin (port 8002) owns review, publication, operations, and migrations; builder (8003) owns organization project ingestion; buyer (8004) owns public catalogue, accounts, requirements, and matching. The persistent shared worker handles document, media, and signed-in recommendation events.
- Buyer and builder bundles come from one React/Vite source tree under `reailEstate/reailEstate_common/frontend`. Admin is Django's interface. Keep browser API requests on the same service origin; Vite development proxies buyer and builder requests separately.
- Published catalogue facts are the buyer boundary. Builder uploads and generated facts are proposals until independent review. Original evidence remains immutable; corrections create derived or versioned records. A successful ingestion job never publishes a project.
- Buyer profiles, consent, exact destinations, snapshots, and shortlists are account-scoped. Signed-in matching stores a versioned requirement and queues an immutable recommendation snapshot. Shared views redact exact location data.

### Current user flows

- **Guest buyer:** Browse published projects; fill requirements, including optional exact destinations; consent to send them for matching and keep them in browser cookies; call `POST /api/v1/guest/recommendations`; view the top-ten report and save shortlist IDs in cookies. A successful cookie save marks this browser's free run used. Report cookies last 90 days; the usage marker is renewed on visits. Sign in for another run.
- **Signed-in buyer:** Save a consent-backed profile, submit its version, poll the operation, and inspect the hosted snapshot. Shortlist, comparison, sharing, export, and deletion use owner-scoped APIs. Guest cookies do not become a hosted profile automatically.
- **Builder:** Sign in with an active organization membership, select an organization and project, edit/version project data, upload evidence, monitor worker jobs, and submit for independent review. An owner can create and assign owner, admin, editor, or viewer role packages. An organization admin can manage non-owner packages and members. Existing active usernames are required; an owner must remain.
- **Platform reviewer/admin:** Verify evidence and locations, resolve conflicts, approve layouts and facts, publish projects, and inspect/replay operations using explicit permissions and audit trails. Authorized platform administrators may manage organization roles through Django admin without being organization members.
- **Theme:** Buyer and builder have System, Light, and Dark controls; Django admin has the same choices. They share a browser preference. System follows the browser-reported scheme; dark is used only if neither light nor dark is reported.

### Error and recovery flows

- A guest request with invalid sections or missing consent returns validation errors. Routing unavailability leaves commute unavailable in the report. Guest scoring creates no buyer profile, snapshot, operation, or route-cache write.
- If a guest report cannot fit into bounded cookies or cookies are blocked, show an error and do not mark the run used. Damaged or incomplete cookies show a recovery message and can be deleted. Clearing cookies can reset the browser-only usage limit; it cannot enforce one report per person.
- Signed-in submission may fail validation, consent, version checks, or worker processing. Keep the saved draft/version for correction; retry through the operation flow, never turn a failed operation into a ready snapshot.
- Builder role changes are checked on every organization-scoped API call. Admins cannot grant or change owner access; editors, viewers, nonmembers, and staff flags alone cannot manage packages. Bulk assignment fails atomically for unknown or inactive accounts and cannot remove the last active owner.
- Uploads, extraction, provider lookups, and reviews can fail independently. Inspect the operation and queue status, retry only eligible work, and never treat unreviewed provider output as a published fact.

### Local operation, status, and workarounds

- From the repo root run `./reailEstate/manage_services.ps1 -Action start|status|stop`. Start applies migrations and builds both React bundles. Use `npm --prefix ./reailEstate/reailEstate_common/frontend run dev:buyer` or `dev:builder` for hot reload after starting the matching Django service. Use the health URLs and worker heartbeat in WORKFLOWS.md to confirm readiness.
- Run focused frontend tests with `npm --prefix ./reailEstate/reailEstate_common/frontend test` and `build:services`. Backend tests use the existing virtual environment and an isolated PostgreSQL test database. Start PostgreSQL first and use an account with CREATEDB permission; use a workspace-local pytest `--basetemp` if the host temp directory is inaccessible.
- The full backend test baseline was already red before this work. MAINTAINERS.md records the dated SQLite and PostgreSQL counts and known areas to triage. Do not claim the full suite is a release gate until those failures are resolved.
- Exact commute scoring depends on the routing provider. Local approximate routing is development-only; an unavailable provider produces an explicit unavailable component. Guest requests and reports stay in cookies, so browser cookie limits can reject long notes or destination-heavy reports.
- Public processing previews and demo pins are controlled by explicit settings or feature flags and are temporary review aids. They do not make draft facts publishable. Migration 0020 introduced reusable roles; migration 0021 adds the organization admin role.
- Azure preparation has a validated plan but no authorized apply. AWS is offline-validated without an approved account deployment. Do not infer a live deployment from these artifacts.

## Participants

**Buyer**:
A person seeking a home who defines requirements, receives recommendations, compares projects, and maintains a shortlist.
_Avoid_: Customer, client, user (when the buyer role is meant)

**Co-buyer**:
A person granted permission to review or edit a Buyer's shared requirement profile or shortlist.
_Avoid_: Collaborator, secondary buyer

**Developer representative**:
An authorized person who submits project information and evidence for an Organization.
_Avoid_: Seller, agent

**Organization**:
The developer entity responsible for one or more Projects and their submitted information.
_Avoid_: Builder (except when referring to the market-facing persona)

**Organization administrator**:
An active Organization member who can manage non-owner role packages and memberships within that Organization, without granting or removing owner access.
_Avoid_: Platform administrator

**Role package**:
A reusable Organization-scoped access role assigned to existing active platform accounts; owner, admin, editor, and viewer are its access levels.
_Avoid_: Platform permission group

**Data reviewer**:
A platform role that resolves evidence conflicts and approves project facts for publication.
_Avoid_: Moderator, administrator

## Buyer intent and recommendations

**Requirement profile**:
A Buyer's versioned statement of housing needs, constraints, preferences, budget, and consent-backed personal data.
_Avoid_: Search, query, questionnaire

**Requirement version**:
The immutable submitted version of a Requirement profile used to generate a Recommendation snapshot.
_Avoid_: Draft (unless it has not been submitted)

**Frequent place**:
A destination relevant to a Buyer's routine, together with travel preferences and tolerance.
_Avoid_: Address, location (when the destination concept is intended)

**Hard constraint**:
A requirement that eliminates a Project before ranking.
_Avoid_: Preference, priority

**Preference**:
A desirable characteristic that influences ranking without disqualifying an otherwise eligible Project.
_Avoid_: Hard constraint

**Recommendation snapshot**:
A time-bound, reproducible ranked result for one Requirement version, including the evidence and method version used to produce it.
_Avoid_: Search result, live score

**Recommendation item**:
One Project's rank, score, confidence, and explanation within a Recommendation snapshot.
_Avoid_: Listing (unless referring to its presentation)

**Shortlist**:
A Buyer's saved set of Projects for later comparison or sharing.
_Avoid_: Recommendation list

## Project catalogue and evidence

**Project**:
A new or under-construction residential development represented by normalized, publishable facts and associated evidence.
_Avoid_: Property, listing

**Home configuration**:
A Project's offered residential unit type and its comparable characteristics, including carpet area.
_Avoid_: Flat type, inventory

**Carpet area**:
The canonical area measure used to compare Home configurations; other area definitions remain explicitly labelled.
_Avoid_: Built-up area, super-built-up area

**Evidence document**:
An original submitted or authoritative document that supports one or more candidate facts about a Project.
_Avoid_: Brochure (unless describing that specific document type)

**Fact candidate**:
A proposed Project value derived from Evidence or an authoritative source, retaining provenance, confidence, and review state.
_Avoid_: Project fact, verified fact

**Project fact**:
A reviewed, selected, versioned value that represents a Project for publication or decision support.
_Avoid_: Claim, candidate

**Fact conflict**:
A disagreement between Fact candidates for the same Project attribute that requires a resolution.
_Avoid_: Error

**Review task**:
Work assigned to a Data reviewer to resolve evidence, verification, or publication uncertainty.
_Avoid_: Ticket

## Verification, metrics, and trust

**MahaRERA verification**:
The comparison of a Project against an authoritative MahaRERA record, with a result and timestamp; it is not legal certification.
_Avoid_: Legal approval, guaranteed verification

**Verification snapshot**:
A time-stamped record of authoritative-source data used to assess a Project's MahaRERA verification.
_Avoid_: Project fact

**Project metric**:
A versioned, time-bound calculation about a Project, retaining its definition, inputs, confidence, and freshness.
_Avoid_: Fact, guarantee

**Evidence confidence**:
The stated reliability of a fact or calculation based on its provenance, review status, and freshness.
_Avoid_: Accuracy, certainty

**Nearby amenity**:
An amenity outside a Project that may contribute to a preference only when it is within 5 km; it is never equivalent to an in-project amenity.
_Avoid_: In-society amenity

**Amenity substitution**:
The explicit comparison of an in-project amenity with an acceptable nearby alternative; the alternative is never treated as identical.
_Avoid_: Amenity match

**Local transaction evidence**:
Licensed, normalized records of completed transactions used to provide market context, distinctly from asking prices.
_Avoid_: Sale price, market value
