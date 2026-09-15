"""Ingestion queue contracts; all scanner, RAG, and inference work is mocked."""

import hashlib
import re
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, call, sentinel
from uuid import uuid4

import pytest
from django.core.files.base import ContentFile
from platform_api.artifact_storage import storage as default_storage
from django.utils import timezone
from rest_framework.test import APIClient

from platform_api import floor_plan_vision, workers
from platform_api.models import (
    AgentWorkflowRun,
    AuditEvent,
    EvidenceDocument,
    ExtractionJob,
    IdempotencyRecord,
    InboxMessage,
    IngestionRun,
    Operation,
    Organization,
    OrganizationMembership,
    OutboxMessage,
    Project,
)


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def vision_profiles(settings, tmp_path, monkeypatch):
    settings.FLOOR_PLAN_EXTRACTION_MODE = "ai"
    settings.MEDIA_ROOT = tmp_path
    settings.SECURE_SSL_REDIRECT = False
    settings.ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": f"ingestion-queue-{uuid4()}",
        },
    }
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    settings.FLOOR_PLAN_VISION_PROVIDER = "ollama"
    settings.FLOOR_PLAN_VISION_MODEL = "vision-local"
    settings.FLOOR_PLAN_VISION_BASE_URL = "http://127.0.0.1:11434/v1"
    settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE = "local"
    settings.FLOOR_PLAN_VISION_PROFILES = {
        "local": {
            "label": "Local vision",
            "provider": "ollama",
            "model": "vision-local",
            "base_url": "http://127.0.0.1:11434/v1",
            "enabled": True,
        },
        "cloud": {
            "label": "Foundry vision",
            "provider": "azure_foundry",
            "model": "vision-cloud",
            "base_url": "https://sample.openai.azure.com/openai/v1",
            "api_key_env": "TEST_INGESTION_VISION_KEY",
            "enabled": True,
        },
        "disabled": {
            "provider": "ollama", "model": "disabled-model", "enabled": False,
        },
        "unconfigured": {"provider": "ollama", "model": ""},
    }
    monkeypatch.setenv("TEST_INGESTION_VISION_KEY", "test-secret-never-expose")
    return settings.FLOOR_PLAN_VISION_PROFILES


@pytest.fixture(autouse=True)
def pipeline_mocks(monkeypatch):
    """Fail closed on unexpected work, including deferred upload callbacks."""
    mocks = SimpleNamespace()
    for name in (
        "get_document_scanner", "get_floor_plan_vision_provider",
        "ingest_document", "extract_project_knowledge",
    ):
        mock = Mock(side_effect=AssertionError(f"Unexpected pipeline call: {name}"))
        monkeypatch.setattr(workers, name, mock)
        setattr(mocks, name, mock)
    for name in ("analyze", "_api_key", "_entra_token"):
        monkeypatch.setattr(
            floor_plan_vision.OpenAIFloorPlanVisionProvider,
            name,
            Mock(side_effect=AssertionError("Live vision providers are forbidden")),
        )
    return mocks


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_user(username="queue-owner")


@pytest.fixture
def project(owner):
    organization = Organization.objects.create(legal_name="Queue Builder")
    OrganizationMembership.objects.create(
        organization=organization, user=owner, role="owner", status="active",
    )
    return Project.objects.create(organization=organization, submitted_name="Queue Project")


@pytest.fixture
def client(owner):
    client = APIClient()
    client.force_authenticate(owner)
    return client


@pytest.fixture
def document_factory(project, owner):
    def create(*, target_project=None, status="uploaded", scan_status="pending"):
        content = f"# Project evidence\nDocument reference: {uuid4()}\n".encode()
        key = default_storage.save(f"quarantine/tests/{uuid4()}.md", ContentFile(content))
        return EvidenceDocument.objects.create(
            project=target_project or project,
            object_key=key,
            original_name="evidence.md",
            declared_media_type="text/markdown",
            byte_length=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            status=status,
            scan_status=scan_status,
            uploaded_by=owner,
            retention_until=timezone.now() + timedelta(days=7),
        )
    return create


