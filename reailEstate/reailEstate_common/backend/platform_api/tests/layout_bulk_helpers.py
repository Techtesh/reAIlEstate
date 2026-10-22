"""Offline fixture definitions shared by bulk-removal and health contract tests."""

import hashlib
from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from platform_api.models import (
    AgentWorkflowRun, EvidenceDocument, FloorPlan, FloorPlanAsset, IngestionRun,
    Operation, Organization, OutboxMessage, Project,
)


@pytest.fixture
def layout_records(settings, tmp_path, django_user_model):
    settings.WORKSPACE_DATA_ROOT = tmp_path
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.SECURE_SSL_REDIRECT = False
    actor = django_user_model.objects.create_superuser(username="bulk-editor", password="test-only")
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Boundary tests"),
                                     submitted_name="Acme canonical")

    def make_plan(*, owner=None, document=None, state="needs_review", raw=None, scope="flat"):
        owner = owner or project
        if document is None:
            object_key = f"sources/{uuid4()}.pdf"
            document = EvidenceDocument.objects.create(
                project=owner, uploaded_by=actor, original_name="immutable.pdf", object_key=object_key,
                declared_media_type="application/pdf", byte_length=10,
                sha256=hashlib.sha256(object_key.encode()).hexdigest(), scan_status="clean",
                retention_until=timezone.now() + timedelta(days=30),
            )
        run = IngestionRun.objects.create(project=owner, document=document, status="completed")
        workflow = AgentWorkflowRun.objects.create(project=owner, document=document, ingestion_run=run,
                                                   status="completed")
        asset = FloorPlanAsset.objects.create(
            project=owner, document=document, workflow_run=workflow, page_number=1, asset_type="unit_crop",
            object_key=f"images/{uuid4()}.png", byte_length=10, sha256="b" * 64, pixel_width=100, pixel_height=100,
            detector_version="test",
        )
        return FloorPlan.objects.create(
            project=owner, document=document, workflow_run=workflow, asset=asset, page_number=1,
            plan_scope=scope, plan_name=f"Layout {uuid4()}", review_state=state,
            raw_extraction=raw or {}, extractor_version="test",
        )

    return actor, project, make_plan


def queue_plan(plan, actor, event="floor_plan.crop_evaluation.requested", *, operation=None):
    operation = operation or Operation.objects.create(owner=actor, kind="evaluation")
    message = OutboxMessage.objects.create(event_type=event, correlation_id=uuid4(), payload={
        "floorPlanId": str(plan.pk), "projectId": str(plan.project_id), "documentId": str(plan.document_id),
        "assetId": str(plan.asset_id), "operationId": str(operation.pk),
    })
    return message, operation


def versions(*plans):
    return {str(plan.pk): plan.updated_at for plan in plans}