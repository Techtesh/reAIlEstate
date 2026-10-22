# Bulk layout removal and boundary health integration (items 1 / 4)

These additions are opt-in. Existing admin registration, dashboard/card templates,
queue implementation, models and migrations were not changed. No existing database
records were removed, and Python, tests, lint and migrations were not executed.

## ProjectAdmin hooks for the main integrator

- Import `LayoutBulkAdminMixin` from `platform_api.layout_bulk_admin` and
  `ProjectLayoutHealthAdminMixin`, `NeedsBoundaryReviewFilter` from
  `platform_api.project_layout_health`.
- Add both mixins before the existing `ProjectAdmin` bases. The health mixin wraps
  `get_queryset()` in `with_project_layout_health()`; keep the existing cooperative
  `super().get_queryset(request)` and canonical-project exclusions.
- Prepend `self.layout_bulk_urls()` in `ProjectAdmin.get_urls()`, before the
  object/change catch-all. Alternatively add the equivalent route explicitly:
  `<uuid:object_id>/layouts/bulk-remove/`, handler
  `self.admin_site.admin_view(self.layout_bulk_remove_view)`, name
  `platform_api_project_layout_bulk_remove`.
- Full default route: `/admin/platform_api/project/<project UUID>/layouts/bulk-remove/`.
  Reverse: `admin:platform_api_project_layout_bulk_remove`, args `[project.pk]`.
- Append `"boundary_review_link"` to `list_display` and
  `NeedsBoundaryReviewFilter` to `list_filter`. The filter title is **Needs boundary
  review**, query parameter `boundary_review=yes` (or `no`). Sorting uses the
  `boundary_review_count` SQL annotation, not per-row queries.
- No automatic URL registration or admin action performs removal. The explicit
  method is `layout_bulk_remove_view(request, object_id)`. The template override
  attribute is `layout_bulk_confirmation_template`.

## Card selection and confirmation protocol

1. Add a CSRF-protected POST form directed to the above bulk endpoint. Checked
   layout cards submit repeated **`selected_ids`** UUID values. An optional hidden
   **`stage=select`** is supported (it is the default).
2. **Select all visible** must only check cards actually visible in the current
   subset/page. Submit those concrete IDs, not a project-wide `select_all` flag.
   The endpoint deliberately never expands a query or flag into a selection.
   At most 200 submitted IDs are accepted; UUID duplicates within the bound are
   deduplicated. Do not nest this form inside existing card action forms; use a
   separate form with the HTML `form` attribute on associated card checkboxes.
3. GET ignores all selected-ID/action query parameters and does not remove anything.
   The first POST validates permissions and the full project-family selection,
   then renders the exact records, their review states, and pending job count.
4. The new confirmation template sends **`stage=confirm`**, a signed
   **`selection_token`**, JSON **`expected_updated_at`** mapping each selected UUID
   to its ISO timestamp, CSRF token, and a deliberate required **`confirm`** checkbox.
   Tokens bind the exact selection/versions to the actor and dashboard project and
   expire after 30 minutes. No initial card timestamp or client-written token is needed.
5. Success redirects to the **same project** via
   `admin:platform_api_project_media_dashboard` + `?section=layouts` and reports
   selected / removed / unchanged / cancelled-job counts in the admin message.
   Initial invalid selections return 400; unauthorized access 403; unavailable or
   foreign selections 404; changed/expired/busy confirmations 409. There is no
   partial-success path. Return to layouts and select again after errors.

Service API: `remove_floor_plans(project, plan_ids, actor, *, expected_updated_at)`.
Timestamp values here are datetimes, not serialized strings. It returns
`BulkRemovalResult(selected_count, removed_count, unchanged_count, cancelled_job_count)`.
The existing `set_floor_plan_removed(project, plan_id, actor, *, removed,
expected_updated_at=None)` signature and `(plan, changed)` result are preserved.

Both APIs share a non-recursive locked helper. They acquire unique sorted document
work locks, then sorted pending-outbox work locks, before the short transaction.
Inside it they lock documents, plans and pending outbox rows in sorted order. A job
inserted after work-lock discovery forces retry; plan/document row locks serialize
against queue writers even when those writers use no document work lock. PostgreSQL
row contention fails fast with NOWAIT. Queue writers must check hidden state under the same plan row lock.

Every selected plan gets its own existing `floor_plan.removed_by_user` audit with
`previousReviewState` and cancelled job/operation IDs. Existing per-record restore
continues to work and never restarts jobs. Only pending page/crop/units jobs whose
`payload.floorPlanId` is that exact plan are cancelled. Shared operations and
unrelated, child, whole-document, completed or already stopped jobs are not cancelled.
Original documents, images, assets, provenance and records are never deleted.

## Shared health helpers and dashboard integration

- `with_project_layout_health(project_queryset)` returns a queryset annotated with
  **`boundary_review_count`** and **`layout_health_project_id`** (canonical UUID).
- `project_boundary_review_counts(projects)` returns `{project_uuid: count}` in one
  SQL query for a Project queryset or iterable of Project objects.
- `boundary_review_layouts(optional_floorplan_queryset)` applies the shared predicate.
- `project_boundary_layouts(project)` returns the authoritative canonical-family
  boundary-issues queryset, including merged-source evidence.
- Add `project_layout_health_context(project, admin_site_name=self.admin_site.name)`
  to the media dashboard context. Its keys are **`boundary_review_count`** and
  **`boundary_review_url`**. Render this same count and link on the dashboard.
- The link is `?section=layouts&issues=boundaries` on the canonical media dashboard.
  In that branch use `project_boundary_layouts(project)` as the layout set before
  card building / evaluation-status lookup. Do not reapply the dashboard's legacy
  latest-workflow pruning to this subset: doing so can hide reported evidence and
  make the list count disagree with the linked subset. Normal non-issue views may
  retain their existing display rules. Keep related-object loading and card sorting.

Only visible flat records count, once each, when any of these is true:

- `review_state == "needs_boundary_review"`;
- nonempty `raw_extraction.qualityWarning` (whitespace, null and empty JSON values
  do not count);
- an unresolved `layout_quality` task references the plan via `resolution.floorPlanId`
  with `reasonCode` **BAD_CROP** or **WRONG_FLAT_BOUNDARY**.

Completed timestamps and terminal task states are excluded. Removed, replaced and
superseded-page-candidate plans are excluded even with flags/reports. Applied
`ProjectMerge` relations determine families, not address hints. Task project may be
the canonical project or a merged source; the referenced plan determines attribution.
Existing flags remain counted even when a separate report is resolved. No findings,
reports, quality inferences or database writes are created by these helpers.

## Verification status

Written contracts cover Acme-style flags, merged report ownership, duplicate reports,
hidden and non-flat plans, bulk query counts, filter/link consistency, single-record
restore compatibility, bounded batches, stale/foreign/unauthorized selection, audit
rollback, document/outbox lock ordering, newly queued jobs, PostgreSQL row contention,
CSRF, deliberate confirmation, actor/project-bound tokens and redirect counts.
They are **not executed**; runtime/SQL behavior remains unverified. Editor diagnostics
are the only requested validation performed during this change.