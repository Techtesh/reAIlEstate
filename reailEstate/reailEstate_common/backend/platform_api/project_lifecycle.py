"""Reviewed project consolidation and safe retirement operations."""

from uuid import UUID, uuid4

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import AuditEvent, OutboxMessage, Project, ProjectMerge


@transaction.atomic
def merge_projects(source_id, target_id, actor, reason_code, notes=""):
    source_id, target_id = UUID(str(source_id)), UUID(str(target_id))
    ordered = sorted({source_id, target_id}, key=str)
    locked = {project.id: project for project in Project.objects.select_for_update().filter(id__in=ordered)}
    if len(locked) != 2 or source_id == target_id:
        raise ValueError("INVALID_PROJECT_MERGE")
    source, target = locked[source_id], locked[target_id]
    if source.organization_id != target.organization_id:
        raise ValueError("CROSS_ORGANIZATION_MERGE_NOT_ALLOWED")
    if target.status in {"merged", "retired"} or ProjectMerge.objects.filter(source_project=target, state="applied").exists():
        raise ValueError("MERGE_TARGET_NOT_CANONICAL")
    if source.status in {"merged", "retired"} or ProjectMerge.objects.filter(source_project=source, state="applied").exists():
        raise ValueError("MERGE_SOURCE_NOT_ACTIVE")
    if ProjectMerge.objects.filter(target_project=source, state="applied").exists():
        raise ValueError("MERGE_SOURCE_HAS_EXISTING_ALIASES")
    pending = OutboxMessage.objects.filter(
        event_type__in=["document.processing.requested", "floor_plan.crop_evaluation.requested", "source.page_evaluation.requested", "floor_plan.units_evaluation.requested"],
        published_at__isnull=True, dead_lettered_at__isnull=True,
    ).filter(
        Q(payload__documentId__in=[str(value) for value in source.documents.values_list("id", flat=True)])
        | Q(payload__floorPlanId__in=[str(value) for value in source.floor_plans.values_list("id", flat=True)])
    )
    if pending.exists():
        raise ValueError("SOURCE_PROJECT_HAS_PENDING_WORK")
    merge, _ = ProjectMerge.objects.update_or_create(
        source_project=source,
        defaults={"target_project": target, "state": "applied", "reason_code": reason_code[:64],
                  "notes": notes[:1000], "merged_by": actor, "applied_at": timezone.now()},
    )
    source.status = "merged"
    source.address = source.address | {"mergedIntoProjectId": str(target.id)}
    source.save(update_fields=["status", "address", "updated_at"])
    AuditEvent.objects.create(
        actor=actor, action="project.merged", entity_type="Project", entity_id=source.id,
        reason_code=reason_code[:64], correlation_id=uuid4(),
        metadata={"targetProjectId": str(target.id), "projectMergeId": str(merge.id)},
    )
    return merge


def project_family_ids(project):
    return [project.id, *ProjectMerge.objects.filter(
        target_project=project, state="applied"
    ).values_list("source_project_id", flat=True)]


@transaction.atomic
def retire_project(project_id, actor, reason_code="ADMIN_RETIRE"):
    project = Project.objects.select_for_update().get(id=project_id)
    if project.status == "merged":
        raise ValueError("MERGED_PROJECT_CANNOT_BE_RETIRED")
    if project.status == "retired":
        return project
    document_ids = [str(value) for value in project.documents.values_list("id", flat=True)]
    plan_ids = [str(value) for value in project.floor_plans.values_list("id", flat=True)]
    OutboxMessage.objects.filter(
        event_type__in=["document.processing.requested", "floor_plan.crop_evaluation.requested", "source.page_evaluation.requested", "floor_plan.units_evaluation.requested"],
        published_at__isnull=True, dead_lettered_at__isnull=True,
    ).filter(
        Q(payload__documentId__in=document_ids) | Q(payload__floorPlanId__in=plan_ids)
    ).update(dead_lettered_at=timezone.now(), last_error_code="PROJECT_RETIRED")
    project.status = "retired"
    project.save(update_fields=["status", "updated_at"])
    AuditEvent.objects.create(
        actor=actor, action="project.retired", entity_type="Project", entity_id=project.id,
        reason_code=reason_code[:64], correlation_id=uuid4(), metadata={},
    )
    return project
