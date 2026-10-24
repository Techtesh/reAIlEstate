# Admin UX migration plan

Date: 2026-10-24. Status: the seven follow-up UX changes below are implemented locally in source. Frontend builds and Django startup checks pass; automated tests remain deferred at the user's request, and browser acceptance is pending.

## Follow-up UX changes — implemented in source

The shared media workspace now has per-entry activation, separate floor/layout review forms, a dedicated crop-editing state and 5-degree rotation buttons. The processing queue has Running, Dead letter and Completed tabs. The requirements below remain the acceptance checklist; source implementation does not establish runtime acceptance.

### Review action clarification

- Editing an extracted value makes **Save for review** the primary action. Approval/rejection actions return after a successful save, subject to independent-review permissions.
- Approval is labelled **Approve extraction** or **Approve floor evaluation**. Saving corrections does not approve or publish them.
- Room measurement validation accepts saved floating-point precision from feet/inches conversion. Filling one missing dimension must not require manually rounding other rooms first.
- Validation responses identify the room and field involved and preserve the user's local entries on failure.

### 1. Activation controls on each plan

- Remove the separate Plan management list from the project workspace.
- Place an accessible Active checkbox beside each floor-plan/layout entry in its existing directory. Keep selection and activation as separate controls so toggling activity does not accidentally select another plan.
- Preserve active-first ordering. Keep the selected plan visible after it moves into the inactive group; do not silently switch selection.
- Reuse the current confirmed, version-checked activation action. Explain that disabling a floor plan also disables its derived layouts, while enabling it does not automatically enable them.
- Retain remove/restore controls on the selected plan and retain draft-replacement controls beside crop drafts. Removing the duplicate list must not remove those operations.

### 2. Floor-specific evaluation correction

- Floor plans get a dedicated Review and correct floor evaluation form: flats on this floor, lifts, staircases, refuge presence/area/unit, and other common-floor features with supporting evidence.
- Keep bedrooms, bathrooms, balconies, carpet area and room-dimension editing in the layout form. Choose the form using the actual plan scope; the server must also reject a payload for the wrong scope.
- Keep unknown values distinct from confirmed zero. Derive flats per lift from the corrected counts; do not make it a second independently editable value. Flat count describes homes visible on the floor, not the number of generated crops.
- Save floor corrections into the floor-evaluation data while preserving source checksums, original extraction provenance and before/after history. Record actor, reason and correction time; return the record to review.
- Preserve permission, stale-version, source-integrity, pending/running-work and independent-review safeguards. Saving a correction and approving it remain separate actions; corrections must not silently alter individual layouts or published facts.
- Approve/reject must target the floor evaluation and its review task. Verify the existing generic resolution path before reusing it so layout-specific fact generation is not triggered accidentally.

### 3. Remove redundant plan image navigation

- Remove Previous image and Next image under Floor plans and Layouts; use their existing searchable directories for selection.
- Keep image navigation for Project images and Amenities, where thumbnail browsing still applies.

### 4. Floor-plan crop editor placement

- Opening Update crop enters a dedicated crop-editing state in the selected plan's detail area. Temporarily replace the usual preview/review content instead of placing a new ribbon beneath sticky content.
- Keep the plan directory and selected plan context visible. Give the editor an explicit title and Save draft / Cancel controls; move focus to it on entry and return focus on exit.
- Distinguish editing/resetting a crop draft from Restore original crop, which changes saved media and can queue evaluation. Retain the appropriate confirmation for each operation.

### 5. Layout crop overlap

- Apply the same crop-editing state to layouts. Suspend the normal sticky preview and multi-row grid placement while cropping so headings, actions and fields cannot render over one another.
- Cancel restores the normal image/review view without losing saved data. Preserve warnings for unsaved crop selections and review edits.
- Check both floor plans and layouts at desktop and narrow widths, with long titles and browser zoom. A higher z-index alone does not satisfy this fix.

### 6. Rotation in 5-degree steps

- Replace the source-rotation dropdown with two buttons, −5° and +5°, plus a visible current-angle readout. Label clockwise/counterclockwise direction accessibly and consistently with the preview.
- Normalize angles through a full turn. Each click updates only the local preview; it does not save media, start evaluation or incur model costs.
- Use the same increments for new crops and updates to existing crops. Align server validation and saved crop geometry with the rendered rotation.
- A rotation changes the crop coordinate system: reset the current unsaved rectangle with a clear explanation, and prevent rotation while retained multi-layout rectangles exist until they are explicitly cleared.

### 7. Processing queue tabs

- Reuse the project-workspace tab style for Running, Dead letter and Completed, including keyboard navigation, selected state and counts.
- Running includes queued, currently running and retrying jobs, with their actual states clearly labelled and optional filtering within the tab. Pending work must not disappear merely because it has not started.
- Keep search and refresh available across tabs. Show live worker/capacity information with Running; keep Inspect and permitted requeue/delete actions with the relevant job details.
- Preserve the selected tab in the URL. Clear an inspected job when leaving its tab or when refreshed data moves it elsewhere; show useful loading, empty, denied and error states.
- Distinguish total queue counts from the number of loaded rows in the existing bounded snapshot, especially for completed/dead-letter history. Do not imply that the latest rows are the entire history.
- Keep current permissions, confirmations and version checks. Changing tabs must never start workers, retry jobs or change queue state.