def project_url(project, resource):
    return f"/api/v1/developer-projects/{project.id}/{resource}"


def document_url(document, action):
    return project_url(document.project, f"documents/{document.id}:{action}")


def post_document(client, document, action="complete", body=None, key="complete-once"):
    headers = {} if key is None else {"HTTP_IDEMPOTENCY_KEY": key}
    return client.post(document_url(document, action), body or {}, format="json", **headers)


def mutation_snapshot():
    """Compare rows as well as counts so rejected requests cannot silently update state."""
    return {
        model.__name__: list(model.objects.order_by("pk").values())
        for model in (
            EvidenceDocument, ExtractionJob, OutboxMessage, InboxMessage,
            Operation, IdempotencyRecord, IngestionRun, AgentWorkflowRun, AuditEvent,
        )
    }


def assert_no_pipeline_calls(mocks):
    for mock in vars(mocks).values():
        mock.assert_not_called()
    assert not IngestionRun.objects.exists()
    assert not AgentWorkflowRun.objects.exists()


def assert_selection(selection, profile_id):
    provider, model = {
        "local": ("ollama", "vision-local"),
        "cloud": ("azure_foundry", "vision-cloud"),
    }[profile_id]
    assert set(selection) == {"profileId", "provider", "model", "configurationHash"}
    assert selection["profileId"] == profile_id
    assert selection["provider"] == provider
    assert selection["model"] == model
    assert re.fullmatch(r"[0-9a-f]{64}", selection["configurationHash"])
    assert selection == floor_plan_vision.resolve_vision_selection(profile_id)


def assert_validation(response, field, code):
    assert response.status_code == 400, response.content
    assert response.json()["errors"][field] == [{"code": code}]


def test_vision_profiles_list_only_enabled_safe_server_metadata(client, project, pipeline_mocks):
    response = client.get(project_url(project, "vision-profiles"))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"defaultProfileId", "profiles"}
    assert body["defaultProfileId"] == "local"
    assert sorted(body["profiles"], key=lambda item: item["id"]) == [
        {"id": "cloud", "label": "Foundry vision", "provider": "azure_foundry",
         "model": "vision-cloud", "hosting": "cloud"},
        {"id": "local", "label": "Local vision", "provider": "ollama",
         "model": "vision-local", "hosting": "local"},
    ]
    for private_value in (
        "base_url", "api_key_env", "configurationHash", "127.0.0.1",
        "sample.openai.azure.com", "TEST_INGESTION_VISION_KEY", "test-secret-never-expose",
    ):
        assert private_value not in response.content.decode()
    assert_no_pipeline_calls(pipeline_mocks)


@pytest.mark.parametrize("profile_id", [None, "local", "cloud"])
def test_completion_pins_selection_without_ingestion(
    client, project, document_factory, profile_id, pipeline_mocks,
    django_capture_on_commit_callbacks,
):
    document = document_factory()
    body = {} if profile_id is None else {"visionProfileId": profile_id}

    with django_capture_on_commit_callbacks(execute=True):
        response = post_document(client, document, body=body)

    assert response.status_code == 202
    result = response.json()
    assert_selection(result["visionSelection"], profile_id or "local")
    assert result["queueStatus"] == "pending"
    document.refresh_from_db()
    assert document.status == "quarantined"
    assert document.scan_status == "pending"
    job = ExtractionJob.objects.get(document=document)
    assert job.status == "awaiting_scan"
    assert job.attempt == 0
    message = OutboxMessage.objects.get()
    assert message.event_type == "document.processing.requested"
    assert message.payload == {
        "documentId": str(document.id), "visionSelection": result["visionSelection"],
    }
    assert message.published_at is None
    assert message.attempts == 0
    operation = Operation.objects.get(id=result["operationId"])
    assert operation.owner_id == document.uploaded_by_id
    assert operation.kind == "document_processing"
    assert operation.state == "pending"
    assert operation.result == {"documentId": str(document.id)}
    assert result["statusUrl"] == f"/api/v1/operations/{operation.id}"
    queue = client.get(project_url(project, "ingestion-queue"))
    assert queue.status_code == 200
    assert queue.json()["pending"] == 1
    assert queue.json()["items"][0]["visionSelection"] == result["visionSelection"]
    assert_no_pipeline_calls(pipeline_mocks)


