# Administrative skills

These are explicit, review-gated operations over already-ingested evidence. They do not run automatically during document ingestion.

## `floor-plan-flat-separation`

Separates one saved full-floor `FloorPlan` into individual flat crops. The source asset must be ready, checksum-valid, and associated with a clean document. Generated `FloorPlanAsset` records use `asset_type=unit_crop`; generated plans remain pending review.

### Modes

- `script`: local deterministic CV only. Color is one supporting signal; numbered markers and geometry may also be used. Ambiguous results fail closed.
- `ai`: invokes the `floor-plan-flat-separation-ai` query skill with a pinned, administrator-configured vision profile.
- `both`: invokes script and AI independently. A failure in one mode does not block the other; the result is `partially_completed` when only one succeeds.

`expectedCount` is an optional quality gate from known evidence. It never instructs either mode to invent or merge flats. The full-floor source remains active unless `supersedeSource` is explicitly selected and every requested mode succeeds.

### Django admin portal

1. Sign in to `/admin/` with a staff account that has permission to change floor plans.
2. Open **Floor plans**, then open a clean, ready full-floor/source record.
3. Select **Separate flats**.
4. Choose `script`, `ai`, or `both`, optionally enter the expected count, and select a configured AI profile.
5. Run the operation and review every generated crop through the normal floor-plan review workflow.

The portal never accepts model endpoints or credentials. AI profiles come only from `FLOOR_PLAN_VISION_PROFILES`.

### Project media dashboard

#### Manual crop drafts and explicit replacement

The active media tab shows only its own **Add missing…** action, aligned to the
right of the tabs. **Add missing layout** is crop-first: choose a document and page,
draw one or more rectangles, edit crop labels, rotate if needed, then **Save to cart**.
Previous/Next and the page dropdown warn before abandoning unsaved selections.
Whole-page layout checkboxes are not offered. Saved drafts accumulate across pages.
The amenity and floor-plan pickers still offer selected whole-page additions.

**Crop manually → Save to cart** saves private temporary `LayoutCropDraft` records,
not `FloorPlan`, `FloorPlanAsset`, ingestion runs or evaluation messages. Each user
may hold at most 20 drafts across projects. The cart shows only that user's drafts
for the current project, with image previews and selection checkboxes.

**Add selected to project** attaches only the selected drafts as new layouts,
queues their evaluation, and removes those drafts from the cart. Unselected drafts
stay in the cart. **Discard selected drafts** removes only drafts. These operations
never automatically replace layouts or hide an earlier manual addition.

**Replace selected layouts** is a separate confirmed user action. Only explicitly
selected old layouts are marked `replaced_by_user`; their evidence is retained and
a `layout.user_replacement` audit event records old/new IDs. Selecting an old layout
without choosing and confirming Replace has no effect. Existing manual crops made
before this feature remain project layouts, not retroactively converted drafts.

Page-crop drafts reference an original document, page and checksum rather than
requiring an existing floor-plan record. Save creates only draft records. On explicit
attachment, source-page evidence is reused or created without creating a whole-page
layout placeholder; only selected crops become layouts. Existing plan-based drafts
remain supported. Originals must be clean and match the preview checksum, including
on attachment. No original document is changed.

Whole-page floor-plan and amenity additions enqueue `source.page_evaluation.requested`.
Floor-plan additions save explicit page candidates; amenities have no plan placeholder.
The dashboard's **Open page to crop layouts** shortcut now opens the crop-first picker
without queueing a whole page. The separate layout-mark API retains explicit page
classification for API callers: mark and page task creation are atomic and repeated
requests reuse existing tasks/results.
The worker evaluates only those page images, not the whole document. Amenity pages
use project-page vision and materialize recognized taxonomy claims with page/image
evidence and an unreviewed fact candidate. **Queue saved amenity pages / retry failed**
recovers historical image-only additions without duplicating images or completed jobs.
Supplemental claims never replace the latest whole-document output or prior evidence.