### Implementation order and acceptance

Implementation covers the shared crop layout, rotation controls and backend parity, activation controls and navigation, the floor-specific correction API/form, and queue tabs.

Continuation fixes preserve the selected plan during activation and defer discarding local edits until all navigation confirmations pass. Review forms use the API's actual plan scope. Angled previews use the saved renderer's expanded canvas dimensions; source-page drafts retain an inverse-rotated polygon and clipped original-page bounds. Floor review verifies original and asset bytes outside its database transaction, rechecks versions under row locks, preserves correction history, and clears old approval metadata after a correction. Queue tabs/search use router query parameters and keep loaded-row counts separate from totals.

Acceptance requires every plan to retain its activation/removal/restoration operations, floor correction to show only floor-level fields, no Previous/Next controls in plan tabs, no overlapping crop content, consistent 5-degree preview/save behavior, and all queue states reachable through the three tabs. Check light/dark styling, keyboard use, selected-item retention and unsaved changes across these journeys.

Implementation continued on the user's request to resume. Builds and test execution stay deferred; use the saved commands below when requested, then inspect the resulting failures. Focused regression cases have been added for rotation geometry, floor correction integrity/version checks, correction history and wrong-scope payloads; they have not been run.

Static checks: the changed frontend files parse without TypeScript syntax diagnostics, and `git diff --check` reports no whitespace errors. These checks do not establish type correctness or runtime behavior. The AST-only graph refresh completed; optional Terraform/HCL and SQL parsers remain unavailable, so those unrelated sources are not represented by this refresh.

## Current UX implementation

- The custom administration shell replaces production Django admin panels and exposes project, review, access, reference-data and processing destinations.
- Project details, sources/review, documents and images/plans have separate sections. Media deep links open the requested section directly; switching sections keeps local entries mounted.
- Project selection is retained in the URL. Name/locality filters stay above the scrolling project list; active projects and plans sort before inactive records.
- Admin amenity/source review appears before collapsed submission forms. Layout images and compact extraction-correction forms share a row when space permits.
- Documents use a compact list/preview layout with original-file access in a new tab. Protected page previews include the source checksum; image preview was confirmed locally. Office formats without a page renderer offer the original download.
- Activation controls sit beside plan directory entries; removal/restoration stays with the selected plan, and replacement stays beside crop drafts. Processing controls remain in expandable sections. Extraction errors and measurement warnings remain visible beside the review form.
- Earlier frontend production builds passed before these follow-up changes; the current source has not been rebuilt. The backend suite has not completed: the long run encountered a Windows Python access violation, and subsequent fixes have not had a final regression run. Do not treat this as release approval.

### Deferred test commands

From the repository root, run backend modules in separate Python processes to avoid the crash seen during the long combined run. The command keeps the complete output in the temporary directory for failure review.

```powershell
Push-Location reailEstate/reailEstate_common/backend
Get-ChildItem platform_api/tests/test_*.py | Sort-Object Name | ForEach-Object {
    & ./.venv/Scripts/python.exe -m pytest $_.FullName --tb=short --show-capture=no -p no:cacheprovider 2>&1
} | Tee-Object -FilePath (Join-Path $env:TEMP 'admin-backend-tests.log')
Pop-Location
Push-Location reailEstate/reailEstate_common/frontend
npm test
Pop-Location
```

## Goal and scope

Replace every user-facing Django default admin panel with the application's Builder portal layouts and components. Administrators must be able to complete their work without entering a Django-rendered admin page or opening an admin tool in another tab.

Reuse the existing project editor, document upload flow, media workspace, crop tools, theme, panels, forms, notices and tables. Keep Django's authentication, permissions, ORM and domain services behind the custom interface. Do not rebuild already supported project/media operations.

This includes admin login/logout, password change, navigation, list/detail/edit screens, confirmation flows, history, errors, expired sessions, operational tools and bookmarked legacy URLs. There is no permanent Django admin “advanced tools” exception in the target state.

This plan is based on the current working tree, including existing uncommitted changes. Capability findings are from static source inspection, not a live environment or runtime acceptance test.

## What already works in the custom admin workspace

The admin workspace reuses Builder components through a separate admin API prefix. It already supports:

- Listing active organizations and selecting their projects without requiring Builder membership.
- Creating projects, reading and editing project details, location entry and processing attestation; archiving through the existing workflow.
- Document upload intents, byte upload and completion; document status, extraction runs, queue information and output downloads.
- Source page selection/previews and crop drafts; attaching/discarding drafts and replacing selected layouts.
- Viewing floor plans, layouts, amenity images and project images.
- Renaming plans, changing layout price, activating/deactivating plans, reversible removal/restoration and crop updates/reset.
- Segmentation, evaluation, rotation, project picture selection and document reprocessing controls.
- Submitting source references, registry evidence and manual amenity claims for independent review.
- A personal crop cart and project-level processing feedback.

These are substantial reusable workflows, but not complete admin parity. For example, layout name/price editing does not include manual correction of extracted room dimensions; submitting an amenity claim does not include approving it.