def test_content_upload_does_not_enqueue_or_infer(
    client, project, pipeline_mocks, django_capture_on_commit_callbacks,
):
    content = b"# Project information\nName: Queue Project\n"
    with django_capture_on_commit_callbacks(execute=True):
        intent = client.post(project_url(project, "documents:upload-intent"), {
            "filename": "evidence.md", "declaredMediaType": "text/markdown",
            "byteLength": len(content), "sha256": hashlib.sha256(content).hexdigest(),
        }, format="json")
        assert intent.status_code == 201
        response = client.put(
            intent.json()["uploadAction"], content, content_type="text/markdown",
            HTTP_CONTENT_DISPOSITION='attachment; filename="evidence.md"',
        )

    assert response.status_code == 204
    document = EvidenceDocument.objects.get(id=intent.json()["documentId"])
    assert document.status == "uploaded"
    with default_storage.open(document.object_key, "rb") as stored:
        assert stored.read() == content
    assert not OutboxMessage.objects.exists()
    assert not ExtractionJob.objects.exists()
    assert not Operation.objects.exists()
    assert_no_pipeline_calls(pipeline_mocks)


@pytest.mark.parametrize("action", ["complete", "reprocess"])
@pytest.mark.parametrize("profile_id", ["missing", "disabled", "unconfigured", "", " local ", None, 42, {}])
def test_invalid_profile_does_not_mutate_state(client, document_factory, action, profile_id, pipeline_mocks):
    document = document_factory(status="uploaded" if action == "complete" else "approved")
    before = mutation_snapshot()

    response = post_document(client, document, action, {
        "visionProfileId": profile_id, "reasonCode": "MODEL_CHANGED",
    })

    expected = "UNKNOWN_OR_UNAVAILABLE_PROFILE" if isinstance(profile_id, str) else "INVALID_PROFILE"
    assert_validation(response, "visionProfileId", expected)
    assert mutation_snapshot() == before
    assert_no_pipeline_calls(pipeline_mocks)


@pytest.mark.parametrize("action", ["complete", "reprocess"])
def test_completion_and_reprocess_require_idempotency_key(client, document_factory, action):
    document = document_factory(status="uploaded" if action == "complete" else "approved")
    before = mutation_snapshot()
    response = post_document(client, document, action, {
        "visionProfileId": "local", "reasonCode": "MODEL_CHANGED",
    }, key=None)
    assert_validation(response, "idempotencyKey", "REQUIRED")
    assert mutation_snapshot() == before


@pytest.mark.parametrize("action", ["complete", "reprocess"])
def test_repeated_idempotency_returns_same_operation_without_duplicates(
    client, document_factory, action, settings, pipeline_mocks,
):
    document = document_factory(status="uploaded" if action == "complete" else "approved")
    body = {"visionProfileId": "local", "reasonCode": "MODEL_CHANGED"}
    first = post_document(client, document, action, body)
    assert first.status_code == 202
    before = mutation_snapshot()
    settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE = "cloud"

    replay = post_document(client, document, action, body)

    assert replay.status_code == 202
    assert replay.json() == first.json()
    assert mutation_snapshot() == before
    assert OutboxMessage.objects.count() == 1
    assert ExtractionJob.objects.count() == 1
    assert Operation.objects.count() == 1
    assert IdempotencyRecord.objects.count() == 1
    assert_no_pipeline_calls(pipeline_mocks)


@pytest.mark.parametrize("role", ["owner", "editor"])
def test_owner_and_editor_can_list_profiles_and_queue_and_reprocess(
    client, project, owner, document_factory, role,
):
    OrganizationMembership.objects.filter(organization=project.organization, user=owner).update(role=role)
    assert client.get(project_url(project, "vision-profiles")).status_code == 200
    assert client.get(project_url(project, "ingestion-queue")).status_code == 200
    assert client.post(project_url(project, "ingestion-queue"), {"limit": 1}, format="json").status_code == 200
    document = document_factory(status="approved", scan_status="clean")
    response = post_document(client, document, "reprocess", {
        "visionProfileId": "cloud", "reasonCode": "MODEL_CHANGED",
    })
    assert response.status_code == 202
    operation = Operation.objects.get(id=response.json()["operationId"])
    assert operation.owner == owner
    assert operation.state == "pending"


