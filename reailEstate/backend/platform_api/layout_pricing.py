"""Manually owned layout prices and visibility-aware project price ranges."""

from uuid import UUID, uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Case, Exists, F, Max, Min, OuterRef, Q, Subquery, When
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404

from .floor_plan_visibility import visible_floor_plans
from .models import AuditEvent, FloorPlan, Project, ReviewTask
from .project_lifecycle import project_family_ids


def pricing_layouts(*, can_review=False):
    inactive_sources = visible_floor_plans(FloorPlan.objects.filter(
        plan_scope="floor", active=False, document_id=OuterRef("document_id"),
    )).alias(source_page=Coalesce("page_number", 0)).filter(source_page=OuterRef("pricing_page"))
    plans = visible_floor_plans(FloorPlan.objects.filter(
        plan_scope="flat", active=True, document__scan_status="clean",
    )).exclude(review_state="rejected").alias(
        pricing_project_id=Case(
            When(project__merge_out__state="applied", then=F("project__merge_out__target_project_id")),
            default=F("project_id"),
        ),
        pricing_document_project_id=Case(
            When(document__project__merge_out__state="applied",
                 then=F("document__project__merge_out__target_project_id")),
            default=F("document__project_id"),
        ),
        pricing_page=Coalesce("page_number", 0),
    ).filter(pricing_project_id=F("pricing_document_project_id")).filter(~Exists(inactive_sources))
    plans = plans.filter(Q(asset__isnull=True) | Q(asset__status="ready", asset__document_id=F("document_id")))
    return plans if can_review else plans.filter(review_state="approved")


def project_price_range(project, *, can_review=False):
    if project.status in {"retired", "merged"} or (not can_review and project.status != "published"):
        return None, None
    values = pricing_layouts(can_review=can_review).filter(pricing_project_id=project.pk).aggregate(
        minimum=Min("price"), maximum=Max("price"),
    )
    return values["minimum"], values["maximum"]


def annotate_project_prices(queryset, *, can_review=False):
    plans = pricing_layouts(can_review=can_review).filter(
        pricing_project_id=OuterRef("pk"), price__isnull=False,
    )
    return queryset.annotate(
        layout_price_min=Subquery(plans.order_by("price").values("price")[:1]),
        layout_price_max=Subquery(plans.order_by("-price").values("price")[:1]),
    )


@transaction.atomic
def save_layout_price(project, floor_plan_id, price, actor, *, expected_updated_at):
    if not (actor and actor.is_authenticated and actor.is_active and actor.is_staff
            and actor.has_perm("platform_api.change_floorplan")
            and actor.has_perm("platform_api.change_project")):
        raise PermissionDenied("Changing layout prices requires project and floor-plan change permissions.")
    project = Project.objects.select_for_update().get(pk=project.pk)
    if project.status in {"retired", "merged"}:
        raise ValueError("Use an active project to edit layout prices.")
    try:
        plan_id = UUID(str(floor_plan_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("Select a valid layout.") from exc
    family = project_family_ids(project)
    plan = get_object_or_404(FloorPlan.objects.select_for_update(), pk=plan_id,
                            project_id__in=family, document__project_id__in=family)
    if expected_updated_at is None or plan.updated_at != expected_updated_at:
        raise ValueError("This layout changed. Reload before saving its price.")
    if not pricing_layouts(can_review=True).filter(pk=plan.pk).exists():
        raise ValueError("An active, clean individual layout is required for pricing.")
    price = FloorPlan._meta.get_field("price").clean(price, plan)
    if plan.price == price:
        return plan
    before = str(plan.price) if plan.price is not None else None
    plan.price = price
    plan.review_state = "needs_boundary_review" if plan.raw_extraction.get("qualityWarning") else "needs_review"
    plan.save(update_fields=["price", "review_state", "updated_at"])
    ReviewTask.objects.create(project=project, task_type="floor_plan", priority=1, resolution={
        "floorPlanId": str(plan.pk), "source": "manual_pricing", "submittedBy": actor.pk,
        "reviewState": "unreviewed", "before": {"price": before},
        "after": {"price": str(price) if price is not None else None},
    })
    AuditEvent.objects.create(
        actor=actor, action="layout.price_changed", entity_type="FloorPlan", entity_id=plan.pk,
        correlation_id=uuid4(), metadata={"projectId": str(project.pk), "previousPrice": before,
                                        "price": str(price) if price is not None else None, "currency": "INR"},
    )
    return plan