Sources: [BuilderPortal.tsx](../reailEstate/reailEstate_common/frontend/src/BuilderPortal.tsx), [BuilderMediaWorkspace.tsx](../reailEstate/reailEstate_common/frontend/src/BuilderMediaWorkspace.tsx), [ProjectContent.tsx](../reailEstate/reailEstate_common/frontend/src/ProjectContent.tsx), [workspace API routes](../reailEstate/reailEstate_common/backend/platform_api/urls_project_workspace.py).

## Target navigation and layouts

Use one admin entry point under `/admin/`, with the existing Builder visual language and shared controls. Organize work into:

| Navigation | Pages |
| --- | --- |
| Overview | Review backlog, processing failures and links to work requiring attention |
| Projects | Global searchable/filterable project list; project workspace; documents; layouts/media; sources; amenities; facts; merge/history |
| Review | Task queue; evidence/candidate comparison; room correction; source and amenity review; publication |
| Processing | Cross-project jobs, workers, failures/dead letters, retries, replay and extraction runs/outputs |
| Organizations & access | Organizations, accounts, platform roles, organization roles and memberships |
| Reference data | Amenity taxonomy and station candidates/corrections |
| Settings & operations | Feature flags, integration status, metrics, audit history and restricted retained records |

Navigation and actions follow server-provided capabilities. A reviewer, read-only operator or account administrator must not need project-edit permission merely to enter the shell. Keep platform administration separate from organization membership.

All pages use the same header, account/theme controls, page headings, breadcrumbs, panel spacing, table/form controls, loading/empty/error states, confirmations and responsive behavior. Reuse existing Builder layout patterns; add navigation for admin tasks without redesigning the Builder workflow.

Use stable URLs for project, organization, section, selection, filters, search, sort and page. Preserve previous/next navigation, return-to-list context and dirty-form warnings across transitions. Admin tool links must navigate within this shell.

## Feature and CRUD gap inventory

Legend: C = create, R = list/detail/read, U = update, D = deletion. “Missing” means missing from the custom **admin** experience; backend support is listed separately. An API existing does not imply the screen exists or all legacy fields are writable.

The main registry plus role registrations expose 23 model surfaces. The following table accounts for all of them, including generic model registrations.

| Existing admin surface | Custom admin support today | API/domain support and remaining work |
| --- | --- | --- |
| Project | C/R/U and archive supported within the workspace | Add global list/filter/pagination parity, inactive-organization scope, derived boundary/Maps/registry indicators, bulk retirement/reanalysis, merge UI and merge history. Merge API already exists. Keep retirement semantics instead of hard D. |
| FloorPlan | R, name/price U, crop/evaluate/segment, active state, remove/restore supported | Manual extraction/room correction remains a Django form. Expose its existing validation/save service through an admin API and custom form. Add admin-wide search/filter and explicit review controls. No arbitrary record creation or hard D. |
| FloorPlanAsset | Project-scoped media R and previews supported | Add global metadata/category/status inspection equivalent to the record view and filters. Creation remains upload/processing/crop driven; default admin already forbids C/D. |
| EvidenceDocument | Upload C, project-scoped R, status and reprocessing supported | Add global search/filter/detail and bulk retry of failed extractions. Map mutable metadata to explicit validated operations; arbitrary scan/status edits and hard D are not covered by the workspace. |
| IngestionRun | Project-scoped run/output R supported | Add global run list/detail, error/agent-workflow inspection and links to jobs/outputs. Current generic admin permits model edits/deletion; proposed replacement treats processing history as read-only and retries via actions. |
| ProjectMerge | Merge history screen missing | Merge action API exists, but an admin history/list/detail API and screen are missing. C occurs through merge, history is R; no arbitrary U/D. |
| Organization | Active-organization picker only | Existing organization ModelViewSet supports C/R/U/D, but there is no custom management screen. Serializer makes verification read-only and omits `confirm_name_changes`, both exposed by the Django form. Add deliberate field/workflow coverage, status management and safe deletion/deactivation behavior. |
| ProjectAmenity | Claim submission and summary R supported | Acceptance/rejection, reason, availability/distance/travel edits remain in a Django form. Add custom review API/UI reusing optimistic concurrency, audit and independent-review rules. No arbitrary C/D. |
| StationCandidate | No admin correction/CRUD screen | Public map reading exists, not admin C/U/D. Add permission-scoped station list/detail/create/edit, coordinate alternatives, line/kind/sequence, review flag, issue and active state. Prefer deactivate; make any permanent D policy explicit. |
| User | No custom account administration | Add admin list/detail/create/update, activation/deactivation, staff access, group/direct-permission assignment, password setting/reset and membership management APIs/UI. Separate administrator password reset from a user's own password change. Do not expose hashes. |
| Group / platform role | Missing | Django form manages role name, permissions, multiple members and optional staff-login grant. Add equivalent admin APIs/UI with permission-escalation checks and role/membership removal controls. |
| OrganizationRole | Missing in admin; partial Builder component exists | Builder has R/C and bulk assignment to existing active accounts. Admin hides that component and its workspace routes do not mount the role endpoints. Add admin-authorized routes/UI, role U and safe D; preserve assigned-role restrictions and last-owner protection. Builder form also omits the `admin` base-role option accepted by its API. |
| OrganizationMembership | Missing in custom admin | Existing organization response reads memberships; Builder role assignment can create/update assignments. Add admin membership R/C/U, suspension/revocation/removal and versioned changes. The Django user inline must be replaced too. |
| Amenity | No taxonomy management UI | Admin ModelViewSet already supports C/R/U/D. Add custom taxonomy screens; check relationships before deletion and support useful validation/error messages. |
| FeatureFlag | Missing | Dedicated API supports C/R/U with version checks and no DELETE. Add custom management UI, environment/registry constraints and conflict handling. The main admin registry currently registers this generically; the richer `FeatureFlagAdmin` class is opt-in, not the live registration. Use the dedicated API semantics rather than reproducing generic model edits. |
| FactCandidate | No dedicated list/detail/editor | Candidates can be included in review-task responses. Add global/project evidence browsing and review-driven correction/selection. Generic raw C/U/D is not a complete reviewed workflow and should not be copied silently. |
| ProjectFact | No admin fact-set/history UI | Project detail/output paths and review/publication services cover parts of R and controlled mutation. Add admin access to unpublished facts, version/history and provenance; make corrections through review. No raw published-history editing/deletion. |
| ReviewTask | No custom queue/resolution UI | Read-only task list/detail and resolve APIs exist. Add evidence comparison/resolution and status/type filters. Assignment, priority changes, explicit reject/reopen and other task-type-specific actions need defined APIs; existing resolve is not a general task CRUD API. |
| DocumentLayoutPage | Source-page tools cover part of R and workflow creation/update | Add a custom page-metadata inspector and account for fields presently exposed through generic admin. Keep page marking/replacement in existing workflows; arbitrary row C/U/D is not currently covered. |
| AuditEvent | Missing admin audit UI | Read API returns the latest 200 events; add filtering/pagination/detail as needed for the replacement. Proposed UI is read-only, although generic Django registration currently exposes model CRUD. |
| Consent | No admin-wide UI | Existing user-scoped consent API is not an administrator-wide management API. Add restricted admin inspection and explicit privacy/withdrawal workflows where required; do not recreate arbitrary consent fabrication/edit/delete. |
| RequirementProfile | No admin-wide UI | Buyer APIs are user-scoped. Add restricted support inspection through admin-specific authorization; preserve owner data boundaries. Do not silently repurpose buyer endpoints for staff-wide CRUD. |
| RecommendationSnapshot | No admin-wide UI | Buyer recommendation reads exist; add restricted support/history inspection. Snapshots should be R with regeneration through the recommendation workflow, not raw C/U/D. |