@pytest.mark.parametrize("access", ["unrelated_owner", "viewer", "inactive_editor"])
@pytest.mark.parametrize("endpoint", ["profiles", "queue_get", "queue_post", "reprocess", "complete"])
def test_unauthorized_project_access_has_no_disclosure_or_mutations(
    project, document_factory, django_user_model, access, endpoint, pipeline_mocks,
):
    user = django_user_model.objects.create_user(username="restricted-user")
    organization = project.organization
    role, membership_status = "viewer", "active"
    if access == "unrelated_owner":
        organization = Organization.objects.create(legal_name="Unrelated Builder")
        Project.objects.create(organization=organization, submitted_name="Unrelated Project")
        role = "owner"
    elif access == "inactive_editor":
        role, membership_status = "editor", "inactive"
    OrganizationMembership.objects.create(
        organization=organization, user=user, role=role, status=membership_status,
    )
    client = APIClient()
    client.force_authenticate(user)
    document = document_factory(status="uploaded" if endpoint == "complete" else "approved")
    before = mutation_snapshot()

    if endpoint == "profiles":
        response = client.get(project_url(project, "vision-profiles"))
    elif endpoint == "queue_get":
        response = client.get(project_url(project, "ingestion-queue"))
    elif endpoint == "queue_post":
        response = client.post(project_url(project, "ingestion-queue"), {"limit": 1}, format="json")
    else:
        response = post_document(client, document, endpoint, {
            "visionProfileId": "cloud", "reasonCode": "MODEL_CHANGED",
        })

    assert response.status_code == 403
    assert response.json()["code"] == "ACCESS_DENIED"
    assert mutation_snapshot() == before
    for private_value in ("vision-cloud", "vision-local", "sample.openai.azure.com", "visionSelection"):
        assert private_value not in response.content.decode()
    assert_no_pipeline_calls(pipeline_mocks)


def test_reprocess_rejects_document_under_wrong_project(client, project, document_factory):
    other_project = Project.objects.create(organization=project.organization, submitted_name="Other Project")
    document = document_factory(target_project=other_project, status="approved")
    before = mutation_snapshot()
    response = client.post(project_url(project, f"documents/{document.id}:reprocess"), {
        "visionProfileId": "cloud", "reasonCode": "MODEL_CHANGED",
    }, format="json", HTTP_IDEMPOTENCY_KEY="wrong-project")
    assert response.status_code == 404
    assert mutation_snapshot() == before


def test_reprocess_requires_reason_without_mutating(client, document_factory):
    document = document_factory(status="approved")
    before = mutation_snapshot()
    response = post_document(client, document, "reprocess", {"visionProfileId": "cloud"})
    assert_validation(response, "reasonCode", "REQUIRED")
    assert mutation_snapshot() == before


@pytest.mark.parametrize("delayed", [False, True])
def test_reprocess_rejects_active_outstanding_message(client, document_factory, delayed):
    document = document_factory(status="quarantined")
    message = workers.enqueue_document(document, uuid4())
    if delayed:
        message.available_after = timezone.now() + timedelta(hours=1)
        message.save(update_fields=["available_after"])
    before = mutation_snapshot()
    response = post_document(client, document, "reprocess", {
        "visionProfileId": "cloud", "reasonCode": "MODEL_CHANGED",
    }, key="new-reprocess-key")
    assert_validation(response, "document", "ALREADY_QUEUED")
    assert mutation_snapshot() == before


