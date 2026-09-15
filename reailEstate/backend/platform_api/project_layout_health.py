"""Read-only, evidence-based boundary health shared by lists and dashboards.

No review tasks are created. Bulk annotations avoid per-project queries and the
boundary subset uses the exact predicate used by project-level counts.
"""

from uuid import UUID

from django.contrib import admin
from django.db.models import Case, CharField, Count, F, IntegerField, Q, Value, UUIDField, When
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Coalesce, Replace, Trim
from django.urls import reverse
from django.utils.html import format_html

from .floor_plan_visibility import visible_floor_plans
from .models import FloorPlan, Project, ProjectMerge, ReviewTask

BOUNDARY_REASONS = ("BAD_CROP", "WRONG_FLAT_BOUNDARY")
TERMINAL_REVIEW_STATES = ("resolved", "completed", "cancelled", "dismissed", "rejected")


def boundary_review_layouts(queryset=None):
    """Distinct visible flat records with an existing boundary flag or report.

    JSON UUID text is normalized on both sides for SQLite (UUIDs stored without
    hyphens) and PostgreSQL. Exists prevents duplicate reports inflating counts.
    Report ownership can be source OR canonical project after a merge: the plan
    reference, not the task's project FK, identifies the affected evidence.
    """
    if queryset is None:
        queryset = FloorPlan.objects.all()
    tasks = ReviewTask.objects.filter(
        task_type="layout_quality", completed_at__isnull=True,
        resolution__reasonCode__in=BOUNDARY_REASONS,
    ).exclude(state__in=TERMINAL_REVIEW_STATES).values_list("resolution__floorPlanId", flat=True)
    reported_ids = set()
    for identifier in tasks:
        try:
            reported_ids.add(UUID(str(identifier)))
        except (ValueError, TypeError, AttributeError):
            continue  # Malformed reports cannot link to an unrelated layout.
    warning = Coalesce(KeyTextTransform("qualityWarning", "raw_extraction"), Value(""), output_field=CharField())
    # SQL TRIM removes spaces only on both supported backends. Strip the other
    # ordinary whitespace characters explicitly, without backend-specific regex.
    for whitespace in ("\n", "\r", "\t"):
        warning = Replace(warning, Value(whitespace), Value(""), output_field=CharField())
    return visible_floor_plans(queryset).filter(plan_scope="flat").annotate(
        _boundary_warning=Trim(warning),
    ).filter(
        Q(review_state="needs_boundary_review") | Q(pk__in=reported_ids)
        | ~Q(_boundary_warning__in=["", "null", "[]", "{}", "false", "0"])
    ).distinct()


def with_project_layout_health(queryset):
    """Annotate Project queryset with boundary_review_count and canonical ID.

    Applied merges are one level by project_lifecycle's merge contract. Both
    canonical and alias rows get the same family count; no stale address hints
    or proposed/reverted merges expand scope. Four bulk queries for an entire list,
    independent of project count; no nested correlated report scan.
    """
    if "boundary_review_count" in queryset.query.annotations:
        return queryset
    # A correlated plan/report subquery per project made the SQLite admin count
    # take minutes and block writers. Scan evidence once, then annotate constants.
    aliases = dict(ProjectMerge.objects.filter(state="applied").values_list("source_project_id", "target_project_id"))
    counts = {}
    for row in boundary_review_layouts().order_by().values("project_id").annotate(total=Count("pk", distinct=True)):
        canonical_id = aliases.get(row["project_id"], row["project_id"])
        counts[canonical_id] = counts.get(canonical_id, 0) + row["total"]
    all_counts = counts | {source: counts.get(target, 0) for source, target in aliases.items()}
    return queryset.annotate(
        layout_health_project_id=Case(*[When(pk=source, then=Value(target, output_field=UUIDField()))
                                       for source, target in aliases.items()], default=F("pk"), output_field=UUIDField()),
        boundary_review_count=Case(*[When(pk=pk, then=Value(count)) for pk, count in all_counts.items()],
                                  default=Value(0), output_field=IntegerField()),
    )


def project_boundary_review_counts(projects):
    """Bulk mapping {project UUID: count}; accepts a Project queryset or objects."""
    if hasattr(projects, "values_list"):
        queryset = projects
    else:
        queryset = Project.objects.filter(pk__in=[project.pk for project in projects])
    return dict(with_project_layout_health(queryset).values_list("pk", "boundary_review_count"))


def project_boundary_layouts(project):
    """Canonical-family queryset for dashboard ``issues=boundaries``."""
    canonical_id = ProjectMerge.objects.filter(source_project=project, state="applied").values_list(
        "target_project_id", flat=True
    ).first() or project.pk
    return boundary_review_layouts().filter(
        Q(project_id=canonical_id)
        | Q(project__merge_out__state="applied", project__merge_out__target_project_id=canonical_id)
    )


def project_layout_health_context(project, *, admin_site_name="admin"):
    """Dashboard context using the same annotation as the ProjectAdmin list."""
    row = with_project_layout_health(Project.objects.filter(pk=project.pk)).get()
    return {
        "boundary_review_count": row.boundary_review_count,
        "boundary_review_url": reverse("admin:platform_api_project_media_dashboard", args=[row.layout_health_project_id],
                                       current_app=admin_site_name) + "?section=layouts&issues=boundaries",
    }


class NeedsBoundaryReviewFilter(admin.SimpleListFilter):
    title = "Needs boundary review"
    parameter_name = "boundary_review"

    def lookups(self, request, model_admin):
        return [("yes", "Needs boundary review"), ("no", "No boundary issues")]

    def queryset(self, request, queryset):
        if self.value() not in {"yes", "no"}:
            return queryset
        queryset = with_project_layout_health(queryset)
        return queryset.filter(boundary_review_count__gt=0) if self.value() == "yes" else queryset.filter(boundary_review_count=0)


class ProjectLayoutHealthAdminMixin:
    """Add boundary_review_link to list_display and NeedsBoundaryReviewFilter to list_filter."""

    def get_queryset(self, request):
        return with_project_layout_health(super().get_queryset(request))

    @admin.display(description="Needs boundary review", ordering="boundary_review_count")
    def boundary_review_link(self, obj):
        # Requires the annotated queryset, rather than an accidental N+1 fallback.
        url = reverse("admin:platform_api_project_media_dashboard", args=[obj.layout_health_project_id],
                      current_app=self.admin_site.name) + "?section=layouts&issues=boundaries"
        if obj.boundary_review_count:
            return format_html('<a href="{}" style="font-weight:700;color:var(--error-fg)">⚠ {} layouts</a>', url, obj.boundary_review_count)
        return "—"