Registry sources: [admin.py](../reailEstate/reailEstate_common/backend/platform_api/admin.py), [roles_admin.py](../reailEstate/reailEstate_common/backend/platform_api/roles_admin.py). API sources: [admin routes](../reailEstate/adminService/admin_service/api_urls.py), [views.py](../reailEstate/reailEstate_common/backend/platform_api/views.py), [serializers.py](../reailEstate/reailEstate_common/backend/platform_api/serializers.py), [organization role views](../reailEstate/reailEstate_common/backend/platform_api/organization_roles_views.py), [feature flag API](../reailEstate/reailEstate_common/backend/platform_api/feature_flags/api.py).

### Other missing workflows and admin dependencies

| Area | Current state | Replacement work |
| --- | --- | --- |
| Login, logout, password change and expired sessions | Entry is wrapped in `admin.site.admin_view`; Django supplies login and account pages | Custom admin login/session endpoints and Builder-style login/account views, staff/capability checks, CSRF, safe return URLs and session-expiry recovery. Maintain correct 401/403 responses for APIs/media. |
| Full admin routing | Only `/admin/project-workspace/` selects the React admin view | Add full nested admin routing, direct-link serving and custom 403/404/error views in both service and common URL configurations. |
| Extracted room correction | `layout_extraction_admin.py` renders Django forms/formsets | Move reusable correction logic behind an API; custom fields/room editor with source preview and version-conflict handling. Preserve original evidence and review/audit creation. |
| Source review and transit directions | Claim submission is custom; review actions and station selections are Django-backed | Port manual/saved reference review, station selection and route retry; show attribution, dated evidence and project/transit maps in the custom shell. A generic fact-resolution endpoint does not replace these specialized actions. |
| Project amenity review | Custom workspace submits claims; Django form approves/rejects | Custom evidence/reason editor and review API, preserving independent reviewer and stale-write checks. |
| Global evaluation queue | Project-level status exists; global monitor and controls remain Django-backed | Add snapshot/list APIs and custom jobs/worker/dead-letter screens; run queued/fresh project evaluations, start own queue, requeue/delete eligible dead letters, confirmation and stale-version handling. Preserve current user/job scope; define broader operator access explicitly. |
| Bulk administration | Some media bulk workflows exist; cross-project actions and separate bulk-removal confirmation remain Django-backed | Port cross-project retirement/reanalysis and required bulk layout-removal selection/confirmation parity; reuse existing multi-target backend operations where behavior matches. |
| Outbox replay | API exists, no custom operator UI | Add authorized message lookup/list/detail and reasoned replay action. Replay is distinct from requeueing an existing dead-letter job. |
| Integration status and operational metrics | Read APIs exist, no custom screens | Add status and metrics views. Current integration API reports configuration availability; it is not a full active provider diagnostic or credential/settings editor. |
| Admin history | Django record history and domain/audit records use different surfaces | Define a consistent custom history view using domain audit records; preserve access to any retained Django admin log history needed for parity. Do not delete history during panel removal. |
| Legacy tool URLs | Frontend buttons and API `adminTools` return Django paths | Replace generated links as well as visible frontend links. Old GET bookmarks redirect to equivalent custom routes; legacy mutation URLs must not silently redirect/replay POSTs. |

