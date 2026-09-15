"""Boundary health reflects existing evidence only, with constant bulk query count."""

import pytest
from django.contrib.admin import AdminSite, ModelAdmin
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import path
from django.utils import timezone

from platform_api.floor_plan_visibility import HIDDEN_REVIEW_STATES
from platform_api.models import AuditEvent, Project, ProjectMerge, ReviewTask
from platform_api.project_layout_health import (
    NeedsBoundaryReviewFilter, ProjectLayoutHealthAdminMixin, boundary_review_layouts,
    project_boundary_layouts, project_boundary_review_counts, project_layout_health_context,
    with_project_layout_health,
)
from platform_api.tests.layout_bulk_helpers import layout_records

pytestmark = pytest.mark.django_db


class HealthAdmin(ProjectLayoutHealthAdminMixin, ModelAdmin):
    list_display = ("submitted_name", "boundary_review_link")
    list_filter = (NeedsBoundaryReviewFilter,)

    def get_urls(self):
        return [path("<uuid:object_id>/media/", lambda request, object_id: HttpResponse("Layouts"),
                     name="platform_api_project_media_dashboard")] + super().get_urls()


site = AdminSite(name="health_test")
site.register(Project, HealthAdmin)
urlpatterns = [path("admin/", site.urls)]


def report(project, plan, *, reason="BAD_CROP", state="open", completed_at=None):
    return ReviewTask.objects.create(
        project=project, task_type="layout_quality", state=state, completed_at=completed_at,
        resolution={"floorPlanId": str(plan.pk), "reasonCode": reason},
    )


def test_acme_flags_and_reports_union_without_inventing_findings(layout_records, django_assert_num_queries):
    _, project, make = layout_records
    state_only = make(state="needs_boundary_review")
    warning_only = make(state="gen_with_ai_needs_review", raw={"qualityWarning": "Crop evaluation did not find exactly one dwelling."})
    report_only = make()
    all_three = make(state="needs_boundary_review", raw={"qualityWarning": "Wrong dwelling boundary"})
    report(project, report_only)
    report(project, report_only, reason="WRONG_FLAT_BOUNDARY", state="in_progress")
    report(project, all_three)
    report(project, all_three)
    make(state="needs_review")  # No generic pending-review inference.
    make(raw={"dimensions": [], "roomCount": None})  # Missing facts are not automatic boundary findings.
    make(scope="floor", state="needs_boundary_review", raw={"qualityWarning": "Full floor"})
    existing_tasks = ReviewTask.objects.count()
    with django_assert_num_queries(4):
        counts = project_boundary_review_counts(Project.objects.all())
    assert counts[project.pk] == 4
    assert set(boundary_review_layouts().values_list("pk", flat=True)) == {
        plan.pk for plan in (state_only, warning_only, report_only, all_three)
    }
    assert ReviewTask.objects.count() == existing_tasks
    assert not AuditEvent.objects.exists()


@pytest.mark.parametrize("warning", [None, "", "  \n ", [], {}, False, 0])
def test_empty_quality_warning_is_not_a_finding(layout_records, warning):
    _, project, make = layout_records
    make(raw={"qualityWarning": warning})
    assert project_boundary_review_counts([project]) == {project.pk: 0}


@pytest.mark.parametrize("warning", ["bad crop", ["ambiguous boundary"], {"reason": "overlap"}])
def test_nonempty_quality_warning_is_a_finding(layout_records, warning):
    _, project, make = layout_records
    make(raw={"qualityWarning": warning})
    assert project_boundary_review_counts([project])[project.pk] == 1


@pytest.mark.parametrize("hidden_state", HIDDEN_REVIEW_STATES)
def test_removed_replaced_superseded_ignore_all_flags_and_reports(layout_records, hidden_state):
    _, project, make = layout_records
    plan = make(state=hidden_state, raw={"qualityWarning": "Acme bad boundary"})
    report(project, plan)
    assert not project_boundary_layouts(project).exists()
    assert project_boundary_review_counts([project])[project.pk] == 0


@pytest.mark.parametrize("state", ["resolved", "completed", "cancelled", "dismissed", "rejected"])
def test_resolved_reports_do_not_count_but_remaining_raw_flag_does(layout_records, state):
    _, project, make = layout_records
    no_flag = make()
    raw_flag = make(raw={"qualityWarning": "Still in extraction evidence"})
    report(project, no_flag, state=state)
    report(project, raw_flag, state=state)
    assert list(project_boundary_layouts(project).values_list("pk", flat=True)) == [raw_flag.pk]


