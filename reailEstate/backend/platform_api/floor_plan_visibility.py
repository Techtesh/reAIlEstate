"""Active views hide records without destroying their evidence or shared assets."""

REMOVED_REVIEW_STATE = "removed_by_user"
HIDDEN_REVIEW_STATES = ("superseded_page_candidate", "replaced_by_user", REMOVED_REVIEW_STATE)


def visible_floor_plans(queryset):
    return queryset.exclude(review_state__in=HIDDEN_REVIEW_STATES)


def visible_plan_assets(queryset):
    """Hide removed-only images, but keep unlinked images and shared sources."""
    from .models import FloorPlan

    linked_plans = FloorPlan.objects.filter(asset_id__isnull=False)
    removed_assets = linked_plans.filter(review_state=REMOVED_REVIEW_STATE).values("asset_id")
    retained_assets = linked_plans.exclude(review_state=REMOVED_REVIEW_STATE).values("asset_id")
    removed_only = queryset.filter(pk__in=removed_assets).exclude(pk__in=retained_assets).values("pk")
    return queryset.exclude(pk__in=removed_only)


def project_gallery_assets(project, *, can_review=False):
    from django.db.models import Q
    from .models import FloorPlan

    assets = project.floor_plan_assets.filter(status="ready", document__scan_status="clean",
        document__project=project, media_type__in=["image/png", "image/jpeg", "image/webp"])
    plans = visible_floor_plans(FloorPlan.objects.filter(project=project, active=True))
    if not can_review:
        if project.status != "published":
            return assets.none()
        return assets.filter(floor_plans__in=plans.filter(review_state="approved")).distinct()
    linked = FloorPlan.objects.filter(project=project, asset_id__isnull=False).values("asset_id")
    return assets.filter(~Q(pk__in=linked) | Q(pk__in=plans.values("asset_id")))


def visible_plan_facts(queryset):
    from django.db.models import Q
    from .models import FloorPlan

    removed_ids = [str(pk) for pk in FloorPlan.objects.filter(
        review_state=REMOVED_REVIEW_STATE, project_id__in=queryset.values("project_id")
    ).values_list("pk", flat=True)]
    # JSON paths absent from ordinary business facts yield SQL NULL. Preserve
    # those explicitly; a plain exclude(path__in=...) is not its complement.
    return queryset.filter(
        Q(typed_value__floorPlanId__isnull=True) | ~Q(typed_value__floorPlanId__in=removed_ids)
    ) if removed_ids else queryset