Additional unsupported feature already in the backlog: transferring a project between organizations/handing it to a Builder. This is not supplied by the present editor or same-organization merge action. Treat it as a separate feature with evidence/access migration rules; it is not a prerequisite for replacing an existing Django panel.

## Screenshot review requirements

The following requirements are part of this migration's acceptance scope, not optional polish. References include the supplied images in [review/](../review/). They show observed UI behavior; source findings below do not imply the screenshot environment runs the current working-tree assets.

### R1. Cover queue and other admin-only components

The Builder components are the baseline, but they cannot supply every administrative interaction. Add the following focused views/components where reuse is insufficient. These are responsibilities to implement, not a requirement to invent a generic component framework.

| Component responsibility | Required behavior |
| --- | --- |
| Queue overview and filter toolbar | Compact state counts; project/job-type/status filters; search; refresh; visible last update and stale/error state; stable URL context. |
| Jobs table and job detail pane | Dense rows for project, item, type, state, attempts, retry timing and progress where available; selected-row details with safe error, source and operation links. Do not fabricate percentage progress. |
| Worker status and queue controls | Current worker/heartbeat state, available/busy slots where available, run queued/fresh project evaluations and existing own-queue controls; confirmation and meaningful conflict responses. |
| Failure/dead-letter actions | Retry/requeue, eligible deletion and reasoned replay as separate operations; disabled/unavailable reasons, stale-version checks and audited confirmation. |
| Review queue and evidence pane | Prioritized tasks; selected evidence; review decisions, corrections, reasons, history and review conflicts. |
| Access-management views | Accounts, roles, permissions and memberships using compact tables, selection and focused editors. |
| Operations/reference views | Stations, taxonomy, flags, integration status, metrics and audit/history using the same tables, notices and detail patterns. |

No queue/admin-only destination is complete if it merely opens a Django page. Reuse the existing queue/domain services behind APIs; preserve existing user-scoped behavior unless broader operator scope is explicitly authorized. The global queue should use available space for list/detail work, rather than a stack of oversized tiles. Keep project-level job snippets compact and link them to the custom global monitor with project scope preserved.

### R2. Correct theme inconsistencies

Reference: [review1_buttons_in_light_mode.png](../review/review1_buttons_in_light_mode.png).

The screenshot shows a dark workspace containing bright inactive tab buttons and thumbnail tiles with barely readable text. Every tab, button, thumbnail/tile, table, editor, preview frame and admin-only component must follow the active application theme.

- Use the shared theme tokens for surfaces, text, borders, hover, focus, selected and disabled states. Thumbnail artwork itself retains its original colors.
- Check both light and dark themes, system preference, switching while the page is open and refresh persistence.
- Verify normal, selected, hovered, focused, disabled and error states; selected/focus indication must not depend only on color.
- Inspect computed styles and served asset versions before choosing a fix: the working-tree media stylesheet already uses theme variables, so the screenshot alone does not prove a particular CSS declaration is still wrong.
- Check tile text, filename truncation, disabled text and borders for readable contrast. Do not apply one-off background colors that fix one theme and break the other.

### R3. Add in-place admin review and validation

Reference: [review1_admin_portal.png](../review/review1_admin_portal.png).

An authorized administrator must be able to review amenities, floor plans, individual layouts, extracted facts and source/registry/location details directly in the custom project workspace. Showing `unreviewed` or linking to a separate admin page is insufficient.

| Item | Review controls and evidence |
| --- | --- |
| Project amenities | Compact rows with availability, evidence, state, Accept/Reject, correction fields and required decision reason; distance/travel inputs where relevant. |
| Floor plans | Original floor image, derived floor summary, extraction/source status, Approve/Reject and correction workflow. |
| Individual layouts | Image/crop beside compact editable details and room measurements; Approve/Reject, save corrections and clear review status. |
| Source references, registry and location | Evidence/date/attribution with accept/reject/correct controls and existing station/route workflows. |
| Extracted facts and project details | Evidence/candidates and reviewed corrections; publication remains an explicit project-level action after its prerequisites pass. |

