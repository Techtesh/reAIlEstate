# Items 5/6: edit the existing unit crop

## Main integration (not applied here)

1. Import `LayoutCropUpdateAdminMixin` from
   [platform_api/layout_crop_update_admin.py](platform_api/layout_crop_update_admin.py)
   and add it before `ModelAdmin` in `FloorPlanAdmin`'s bases.
2. Prepend `self.layout_crop_update_urls()` to `FloorPlanAdmin.get_urls()`'s
   returned routes, before the generic admin catch-all routes. The helper wraps
   each view in `self.admin_site.admin_view`; do not register unwrapped callbacks.
3. Add an **Edit crop** link on eligible unit cards using
   `admin:platform_api_floorplan_crop_update`, with `args=[plan.pk]` and the
   current admin site's namespace. Always pass the **target unit ID**.
   Do not call `_manual_crop_source`, `create_manual_layout_crops`, the draft-cart
   action, or source-page addition endpoints for this operation.
4. Link to the route without query parameters for the default saved-unit trim.
   Only an explicit **Original source** link uses `?source_mode=original`.
   The dedicated editor already provides both links.

### Exact route names and callbacks

| URL below the FloorPlan admin prefix | Name under `admin:` | Callback |
| --- | --- | --- |
| `<uuid:object_id>/edit-crop/` | `platform_api_floorplan_crop_update` | `layout_crop_update_view` |
| `<uuid:object_id>/edit-crop/preview/` | `platform_api_floorplan_crop_update_preview` | `layout_crop_update_preview` |

The editor returns to the existing `admin:platform_api_project_media_dashboard`
route, with the canonical project ID and `?section=layouts`.

## Service contract

Implementation: [platform_api/layout_crop_update.py](platform_api/layout_crop_update.py).

- `editable_layout(plan_id, actor, project_id=None)` returns the authorized
  `(plan, canonical_project)`; an optional project ID must equal the canonical ID.
- `prepare_crop_edit(plan_id, actor, source_mode="current")` returns
  `(plan, canonical_project, state, signed_token)`. `source_mode` is keyword-only.
- `crop_edit_preview(plan_id, actor, token)` returns verified PNG/JPEG bytes.
- `update_layout_crop(plan_id, actor, *, editor_token, expected_asset_id,
  expected_sha256, source_sha256, source_mode, regions, rotation_degrees=0,
  confirm=False)` returns `floorPlanId`, `assetId`, `projectId`, `messageId`,
  and `operationId` as strings. It creates no FloorPlan or draft.
- `regions` must contain exactly one finite normalized `{x, y, width, height}`
  rectangle. Full-range rectangles and trims over 70% are allowed. Subpixel
  empty selections are rejected. Rotation is 0/90/180/270 **clockwise**, applied
  before cropping; coordinates refer to that rotated preview.
- `source_sha256` is the checksum of the selected preview image. The signed state
  separately pins the original document checksum, source identity/page, target
  asset ID/checksum, actor, canonical project and plan/asset timestamps.
- `StaleCropEdit` indicates stale/tampered/expired state; admin returns 409 and
  requires a deliberate reload. Invalid rectangles/confirmation return 400.
- All image entrypoints must run outside DB transactions (`ATOMIC_REQUESTS`
  must not wrap these routes). A document work lock spans image I/O and save;
  only final persistence/queueing uses a DB transaction. No model is invoked.

## Persistence and evaluation

- Creates a unique immutable `unit_crop` asset, ingestion run and workflow.
  Retains the plan's ID, names, alias, project, document, page and original workflow.
- Clears image-dependent facts; sets `manual_crop_needs_evaluation`. Retains the
  prior complete raw extraction and fact fields in `cropRevisionHistory[].previous`
  plus the `floor_plan.crop_updated` audit. The old asset remains referenced there
  and is never mutated or deleted. Current raw metadata contains provenance only;
  old image warnings/model output are archived, not displayed as current facts.
- Keeps the original `segmentationMethod` when present; otherwise sets `manual`
  so the existing crop evaluator does not rename the unit. `display_name` is
  never changed. No evaluator file changes are required for identity preservation.
- Marks pending matching old-image jobs for this plan cancelled, not sibling or
  document-wide jobs. Rejects shared operation corruption instead of cancelling
  another plan's operation. Uses runtime import of `queue_layout_evaluation(plan,
  actor=actor)` inside the short transaction; does not run workers.
- Reconciles open old-image floor-plan and image-quality tasks for this target;
  leaves duplicate/other reports, different-asset tasks and sibling tasks alone.
  The fresh generic review task uses the existing worker's exact lookup shape.
- `extractionStale` records the invalidation. Current completion must be determined
  using the new asset/checksum-bound queue status / `cropEvaluation` metadata;
  the unchanged evaluator deliberately does not clear arbitrary stale flags.

## Scope and verification

Only new service, mixin/form, dedicated template/static assets, this handoff and
[platform_api/tests/test_layout_crop_update.py](platform_api/tests/test_layout_crop_update.py)
were added. Existing admin, templates, queues, models and migrations were untouched.
Tests include a private test AdminSite to exercise the opt-in routes independently.
No Python, tests, lint, migrations, model calls or actual database mutations were
run during implementation. Editor diagnostics are the only validation performed.

As with existing image tools, legacy writers that bypass document ownership must
not be used concurrently with this editor. Route unit crop editing through this
service rather than the older in-place normalization or crop-draft actions.