def test_wrong_report_reason_type_missing_reference_or_completed_timestamp_ignored(layout_records):
    _, project, make = layout_records
    plan = make()
    report(project, plan, reason="MISSING_ROOMS")
    report(project, plan, reason="DUPLICATE")
    report(project, plan, completed_at=timezone.now())
    ReviewTask.objects.create(project=project, task_type="floor_plan", resolution={
        "floorPlanId": str(plan.pk), "reasonCode": "BAD_CROP",
    })
    ReviewTask.objects.create(project=project, task_type="layout_quality", resolution={"reasonCode": "BAD_CROP"})
    ReviewTask.objects.create(project=project, task_type="layout_quality", resolution={
        "floorPlanId": "not-a-uuid", "reasonCode": "BAD_CROP",
    })
    assert project_boundary_review_counts([project])[project.pk] == 0


def test_merged_family_evidence_and_canonical_reports_no_duplicates(layout_records, django_assert_num_queries):
    _, canonical, make = layout_records
    source = Project.objects.create(organization=canonical.organization, submitted_name="Acme source", status="merged")
    ProjectMerge.objects.create(source_project=source, target_project=canonical, state="applied")
    sibling = Project.objects.create(organization=canonical.organization, submitted_name="Acme other evidence", status="merged")
    ProjectMerge.objects.create(source_project=sibling, target_project=canonical, state="applied")
    own_plan = make(state="needs_boundary_review")
    source_plan = make(owner=source)
    sibling_plan = make(owner=sibling, raw={"qualityWarning": "Boundary overlaps"})
    report(canonical, source_plan)
    report(source, source_plan, reason="WRONG_FLAT_BOUNDARY")
    report(sibling, source_plan)
    removed = make(owner=source, state="removed_by_user", raw={"qualityWarning": "Removed"})
    report(canonical, removed)
    unrelated = Project.objects.create(organization=canonical.organization, submitted_name="Unrelated")
    make(owner=unrelated, state="needs_boundary_review")
    with django_assert_num_queries(4):
        counts = project_boundary_review_counts(Project.objects.all())
    assert counts == {canonical.pk: 3, source.pk: 3, sibling.pk: 3, unrelated.pk: 1}
    for project in [canonical, source, sibling]:
        assert set(project_boundary_layouts(project).values_list("pk", flat=True)) == {
            own_plan.pk, source_plan.pk, sibling_plan.pk,
        }


@pytest.mark.parametrize("merge_state", ["proposed", "reverted"])
def test_unapplied_merges_and_address_hints_do_not_expand_scope(layout_records, merge_state):
    _, canonical, make = layout_records
    alias = Project.objects.create(organization=canonical.organization, submitted_name="Not applied",
                                   address={"mergedIntoProjectId": str(canonical.pk)})
    ProjectMerge.objects.create(source_project=alias, target_project=canonical, state=merge_state)
    make(owner=alias, state="needs_boundary_review")
    assert project_boundary_review_counts([canonical, alias]) == {canonical.pk: 0, alias.pk: 1}


def test_health_filter_link_and_dashboard_use_same_count_without_n_plus_one(layout_records, settings, django_assert_num_queries):
    settings.ROOT_URLCONF = __name__
    actor, project, make = layout_records
    make(state="needs_boundary_review")
    empty = Project.objects.create(organization=project.organization, submitted_name="Empty")
    for number in range(10):
        Project.objects.create(organization=project.organization, submitted_name=f"Empty {number}")
    request = RequestFactory().get("/admin/platform_api/project/")
    request.user = actor
    model_admin = HealthAdmin(Project, site)
    with django_assert_num_queries(4):
        rows = list(model_admin.get_queryset(request))
    with django_assert_num_queries(0):
        links = [str(model_admin.boundary_review_link(row)) for row in rows]
    assert any("issues=boundaries" in link and "1 layouts</a>" in link for link in links)
    yes = NeedsBoundaryReviewFilter(request, {"boundary_review": ["yes"]}, Project, model_admin)
    no = NeedsBoundaryReviewFilter(request, {"boundary_review": ["no"]}, Project, model_admin)
    assert list(yes.queryset(request, Project.objects.all()).values_list("pk", flat=True)) == [project.pk]
    assert empty.pk in no.queryset(request, Project.objects.all()).values_list("pk", flat=True)
    assert project.pk not in no.queryset(request, Project.objects.all()).values_list("pk", flat=True)
    with django_assert_num_queries(4):
        context = project_layout_health_context(project, admin_site_name=site.name)
    assert context["boundary_review_count"] == 1
    assert context["boundary_review_url"].endswith(f"/{project.pk}/media/?section=layouts&issues=boundaries")
    assert context["boundary_review_count"] == project_boundary_layouts(project).count()
    assert with_project_layout_health(Project.objects.filter(pk=empty.pk)).get().boundary_review_count == 0