- Return per-item permitted actions and denial reasons from the backend. Admin mode makes review tools available to appropriately authorized users; it must not turn every staff/project editor into a reviewer.
- Preserve independent-review rules, including existing superuser behavior. Explain self-review restrictions in the UI instead of silently hiding all controls.
- Distinguish saving a correction for review, approving a record and publishing the project. Do not label all three as a generic Save action.
- Refresh the saved record, version and relevant queue/status counts after a decision. Keep the selected item and list context; a conflict requires latest evidence before retry.
- Reuse the existing floor-plan resolve and fact/task APIs only where their contract matches. Add the missing specialized amenity/source review adapters and strengthen stale-write/typed-validation checks where existing endpoints do not supply them.
- Builder users continue to submit claims/corrections without gaining admin approval or publication controls.

### R4. Compact document browser with preview pane

Reference: [review1_admin_portal_project_documents.png](../review/review1_admin_portal_project_documents.png).

Replace the long sparse document list with a compact two-pane browser inspired by the Windows 11 file-list/preview arrangement, styled with this application's components.

- Keep upload/select/refresh controls in a compact toolbar, with format guidance available without dominating the document list.
- Show selectable document rows containing filename/type, size, scan/processing/review state and a concise issue indicator. Use human-readable sizes; reveal the full filename accessibly when truncated.
- Selecting a row updates the adjacent preview pane. Retain selection after refresh where the document still exists; keep row keyboard navigation and visible focus.
- Preview PDFs through protected rendered pages with page number/navigation and zoom/fit controls; preview images directly through authorized resources. Reuse the existing clean PDF/image source-page renderers.
- Provide a prominent **Open document in new tab** action in the preview header. It opens the selected authorized document, not the old Django editor. If the browser cannot display that format, make the download fallback clear.
- Office files do not currently share the PDF/image page renderer. Use a server-generated safe preview when available; otherwise show metadata and an explicit preview-unavailable explanation with the permitted open/download action. Do not imply native DOCX/Office preview already exists.
- Add custom original-document read/open endpoints where necessary: the current original-document reader is attached to a Django admin route. Respect project/evidence scope, clean-scan and integrity requirements; preserve private caching and avoid exposing storage keys/URLs.
- Do not display quarantined/unsafe content or allow uploaded HTML/scripts to execute. Use rendered images/text or a deliberately isolated viewer; the existing CSP and frame-denial policy mean an arbitrary iframe is not a drop-in PDF preview.
- At narrow widths, stack the list and selected preview or provide a focused preview view. At desktop widths, list and preview occupy the same row; avoid an empty full-width file area.

### R5. Restore the compact side-by-side review layout

Reference: [review1_floor_plan_side_view.png](../review/review1_floor_plan_side_view.png). Use this image as context for the reported density problem, not as proof that its current arrangement meets the agreed design.

- At usable desktop widths, keep the selected source image on one side and a compact review/editor panel on the other. Retain source visibility while editing and reviewing.
- Put related short fields on the same row when space allows: configuration/area, room/bathroom/balcony counts, orientation and review state. Do not render each short field as a full-width section.
- Use dense room rows with room name, length, width, computed area and row actions. Keep units explicit and validation readable; avoid oversized empty cells and repeated labels consuming most of the panel.
- Group floor-level statistics in a compact inline summary; keep a concise action/reason area beside the relevant record instead of repeated tool-link lists.
- Use available container width, not only viewport width, to choose columns. The project/sidebar navigation must not leave the actual image/editor cramped on a wide screen.
- Keep directory/list scroll separate where useful and preserve the selected image and unsaved edits. No page-wide horizontal scrolling; small screens stack panels without hiding fields or actions.
- Compact means less unused space, not illegibly small typography or inaccessible controls. Check keyboard focus, 200% zoom and responsive reflow.

### R6. Expose and complete floor-level derived data

Show a **Floor summary** for floor plans, separate from individual flat/layout details.

| Floor summary field | Current source coverage | Required presentation/work |
| --- | --- | --- |
| Flats on this floor | Pipeline stores `features.floorEvaluation.flatsPerFloor` for multi-unit floor plans after successful full-floor analysis | Display detected flats, with linked derived layouts. Distinguish source-detected flats from successfully extracted/active layout crops; duplicate/replaced crops must not inflate the source count. |
| Lifts | `features.floorEvaluation.liftCount` exists | Display a count with source/review status; do not infer it from project-wide tower metrics. |
| Stairs | `features.floorEvaluation.staircaseCount` exists | Display staircase count, with source/review status. |
| Refuge area | No structured refuge field found in the full-floor result schema/persistence | Extend the provider schema, validation, saved summary, serialization and review UI. Capture presence (`present`/`absent`/`unknown`) separately from a measured area and unit. Only show a numeric area if evidence supports it. |
| Other visible common-floor features | Not a complete typed floor-summary contract today | Support evidence-backed corridors/lobbies, common areas or service/fire lifts when explicitly identified; retain Unknown rather than inventing counts, areas or classifications. Define exact supported fields before implementing each extraction output. |