A full-floor page is not assumed to be one flat: classified floor results enqueue
`floor_plan.units_evaluation.requested`, which finds dwelling boundaries and then
queues each crop for fact extraction. Existing floor cards offer **Extract individual
layouts / retry failed split** and **Crop manually**. Run queued evaluations again
while child tasks remain; each click processes at most 20 tasks. Failed/stopped tasks
remain visible. Repeated additions through both plan tabs reuse the same page asset/job.

**Remove duplicate plan…** confirms removal of only the selected record, marks it
`removed_by_user`, and cancels its pending evaluation jobs. Originals, shared assets,
child layouts and provenance remain. Restore from the floor-plan admin record;
restoring does not restart cancelled tasks. New split crops do not hide existing
document-page layouts. Manual crop Save remains draft-only.

#### Extraction status and display names

Each layout card distinguishes queued, running, failed, cancelled and completed
page/crop evaluation. Empty dimensions are pending while work is queued, not failures.
**Queue extraction** is enqueue-only: it does not run inference or acquire document
ownership in the HTTP request. Requests deduplicate by layout ID, asset ID and checksum;
queued, running and completed work is reused. **Requeue failed extraction** explicitly
retries a failed job using its original model selection. Cancelled jobs are not restarted.
Processing uses the separate **Run queued evaluations** control or a worker.
Boundary-invalid crops stop retrying automatically; their layout needs correction.
AI extraction records every visibly labeled space with two readable dimensions, including
nooks, foyers, passages, corridors, utility areas, balconies and decks; narrow dimensions
down to 0.5 ft are retained. Preview a corpus-wide refresh with
`python manage.py requeue_all_layout_evaluations`, then add `--execute` to force completed
eligible layouts through the current configured vision profile. Process the durable media
queue separately with `python manage.py process_outbox --event-type media --drain`.

**Edit display name → Save display name** stores a reviewer-owned alias separately
from the internal `plan_name`/unique key. Blank clears the alias. Cards and API display
titles strip known generated UUID/segment suffixes from fallback names; original names
remain in provenance. New attached crops use their chosen label as the initial alias.
Aliases are audited and survive extraction updates. Migrations 0010 and 0011 add
page-sourced drafts and layout display names, respectively.

#### Media tools v7

- The prominent **Draft cart** icon opens an owner-only cross-project draft index,
    with links to each project's attach/discard controls. Draft and evaluation queues
    remain separate.
- Layouts have selection checkboxes and **Remove selected…** (up to 200). The first
    POST only displays a signed, timestamped confirmation. Confirmed removal is atomic,
    scoped to the project family, audited per layout and reversible. Originals and
    shared assets remain unchanged; only selected per-layout jobs are cancelled.
- **Update this unit crop** edits one existing layout without changing its ID, names
    or alias. The default preview is its actual saved image. Choose **Original source**
    explicitly to redraw a larger boundary. Save requires one selected rectangle and
    confirmation to **Save to cart**; this creates only a private draft, without
    changing the layout or queue. Stale editors cannot overwrite newer images/extraction.
- Missing floor-plan and amenity page selections also **Save to cart** (20 drafts
    maximum across projects). Cart entries identify new layouts, page additions and
    existing-layout updates. **Commit saved pages/updates**, with confirmation, applies
    those selected changes and queues evaluation. Failed drafts remain in the cart;
    source checksums and update target versions are rechecked at commit.
- Committed crop updates create a new immutable asset and archive old facts/image references
    in crop-revision history. Old facts are cleared from the current layout pending
    checksum-bound evaluation. **Create new crops from source** remains a separate,
    draft-only action and intentionally opens the full source.
- The Layouts carousel uses saved unit crops and pre-cut layout sources; floor plans
    are displayed in the Floor plans tab. Cards label records without a saved unit crop.
- Project lists expose **Needs boundary review** counts/filter. The media banner links
    to flagged layouts across the canonical project family. Removed/replaced layouts
    do not inflate counts; existing flags/reports are used, not inferred from empty facts.
- Shared scoped styling standardizes action sizes and placement. The media byline
    displays recorded project locality, or explicitly says the location is not recorded.