@pytest.mark.parametrize("old_message_state", ["published", "dead_lettered"])
def test_reprocess_can_choose_another_model_and_creates_operation(
    client, document_factory, old_message_state, pipeline_mocks,
):
    document = document_factory()
    first = post_document(client, document, body={"visionProfileId": "local"})
    assert first.status_code == 202
    old_message = OutboxMessage.objects.get()
    old_payload = old_message.payload
    field = "published_at" if old_message_state == "published" else "dead_lettered_at"
    setattr(old_message, field, timezone.now())
    old_message.save(update_fields=[field])
    document.status = "approved" if old_message_state == "published" else "extraction_failed"
    document.scan_status = "clean"
    document.error_category = "OLD_FAILURE"
    document.save()
    Operation.objects.filter(id=first.json()["operationId"]).update(state="completed")

    response = post_document(client, document, "reprocess", {
        "visionProfileId": "cloud", "reasonCode": "MODEL_CHANGED",
    }, key="reprocess-cloud")

    assert response.status_code == 202
    result = response.json()
    assert_selection(result["visionSelection"], "cloud")
    assert result["visionSelection"]["configurationHash"] != old_payload["visionSelection"]["configurationHash"]
    assert result["queueStatus"] == "pending"
    assert result["documentId"] == str(document.id)
    assert result["operationId"] != first.json()["operationId"]
    operation = Operation.objects.get(id=result["operationId"])
    assert operation.kind == "document_processing"
    assert operation.state == "pending"
    assert operation.owner_id == document.uploaded_by_id
    assert operation.result == {"documentId": str(document.id)}
    assert result["statusUrl"] == f"/api/v1/operations/{operation.id}"
    message = OutboxMessage.objects.exclude(id=old_message.id).get()
    assert message.payload == {"documentId": str(document.id), "visionSelection": result["visionSelection"]}
    assert message.published_at is None
    old_message.refresh_from_db()
    assert old_message.payload == old_payload
    assert OutboxMessage.objects.count() == 2
    assert ExtractionJob.objects.filter(document=document).count() == 2
    assert Operation.objects.count() == 2
    document.refresh_from_db()
    assert document.status == result["status"] == "extraction_failed"
    assert document.error_category == ""
    assert AuditEvent.objects.filter(action="document.reprocessing_requested", reason_code="MODEL_CHANGED").exists()
    assert_no_pipeline_calls(pipeline_mocks)


@pytest.mark.parametrize("limit", [0, -1, 21, True, 1.5, "2", None])
def test_project_queue_rejects_invalid_limit_without_work(client, project, document_factory, limit, pipeline_mocks):
    document = document_factory(status="quarantined")
    workers.enqueue_document(document, uuid4())
    before = mutation_snapshot()
    response = client.post(project_url(project, "ingestion-queue"), {"limit": limit}, format="json")
    assert_validation(response, "limit", "OUT_OF_RANGE")
    assert mutation_snapshot() == before
    assert_no_pipeline_calls(pipeline_mocks)


@pytest.mark.parametrize("limit", [None, 1, 20])
def test_project_queue_processes_synchronously_with_limit_and_project_scope(
    client, project, document_factory, monkeypatch, limit, pipeline_mocks,
):
    messages = [workers.enqueue_document(document_factory(status="quarantined"), uuid4()) for _ in range(21)]
    unrelated = Project.objects.create(
        organization=Organization.objects.create(legal_name="Other Queue Builder"),
        submitted_name="Other Queue Project",
    )
    excluded = [workers.enqueue_document(document_factory(target_project=unrelated, status="quarantined"), uuid4())]
    for field in ("available_after", "dead_lettered_at", "published_at"):
        message = workers.enqueue_document(document_factory(status="quarantined"), uuid4())
        setattr(message, field, timezone.now() + timedelta(hours=1))
        message.save(update_fields=[field])
        excluded.append(message)

    def finish(document_id, **kwargs):
        document = EvidenceDocument.objects.get(id=document_id)
        document.status = "approved"
        document.save(update_fields=["status"])
        return document

    process = Mock(side_effect=finish)
    monkeypatch.setattr(workers, "process_document", process)
    count = 10 if limit is None else limit
    body = {} if limit is None else {"limit": limit}
    response = client.post(project_url(project, "ingestion-queue"), body, format="json")

    assert response.status_code == 200
    result = response.json()
    assert result["requested"] == count
    assert result["results"] == {"processed": count, "retry": 0, "failed": 0}
    assert result["queue"]["pending"] == 21 - count
    assert result["queue"]["delayed"] == 1
    assert result["queue"]["deadLettered"] == 1
    expected = OutboxMessage.objects.filter(id__in=[item.id for item in messages]).order_by("occurred_at")
    expected_ids = [item.payload["documentId"] for item in expected[:count]]
    assert [entry.args[0] for entry in process.call_args_list] == expected_ids
    assert InboxMessage.objects.filter(consumer="platform-worker").count() == count
    assert EvidenceDocument.objects.filter(id__in=expected_ids, status="approved").count() == count
    assert OutboxMessage.objects.filter(id__in=[item.id for item in messages], published_at__isnull=False).count() == count
    for message in excluded:
        message.refresh_from_db()
        assert message.attempts == 0
        assert not InboxMessage.objects.filter(message_id=message.id).exists()
    assert_no_pipeline_calls(pipeline_mocks)