- Provide source document/page, source-image identity, analysis/profile version, analysis time and evidence/review state. A replaced source or new extraction invalidates prior review/marks its summary stale.
- Missing/not evaluated is **Unknown**, never numeric zero. A confirmed zero/absence is distinct from a missing field. Do not derive whole-floor totals from an isolated flat image.
- Keep detection evidence and reviewer corrections distinct; correction requires permission, reason, audit and conflict checks. Reviewed values must not silently overwrite original model evidence.
- The custom media response already includes `features`, but the frontend needs an explicit typed floor-summary renderer instead of dumping JSON.
- Full-floor statistics are currently persisted in the successful segmentation path, which rejects results without valid layout regions. Define how useful floor-only observations can be retained independently of crop creation; do not report a successful layout extraction when no crops were produced.
- Do not automatically reprocess all existing documents just to populate the new fields. Show Unknown for legacy records and offer the existing explicit evaluation workflow.

Sources: [full-floor evaluation persistence](../reailEstate/reailEstate_common/backend/platform_api/floor_plan_layout_jobs.py), [vision schema/provider](../reailEstate/reailEstate_common/backend/platform_api/floor_plan_vision.py), [media response](../reailEstate/reailEstate_common/backend/platform_api/builder_media.py), [media component](../reailEstate/reailEstate_common/frontend/src/BuilderMediaWorkspace.tsx), [document component](../reailEstate/reailEstate_common/frontend/src/BuilderPortal.tsx), [source-document renderer](../reailEstate/reailEstate_common/backend/platform_api/infrastructure/source_documents.py).

### R7. Order active floor plans and layouts before inactive items

- Apply the same ordering to both floor-plan and layout directories: **active A–Z, then inactive A–Z**. Do not interleave inactive items with active items merely because their names sort earlier.
- Sort names case-insensitively using natural numeric ordering, so `Flat 2` precedes `Flat 10`. Use the displayed name/alias, with a stable record-ID tie-breaker for duplicate names.
- Activity grouping is the primary sort key. If the user selects Name Z–A, reverse names within each group while keeping active items first.
- Determine activity from the record's effective state, including an inactive source where the workflow treats its derived layouts as inactive. Processing states such as queued/completed do not determine whether a record is active.
- Apply ordering after search/filter matching and before pagination. Keep the same order for visible rows and previous/next navigation. Changing activity updates the item's position without losing its selection or unsaved work.

Example default order: active `Flat 1`, `Flat 2`, `Flat 10`, then inactive `Flat 1`, `Flat 3`.

### R8. Order and filter projects consistently

- Default project order is **active projects A–Z, then inactive projects A–Z**, using case-insensitive natural name ordering and a stable ID tie-breaker.
- Use the backend's project lifecycle classification for active/inactive. Draft, ingesting and needs-review projects are not inactive merely because they are unpublished. Preserve the existing treatment of merged source records; do not reintroduce duplicate sources into the normal project list.
- Add a visible **Find a project** name search and a **Locality** filter to the compact project-list toolbar. Name search supports case-insensitive partial matches; locality options come from the authorized project scope, with an All localities option.
- Name and locality filters combine, and both active/inactive groups remain correctly sorted within the filtered results. Provide a clear/reset action and a useful no-matches state.
- Apply the same behavior to the global admin project list and the organization-scoped project selector reused from Builder. Preserve organization and authorization scope.
- Filter and sort the full authorized result set before pagination. Preserve name/locality/page context in navigation and return links; reset pagination when filters change. Do not discard unsaved project edits when changing filters or silently switch to another project.

### Screenshot acceptance cases

1. Theme regression: the same media tabs/thumbnails and new queue/review/document components remain legible and correctly themed in light and dark modes, including disabled/selected/focus states.
2. Review workflow: an authorized reviewer can inspect evidence and review an amenity, floor plan, layout and source reference without leaving the custom workspace. A non-reviewer or disallowed self-review receives a clear, enforced explanation.
3. Document browser: selecting a clean PDF/image shows the expected preview; page navigation and open-in-new-tab work; Office/unavailable/blocked/session-expired cases have truthful states.
4. Layout density: desktop image/editor panes share a row; short fields and room measurements use available width; narrow containers and zoom reflow without hidden controls or page-wide overflow.
5. Floor summary: test known positive counts, confirmed zero, unknown values, refuge presence with/without measured area, stale source, legacy records and a mismatch between detected flats and derived crops.
6. Admin-only coverage: queue/failure/worker/review/access/settings tasks have custom components, permission checks and error states; no fallback to a Django-rendered panel counts as completion.
7. Media ordering: mixed active/inactive floor plans and layouts display active A–Z followed by inactive A–Z; verify numeric names, aliases, duplicate names, search and activity changes. Name Z–A still keeps inactive items last.
8. Project ordering/filtering: mixed active/inactive projects display active A–Z followed by inactive A–Z; verify name-only, locality-only and combined filters, reset/no matches, organization scope, pagination and retained selection/unsaved edits.

## Implementation sequence

### 1. Establish parity contracts

- Turn this inventory into route/action acceptance cases with required model/object permissions, editable fields, expected errors and retained history.
- Define the CRUD policy for generic model registrations before replacing them. Preserve legitimate operations through named workflows; record deliberate restrictions instead of accidentally losing functionality.
- Cover every registered surface and custom admin action. Verify runtime registrations before final cutover, including inherited UserAdmin and default bulk/history controls.

### 2. Build the shared admin shell and authentication