The **Run updated layout tools (SQLite 8001)** VS Code task serves the regenerated
SQLite catalog at `http://127.0.0.1:8001/admin/`. It is separate from an older
`--noreload` server that may still occupy port 8000. Fresh shell checks alone do not
prove the live server loaded new admin routes. Do not run corpus writers against
SQLite and PostgreSQL simultaneously.

Open **Admin → Projects → Media dashboard** for a project-scoped view. Direct tabs show:

- structured amenities and amenity images,
- full-floor plans,
- individual layouts with configuration, carpet area, room/bathroom/balcony counts, orientation, confidence, review state, extractor version, room dimensions, calculated rectangular room areas, features, and raw provenance.

Each layout has **Report for update**. An administrator selects a reason and optional notes; this creates one deduplicated, high-priority `layout_quality` review task and an audit event. It never edits or regenerates evidence directly. Reviewers correct or reject the layout through the existing floor-plan review workflow, which resolves the associated quality report.

Generated unit-crop asset records also provide **Rotate / normalize layout**. Rotation is limited to safe 45-degree increments, applies only to the derived crop, trims white canvas, and adds a consistent margin. The immutable full-floor source is not changed. Previous checksums, incremental/cumulative rotation, and normalization history remain in provenance.

### Admin API

Profile discovery:

- `GET /api/v1/admin/floor-plan-segmentation`

Invocation:

- `POST /api/v1/admin/floor-plans/{floorPlanId}:segment-units`
- Required header: `Idempotency-Key`
- Body fields: `mode`, optional `expectedCount`, optional `visionProfileId`, and optional boolean `supersedeSource`.

Only authenticated Django staff users are authorized. Responses contain authenticated media URIs and nonsecret provider-selection metadata, never storage keys or credentials.

### Local/development CLI

The CLI is rejected unless `DEBUG=true` and `ENVIRONMENT` is `local` or `development`.

Configured-profile example:

    python manage.py isolate_floor_plan_units <floor-plan-id> --mode both --expected-count 4 --vision-profile-id <profile-id> --report reports/flat-separation.json

Script-only example:

    python manage.py isolate_floor_plan_units <floor-plan-id> --mode script --expected-count 4 --report reports/flat-separation-script.json

Bulk script extraction for all missing eligible sources:

    python manage.py separate_all_floor_plan_units --report reports/all-project-flat-separation.json

The bulk command processes one unique clean full-floor source asset only when it has no existing script-segmentation workflow. It preserves source records, does not overwrite existing script crops, checkpoints each result, and records ambiguous/insufficient sources as failures for AI or human follow-up.

For a full local refresh, run the backup-first `rebuild_script_outputs` workflow before the bulk separator and AI classifier. It replaces only eligible unreviewed generated extracts, preserves original PDFs and account/project records, and writes a restore bundle. AI classification then operates on rebuilt, checksum-verified PNG candidates and resumes from its checkpoint report after interruption.

Safe generated-image cleanup is preview-first:

    python manage.py cleanup_unreferenced_images
    python manage.py cleanup_unreferenced_images --execute

Execution is local/development-only and deletes only files under `output/media/images` that have no `FloorPlanAsset` database reference. It never deletes a referenced source or derived image. The project dashboard independently hides older duplicate generated sets and page-level candidates by default; those auditable database records remain available through the explicit candidate view and model admin.

A local endpoint/model override remains available with `--endpoint`, `--model`, and optionally `--prompt-api-key`. Keys are read through hidden input, are not written to reports, and the prior process environment is restored.

## `floor-plan-flat-separation-ai`

Internal query skill implemented by `FloorPlanFlatSeparationAISkill`. It accepts image bytes, media type, optional expected count, and an optional configured profile ID. It pins the profile, calls the existing bounded vision provider, validates the structured region response, and returns normalized regions plus nonsecret provenance. Call it through `separate_floor_plan_units()` so source validation, persistence, audit events, and review-task creation are consistently applied.