@pytest.mark.parametrize("profile_id,new_default", [("local", "cloud"), ("cloud", "local")])
def test_worker_uses_saved_selection_after_default_changes_and_deduplicates(
    document_factory, settings, pipeline_mocks, profile_id, new_default,
):
    # Exercise the worker independently of URL loading and request-side behavior.
    document = document_factory(status="quarantined")
    ExtractionJob.objects.create(document=document, extractor_version="extractor-1.0", status="awaiting_scan")
    operation = Operation.objects.create(
        owner=document.uploaded_by, kind="document_processing", state="pending",
        result={"documentId": str(document.id)},
    )
    message = workers.enqueue_document(
        document, uuid4(), vision_selection=floor_plan_vision.resolve_vision_selection(profile_id),
    )
    saved_selection = dict(message.payload["visionSelection"])
    settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE = new_default
    settings.FLOOR_PLAN_VISION_PROVIDER = "local_openai"
    settings.FLOOR_PLAN_VISION_MODEL = "changed-legacy-default"
    assert floor_plan_vision.resolve_vision_selection()["profileId"] == new_default

    scanner = Mock()
    scanner.scan.return_value = "clean"
    factory = pipeline_mocks.get_floor_plan_vision_provider
    factory.side_effect = None
    factory.return_value = sentinel.selected_provider

    def ingest(document, content, **kwargs):
        return IngestionRun.objects.create(project=document.project, document=document, status="completed")

    def extract(run, **kwargs):
        workflow = AgentWorkflowRun.objects.create(
            project=run.project, document=run.document, ingestion_run=run, status="completed",
        )
        return {"candidates": [], "floorPlanWorkflow": workflow}

    pipeline_mocks.ingest_document.side_effect = ingest
    pipeline_mocks.extract_project_knowledge.side_effect = extract
    with default_storage.open(document.object_key, "rb") as stored:
        content = stored.read()

    assert workers.process_outbox_message(message.id, scanner=scanner) == "processed"

    factory.assert_called_once_with(saved_selection)
    run = IngestionRun.objects.get(document=document)
    pipeline_mocks.extract_project_knowledge.assert_called_once_with(
        run, content=content, media_type="text/markdown", vision_provider=sentinel.selected_provider,
    )
    assert pipeline_mocks.ingest_document.call_count == 1
    scanner.scan.assert_called_once()
    pipeline_mocks.get_document_scanner.assert_not_called()
    document.refresh_from_db()
    assert document.status == "needs_review"
    message.refresh_from_db()
    assert message.payload["visionSelection"] == saved_selection
    assert message.published_at is not None
    assert message.attempts == 1
    operation.refresh_from_db()
    assert operation.state == "completed"
    assert operation.result["ingestionRunId"] == str(run.id)
    before = mutation_snapshot()

    assert workers.process_outbox_message(message.id, scanner=scanner) == "already_processed"

    assert mutation_snapshot() == before
    assert factory.call_args_list == [call(saved_selection)]
    assert pipeline_mocks.extract_project_knowledge.call_count == 1
    assert InboxMessage.objects.filter(consumer="platform-worker", message_id=message.id).count() == 1