- Reuse Builder components/styles/theme; extract only components actually shared by both portals.
- Add admin routes, login/logout/password change, capabilities, accessible navigation and common page states.
- Replace `admin.site.admin_view` dependence for the custom frontend with explicit authentication and capability checks. Shell access cannot be gated solely on `change_project`.
- Keep existing session/CSRF and evidence authorization protections. Never grant Builder owner membership simply to make admin pages work.
- Keep the shared frontend build if sufficient; add an explicit admin development/proxy configuration. The current Vite config chooses Builder versus Buyer, and admin currently serves the Builder bundle.
- Correct and verify shared theme behavior (R2) before reusing the controls in new admin-only views. Establish the compact list/detail/editor patterns (R1, R4, R5) as shared layout requirements.

### 3. Complete the project and review journey

- Add the global projects list and context-preserving nested project workspace.
- Port manual extraction corrections, source/transit review, project-amenity review and candidate/fact/history views.
- Add review queue/resolution, assignment/priority actions as defined, and publication.
- Add merge UI/history and cross-project retirement/reanalysis. Reuse existing services and API actions.
- Replace all project/media `adminTools` links; keep work in the same shell.
- Deliver in-place review controls (R3), the compact document browser with protected preview/open endpoints (R4), the dense side-by-side image/editor (R5), and the typed floor summary plus refuge-data contract (R6).
- Enforce active-first floor-plan/layout ordering (R7), and active-first project name ordering with combined name/locality filters in both global and organization-scoped lists (R8).

### 4. Complete access and reference-data management

- Add organization management, including inactive/suspended organization inspection and missing field parity.
- Add accounts, password administration, platform roles, organization roles and memberships.
- Add amenity taxonomy and station management/corrections.
- Preserve model-level authorization, non-escalation rules, owner safeguards and active/inactive access behavior.

### 5. Complete processing, settings and retained-record views

- Add the global jobs monitor, worker controls, dead-letter actions and replay.
- Add global documents/ingestion runs, bulk retry, outputs and audit/history browsing.
- Add feature flags, integration status and metrics.
- Add permission-restricted retained-data inspectors for consent, requirement profiles and recommendation snapshots; expose workflow actions only where defined.
- Deliver the dedicated queue/jobs/detail/worker/failure components from R1, rather than treating a project job snippet or Django link as global queue parity.

### 6. Cut over every entry point and retire Django panels

- Replace both admin-service and common URL mounts of `admin.site.urls` with custom admin routes. Keep Django auth/permissions/models and application migration ownership.
- Replace or remove Django template/static dependencies only after their behavior has custom coverage. Migrate services out of admin adapters rather than deleting domain logic.
- Update generated `reverse('admin:...')` links, frontend anchors, redirects, docs, tests and source/media links. Preserve legacy GET bookmarks through explicit mappings.
- Keep `/admin/` and preferably `/admin/login/` as compatible public entry routes with custom views, so host redirects and deployment checks remain coherent. Update release checks if expectations change.
- Update the deployment README when the admin entry/behavior changes. Release through the existing reviewed GitHub Actions workflow; no local/SSH release substitute.

## Acceptance and validation

- Every registered model surface and custom action above has an implemented custom destination or an explicitly documented read-only/workflow replacement. No silent feature loss.
- All admin entry points, login/password/session-expiry paths, confirmation/error pages and generated tool links render the application UX. No embedded Django panels or new-tab escape to Django.
- Every proposed CRUD/action is tested at its actual API boundary for permitted/denied roles and applicable object scope, not only hidden in navigation.
- Account/role changes cannot escalate privileges; organization owner protection and independent-review rules remain enforced.
- Evidence, published facts, merge history and audit records survive retirement/removal and migration. Dangerous actions retain confirmation, version checks and reasons where required.
- Test stale-write conflicts, request retries/idempotency and failure feedback on migrated mutations.
- Exercise direct links, refresh/back navigation, filters/sort/pagination, selection and unsaved changes; verify draft state clears appropriately on logout/access loss.
- Run focused backend/frontend checks and browser acceptance for representative project, review, access and queue flows. Check keyboard/focus, responsive layouts, theme and readable errors.
- Validate admin-service isolation and the common development URL configuration; Builder/Buyer permissions and workflows must continue working.
- Build and smoke-test through the existing GitHub Actions release path, including custom admin login/readiness checks.
- Run `graphify update .` after implementation changes. The 2026-10-24 continuation refreshed the code graph successfully; documentation semantics and optional Terraform/HCL/SQL extraction were not refreshed.

## Boundaries and decisions recorded by this plan

- This is a UI migration with the backend work needed for feature parity, not a new auth provider, worker architecture or storage migration.
- Backend Django remains. User-facing Django default administration does not.
- Generic raw CRUD on processing/evidence/audit/privacy records is not automatically a required product feature. The proposed policy is read-only history plus named audited actions; review these explicit restrictions during phase 1 rather than silently copying unsafe generic forms.
- Existing hard-delete APIs, particularly organizations and taxonomies, require dependency-aware behavior before the new UI exposes them. Account deactivation and role revocation should be primary controls; permanent deletion requires an explicit retention/dependency policy.
- The original inventory below records planning-time findings. Use the current implementation status above for delivered UX; complete the deferred regression suite and remaining acceptance cases before release.
