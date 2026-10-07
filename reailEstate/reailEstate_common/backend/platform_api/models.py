import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimestampedModel(UUIDModel):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Organization(TimestampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        SUSPENDED = "suspended", "Suspended"

    class VerificationState(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    legal_name = models.CharField(max_length=255)
    confirm_name_changes = models.BooleanField(
        default=False, help_text="Require an extra confirmation when builders save floor-plan or layout names."
    )
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.ACTIVE)
    verification_state = models.CharField(max_length=32, choices=VerificationState.choices, default=VerificationState.PENDING)

    def __str__(self):
        return self.legal_name


class OrganizationRole(TimestampedModel):
    """A reusable access role within one organization."""

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="roles")
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=255, blank=True)
    base_role = models.CharField(max_length=32, choices=[
        ("owner", "Owner"), ("editor", "Editor"), ("viewer", "Viewer"),
    ])

    class Meta:
        constraints = [models.UniqueConstraint(
            models.functions.Lower("name"), "organization", name="unique_organization_role_name",
        )]
        ordering = ["name"]

    def __str__(self):
        return f"{self.organization}: {self.name}"


class OrganizationMembership(TimestampedModel):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        EDITOR = "editor", "Editor"
        VIEWER = "viewer", "Viewer"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INVITED = "invited", "Invited"
        INACTIVE = "inactive", "Inactive"
        SUSPENDED = "suspended", "Suspended"
        REVOKED = "revoked", "Revoked"

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="organization_memberships")
    role = models.CharField(max_length=32, choices=Role.choices)
    named_role = models.ForeignKey(OrganizationRole, null=True, blank=True, on_delete=models.PROTECT, related_name="memberships")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.ACTIVE)
    version = models.PositiveIntegerField(default=1)

    def clean(self):
        super().clean()
        if self.named_role_id and self.named_role.organization_id != self.organization_id:
            from django.core.exceptions import ValidationError
            raise ValidationError({"named_role": "Choose a role belonging to this organization."})

    @property
    def effective_role(self):
        return self.named_role.base_role if self.named_role_id else self.role

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "user"], name="unique_organization_member")]


class Consent(TimestampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="consents")
    purpose = models.CharField(max_length=64)
    policy_version = models.CharField(max_length=32)
    granted_at = models.DateTimeField()
    withdrawn_at = models.DateTimeField(null=True, blank=True)


class RequirementProfile(TimestampedModel):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="requirement_profiles")
    status = models.CharField(max_length=32, default="draft")
    active_version = models.ForeignKey("RequirementVersion", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")


class RequirementVersion(UUIDModel):
    profile = models.ForeignKey(RequirementProfile, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField()
    payload = models.JSONField(default=dict)
    schema_version = models.CharField(max_length=32, default="requirements-1.0")
    status = models.CharField(max_length=32, default="draft")
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["profile", "version"], name="unique_requirement_version")]
        ordering = ["-version"]


class FrequentPlace(TimestampedModel):
    requirement_version = models.ForeignKey(RequirementVersion, on_delete=models.CASCADE, related_name="frequent_places")
    provider_place_id = models.CharField(max_length=255)
    encrypted_coordinates = models.TextField(blank=True)
    display_locality = models.CharField(max_length=255)
    precision = models.CharField(max_length=16, choices=[("exact", "Exact"), ("locality", "Locality"), ("municipality", "Municipality")])
    frequency = models.PositiveSmallIntegerField(default=1)
    modes = models.JSONField(default=list)
    arrival_window = models.JSONField(default=dict)
    tolerance_minutes = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["requirement_version", "provider_place_id"], name="unique_place_per_requirement_version")]


class ProfileShare(TimestampedModel):
    profile = models.ForeignKey(RequirementProfile, on_delete=models.CASCADE, related_name="shares")
    token_hash = models.CharField(max_length=128, unique=True)
    permissions = models.CharField(max_length=16, default="read")
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)


def project_picture_path(instance, filename):
    return f"project-pictures/{instance.pk}/{uuid.uuid4().hex}.png"


class Project(TimestampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        VERIFYING = "verifying", "Verifying"
        INGESTION = "ingestion", "Ingestion"
        NEEDS_REVIEW = "needs_review", "Needs review"
        PUBLISHED = "published", "Published"
        RETIRED = "retired", "Retired"
        MERGED = "merged", "Merged"

    class VerificationStatus(models.TextChoices):
        UNAVAILABLE = "unavailable", "Unavailable"
        VERIFIED = "verified", "Verified"
        UNVERIFIED = "unverified", "Unverified"
        NOT_FOUND = "not_found", "Not found"
        MISMATCH = "mismatch", "Mismatch"

    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="projects")
    maha_rera_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    submitted_name = models.CharField(max_length=255)
    project_pic = models.ImageField("Project pic", upload_to=project_picture_path, blank=True,
                                    help_text="Main image shown on buyer listing cards.")
    address = models.JSONField(default=dict)
    locality = models.CharField(max_length=128, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
        help_text="Accepted project location. Apply a reviewed pin in External sources to populate these coordinates.")
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    status = models.CharField(max_length=32, default=Status.DRAFT, choices=Status.choices)
    published_fact_set_version = models.PositiveIntegerField(default=0)
    processing_attested = models.BooleanField(default=False)
    verification_status = models.CharField(max_length=32, default=VerificationStatus.UNAVAILABLE,
        choices=VerificationStatus.choices)
    verification_checked_at = models.DateTimeField(null=True, blank=True)
    transit_station_choices = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.submitted_name


class StationCandidate(TimestampedModel):
    class Kind(models.TextChoices):
        METRO = "metro", "Metro"
        RAILWAY = "railway", "Railway"

    name = models.CharField(max_length=255)
    line = models.CharField(max_length=128, blank=True)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.METRO)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)])
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)])
    sequence = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    needs_review = models.BooleanField(default=True,
        help_text="Clear only after choosing valid coordinates. Service status remains unverified.")
    issue = models.TextField(blank=True)
    coordinate_options = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ("kind", "line", "sequence", "name")
        constraints = [
            models.UniqueConstraint(fields=("kind", "name", "line"), name="unique_station_candidate"),
            models.CheckConstraint(condition=models.Q(latitude__isnull=True) | models.Q(latitude__gte=-90, latitude__lte=90),
                                   name="station_latitude_range"),
            models.CheckConstraint(condition=models.Q(longitude__isnull=True) | models.Q(longitude__gte=-180, longitude__lte=180),
                                   name="station_longitude_range"),
            models.CheckConstraint(condition=models.Q(needs_review=True) | models.Q(latitude__isnull=False, longitude__isnull=False),
                                   name="station_ready_coordinates"),
        ]

    def __str__(self):
        return f"{self.name} ({self.line or self.get_kind_display()})"


class Building(TimestampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="buildings")
    name = models.CharField(max_length=128)
    status = models.CharField(max_length=32, default="active")


class HomeConfiguration(TimestampedModel):
    building = models.ForeignKey(Building, on_delete=models.CASCADE, related_name="configurations")
    configuration_type = models.CharField(max_length=32)
    carpet_area_min = models.DecimalField(max_digits=10, decimal_places=2)
    carpet_area_max = models.DecimalField(max_digits=10, decimal_places=2)
    bathrooms = models.PositiveSmallIntegerField(default=1)
    balconies = models.PositiveSmallIntegerField(default=0)
    parking_spaces = models.PositiveSmallIntegerField(default=0)
    price_min = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    price_max = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="INR")
    room_dimensions = models.JSONField(default=dict)
    fact_set_version = models.PositiveIntegerField(default=1)


class Amenity(TimestampedModel):
    taxonomy_code = models.CharField(max_length=64, unique=True)
    display_name = models.CharField(max_length=128)
    category = models.CharField(max_length=64)


class ProjectAmenity(TimestampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="project_amenities")
    amenity = models.ForeignKey(Amenity, on_delete=models.PROTECT, related_name="projects")
    availability_state = models.CharField(max_length=32, default="available")
    distance_meters = models.PositiveIntegerField(null=True, blank=True)
    travel_minutes = models.PositiveIntegerField(null=True, blank=True)
    evidence_summary = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "amenity"], name="unique_project_amenity")]


class EvidenceDocument(TimestampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="documents")
    object_key = models.CharField(max_length=255, unique=True)
    original_name = models.CharField(max_length=255)
    declared_media_type = models.CharField(max_length=128)
    detected_media_type = models.CharField(max_length=128, blank=True)
    byte_length = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    scan_status = models.CharField(max_length=32, default="pending")
    status = models.CharField(max_length=32, default="uploaded")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="uploaded_documents")
    retention_until = models.DateTimeField()
    error_category = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "sha256"], name="unique_project_document_hash")]
        indexes = [models.Index(fields=["status", "error_category"], name="document_retry_status_idx")]


class DocumentLayoutPage(TimestampedModel):
    document = models.ForeignKey(EvidenceDocument, on_delete=models.CASCADE, related_name="layout_page_decisions")
    page_number = models.PositiveIntegerField()
    state = models.CharField(
        max_length=24,
        choices=[("layout", "Process as layout"), ("excluded", "Do not process as layout")],
        default="layout",
    )
    source = models.CharField(
        max_length=24,
        choices=[("admin", "Administrator"), ("user_flag", "User flag"), ("system", "System")],
        default="admin",
    )
    reason_code = models.CharField(max_length=64, blank=True)
    notes = models.CharField(max_length=1000, blank=True)
    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="layout_page_decisions",
    )

    class Meta:
        ordering = ["page_number"]
        constraints = [models.UniqueConstraint(fields=["document", "page_number"], name="unique_document_layout_page")]
        indexes = [models.Index(fields=["document", "state"], name="layout_page_state_idx")]


class ExtractionJob(TimestampedModel):
    document = models.ForeignKey(EvidenceDocument, on_delete=models.CASCADE, related_name="extraction_jobs")
    extractor_version = models.CharField(max_length=64)
    status = models.CharField(max_length=32, default="queued")
    attempt = models.PositiveSmallIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_category = models.CharField(max_length=64, blank=True)


class IngestionRun(TimestampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="ingestion_runs")
    document = models.ForeignKey(EvidenceDocument, on_delete=models.CASCADE, related_name="ingestion_runs")
    status = models.CharField(max_length=32, default="queued")
    pipeline_version = models.CharField(max_length=64, default="rag-ingestion-1.0")
    extraction_provider = models.CharField(max_length=64, default="local")
    embedding_provider = models.CharField(max_length=64, default="local-hash-1.0")
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    summary = models.JSONField(default=dict)
    error_code = models.CharField(max_length=64, blank=True)

    class Meta:
        indexes = [models.Index(fields=["document", "status"], name="ingestion_document_status_idx")]


class KnowledgeChunk(TimestampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="knowledge_chunks")
    document = models.ForeignKey(EvidenceDocument, on_delete=models.CASCADE, related_name="knowledge_chunks")
    ingestion_run = models.ForeignKey(IngestionRun, on_delete=models.CASCADE, related_name="chunks")
    ordinal = models.PositiveIntegerField()
    page_number = models.PositiveIntegerField(null=True, blank=True)
    content = models.TextField()
    content_hash = models.CharField(max_length=64)
    token_count = models.PositiveIntegerField()
    embedding = models.JSONField(default=dict)
    metadata = models.JSONField(default=dict)

    class Meta:
        ordering = ["ordinal"]
        constraints = [models.UniqueConstraint(fields=["ingestion_run", "ordinal"], name="unique_chunk_ordinal")]
        indexes = [models.Index(fields=["project", "content_hash"], name="chunk_project_hash_idx")]


class AgentWorkflowRun(TimestampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="agent_workflow_runs")
    document = models.ForeignKey(EvidenceDocument, on_delete=models.CASCADE, related_name="agent_workflow_runs")
    ingestion_run = models.ForeignKey(IngestionRun, on_delete=models.CASCADE, related_name="agent_workflow_runs")
    workflow_type = models.CharField(max_length=64, default="floor_plan_extraction")
    workflow_version = models.CharField(max_length=64, default="floor-plan-agent-1.0")
    status = models.CharField(max_length=32, default="queued")
    current_step = models.CharField(max_length=64, blank=True)
    confidence = models.FloatField(default=0, validators=[MinValueValidator(0), MaxValueValidator(1)])
    result = models.JSONField(default=dict)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True)


class AgentWorkflowStep(TimestampedModel):
    workflow_run = models.ForeignKey(AgentWorkflowRun, on_delete=models.CASCADE, related_name="steps")
    ordinal = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=64)
    status = models.CharField(max_length=32, default="pending")
    input_snapshot_hash = models.CharField(max_length=64)
    output = models.JSONField(default=dict)
    confidence = models.FloatField(default=0, validators=[MinValueValidator(0), MaxValueValidator(1)])
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["ordinal"]
        constraints = [models.UniqueConstraint(fields=["workflow_run", "ordinal"], name="unique_agent_step_ordinal")]


class FloorPlanAsset(TimestampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="floor_plan_assets")
    document = models.ForeignKey(EvidenceDocument, on_delete=models.PROTECT, related_name="floor_plan_assets")
    workflow_run = models.ForeignKey(AgentWorkflowRun, on_delete=models.PROTECT, related_name="floor_plan_assets")
    page_number = models.PositiveIntegerField(null=True, blank=True)
    asset_type = models.CharField(max_length=32, default="plan_crop")
    object_key = models.CharField(max_length=255, unique=True)
    media_type = models.CharField(max_length=64, default="image/png")
    byte_length = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    pixel_width = models.PositiveIntegerField()
    pixel_height = models.PositiveIntegerField()
    source_bounds = models.JSONField(default=dict)
    detector_version = models.CharField(max_length=64)
    detection_confidence = models.FloatField(default=0, validators=[MinValueValidator(0), MaxValueValidator(1)])
    status = models.CharField(max_length=32, default="ready")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workflow_run", "sha256"], name="unique_floor_plan_crop")]


class FloorPlan(TimestampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="floor_plans")
    document = models.ForeignKey(EvidenceDocument, on_delete=models.PROTECT, related_name="floor_plans")
    workflow_run = models.ForeignKey(AgentWorkflowRun, on_delete=models.PROTECT, related_name="floor_plans")
    asset = models.ForeignKey(FloorPlanAsset, null=True, blank=True, on_delete=models.PROTECT, related_name="floor_plans")
    page_number = models.PositiveIntegerField(null=True, blank=True)
    plan_scope = models.CharField(max_length=16, default="unknown", choices=[("flat", "Flat/unit"), ("floor", "Floor/tower"), ("unknown", "Unknown")])
    plan_name = models.CharField(max_length=255, blank=True)
    # Reviewer-owned presentation only; never part of extraction identity/defaults.
    display_name = models.CharField(max_length=160, blank=True)
    active = models.BooleanField(default=True)
    crop_remaining = models.BooleanField(default=False)
    configuration_type = models.CharField(max_length=32, blank=True)
    carpet_area_sqft = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price = models.DecimalField(
        "Total price (INR)", max_digits=16, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    room_count = models.PositiveSmallIntegerField(null=True, blank=True)
    bathroom_count = models.PositiveSmallIntegerField(null=True, blank=True)
    balcony_count = models.PositiveSmallIntegerField(null=True, blank=True)
    orientation = models.CharField(max_length=32, blank=True)
    dimensions = models.JSONField(default=list)
    features = models.JSONField(default=dict)
    raw_extraction = models.JSONField(default=dict)
    confidence = models.FloatField(default=0, validators=[MinValueValidator(0), MaxValueValidator(1)])
    review_state = models.CharField(max_length=32, default="needs_review")
    extractor_version = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["document", "page_number", "plan_name"], name="unique_floor_plan_source"),
            models.CheckConstraint(condition=models.Q(price__isnull=True) | models.Q(price__gt=0),
                                   name="floor_plan_price_positive"),
        ]

    @property
    def display_title(self):
        from .domain.layout_titles import layout_display_title

        return layout_display_title(self)

    @property
    def room_rectangle_sum_sqft(self):
        from .domain.room_dimensions import room_rectangle_sum_sqft

        return room_rectangle_sum_sqft(self.dimensions)


class LayoutCropDraft(TimestampedModel):
    """Temporary crop cart entry; never a published asset or current FloorPlan."""
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="layout_crop_drafts")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="layout_crop_drafts")
    source_plan = models.ForeignKey(FloorPlan, null=True, blank=True, on_delete=models.CASCADE, related_name="crop_drafts")
    document = models.ForeignKey(EvidenceDocument, null=True, blank=True, on_delete=models.CASCADE, related_name="layout_crop_drafts")
    page_number = models.PositiveIntegerField(null=True, blank=True)
    # Original document checksum for page drafts; source asset checksum for legacy plan drafts.
    source_sha256 = models.CharField(max_length=64)
    label = models.CharField(max_length=64)
    image = models.BinaryField(editable=False)
    sha256 = models.CharField(max_length=64)
    pixel_width = models.PositiveIntegerField()
    pixel_height = models.PositiveIntegerField()
    bounds = models.JSONField(default=dict)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["owner", "project"], name="crop_draft_owner_project_idx")]
        constraints = [models.CheckConstraint(
            condition=(
                models.Q(source_plan__isnull=False, document__isnull=True, page_number__isnull=True)
                | models.Q(source_plan__isnull=True, document__isnull=False,
                           page_number__isnull=False, page_number__gte=1)
            ),
            name="crop_draft_valid_source",
        )]


class FactCandidate(TimestampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="fact_candidates")
    document = models.ForeignKey(EvidenceDocument, null=True, blank=True, on_delete=models.PROTECT, related_name="fact_candidates")
    field_code = models.CharField(max_length=64)
    typed_value = models.JSONField()
    unit = models.CharField(max_length=32, blank=True)
    page_number = models.PositiveIntegerField(null=True, blank=True)
    bounding_region = models.JSONField(default=dict)
    source_class = models.CharField(max_length=32, default="developer")
    confidence = models.FloatField(validators=[MinValueValidator(0), MaxValueValidator(1)])
    extractor_version = models.CharField(max_length=64, blank=True)
    review_state = models.CharField(max_length=32, default="unreviewed")


class FactConflict(TimestampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="fact_conflicts")
    field_code = models.CharField(max_length=64)
    candidates = models.ManyToManyField(FactCandidate, related_name="conflicts")
    state = models.CharField(max_length=32, default="open")
    resolution = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "field_code"], condition=models.Q(state="open"), name="unique_open_fact_conflict")]


class ProjectFact(TimestampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="facts")
    field_code = models.CharField(max_length=64)
    typed_value = models.JSONField()
    unit = models.CharField(max_length=32, blank=True)
    source_class = models.CharField(max_length=32)
    selected_candidate = models.ForeignKey(FactCandidate, null=True, blank=True, on_delete=models.PROTECT, related_name="selected_facts")
    status = models.CharField(max_length=32, default="published")
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="reviewed_facts")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    fact_set_version = models.PositiveIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "fact_set_version", "field_code"], name="unique_fact_in_set")]


class ReviewTask(TimestampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="review_tasks")
    candidate = models.ForeignKey(FactCandidate, null=True, blank=True, on_delete=models.SET_NULL, related_name="review_tasks")
    task_type = models.CharField(max_length=32, default="conflict")
    priority = models.PositiveSmallIntegerField(default=3)
    state = models.CharField(max_length=32, default="open")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_review_tasks")
    version = models.PositiveIntegerField(default=1)
    resolution = models.JSONField(default=dict)
    completed_at = models.DateTimeField(null=True, blank=True)


class VerificationSnapshot(TimestampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="verification_snapshots")
    provider = models.CharField(max_length=64, default="maharera")
    provider_record_id = models.CharField(max_length=128)
    retrieved_at = models.DateTimeField()
    fields = models.JSONField(default=dict)
    snapshot_hash = models.CharField(max_length=64)
    match_result = models.CharField(max_length=32)
    discrepancies = models.JSONField(default=list)
    adapter_version = models.CharField(max_length=32)


class TransactionRecord(TimestampedModel):
    provider = models.CharField(max_length=64)
    provider_record_id_hash = models.CharField(max_length=64, unique=True)
    registration_date = models.DateField()
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    currency = models.CharField(max_length=3, default="INR")
    area = models.DecimalField(max_digits=12, decimal_places=2)
    area_unit = models.CharField(max_length=16, default="sqft")
    unit_type = models.CharField(max_length=32, blank=True)
    normalized_data = models.JSONField(default=dict)
    retrieved_at = models.DateTimeField()


class TransactionRecordLink(TimestampedModel):
    transaction_record = models.ForeignKey(TransactionRecord, on_delete=models.CASCADE, related_name="project_links")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="transaction_links")
    confidence = models.FloatField(validators=[MinValueValidator(0), MaxValueValidator(1)])
    reason_codes = models.JSONField(default=list)
    review_state = models.CharField(max_length=32, default="unreviewed")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["transaction_record", "project"], name="unique_transaction_project_link")]


class ProjectMetric(TimestampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="metrics")
    metric_code = models.CharField(max_length=64)
    numeric_value = models.FloatField()
    unit = models.CharField(max_length=32, blank=True)
    confidence = models.FloatField(default=1, validators=[MinValueValidator(0), MaxValueValidator(1)])
    definition_version = models.CharField(max_length=32)
    input_snapshot_hash = models.CharField(max_length=64)
    calculated_at = models.DateTimeField()
    valid_until = models.DateTimeField(null=True, blank=True)


class RecommendationSnapshot(TimestampedModel):
    requirement_version = models.ForeignKey(RequirementVersion, on_delete=models.PROTECT, related_name="recommendation_snapshots")
    algorithm_version = models.CharField(max_length=32, default="match-1.0")
    input_catalog_version = models.CharField(max_length=64)
    state = models.CharField(max_length=32, default="requested")
    generated_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    excluded_summary = models.JSONField(default=list)


class RecommendationItem(UUIDModel):
    snapshot = models.ForeignKey(RecommendationSnapshot, on_delete=models.CASCADE, related_name="items")
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="recommendation_items")
    rank = models.PositiveIntegerField()
    score = models.FloatField()
    confidence = models.FloatField(validators=[MinValueValidator(0), MaxValueValidator(1)])
    components = models.JSONField(default=list)
    reason_codes = models.JSONField(default=list)

    class Meta:
        ordering = ["rank"]
        constraints = [models.UniqueConstraint(fields=["snapshot", "project"], name="unique_recommended_project")]


class ProjectMerge(TimestampedModel):
    source_project = models.OneToOneField(Project, on_delete=models.PROTECT, related_name="merge_out")
    target_project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="merge_sources")
    state = models.CharField(
        max_length=16,
        choices=[("proposed", "Proposed"), ("applied", "Applied"), ("reverted", "Reverted")],
        default="applied",
    )
    reason_code = models.CharField(max_length=64)
    notes = models.CharField(max_length=1000, blank=True)
    merged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="project_merges",
    )
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=~models.Q(source_project=models.F("target_project")),
            name="project_merge_source_differs",
        )]
        indexes = [models.Index(fields=["target_project", "state"], name="project_merge_target_idx")]


class TravelEstimate(TimestampedModel):
    origin_key = models.CharField(max_length=128)
    destination_key = models.CharField(max_length=128)
    mode = models.CharField(max_length=32)
    departure_bucket = models.CharField(max_length=64)
    provider = models.CharField(max_length=64)
    duration_seconds = models.PositiveIntegerField()
    distance_meters = models.PositiveIntegerField()
    assumptions = models.JSONField(default=dict)
    retrieved_at = models.DateTimeField()
    expires_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(fields=["origin_key", "destination_key", "mode", "expires_at"], name="travel_route_expiry_idx"),
        ]
        constraints = [models.UniqueConstraint(fields=["origin_key", "destination_key", "mode", "departure_bucket", "provider"], name="unique_travel_estimate")]


class Shortlist(TimestampedModel):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shortlists")
    name = models.CharField(max_length=128, default="My shortlist")
    projects = models.ManyToManyField(Project, through="ShortlistProject", related_name="shortlists")


class ShortlistProject(TimestampedModel):
    shortlist = models.ForeignKey(Shortlist, on_delete=models.CASCADE)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    note = models.TextField(blank=True, max_length=4000)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["shortlist", "project"], name="unique_shortlist_project")]


class ComparisonShare(TimestampedModel):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="comparison_shares")
    project_ids = models.JSONField(default=list)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)


class NotificationSubscription(TimestampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_subscriptions")
    event_type = models.CharField(max_length=64)
    channel = models.CharField(max_length=16, choices=[("email", "Email"), ("in_app", "In-app")])
    frequency = models.CharField(max_length=16, default="daily")
    enabled = models.BooleanField(default=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)


class AuditEvent(UUIDModel):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="audit_events")
    action = models.CharField(max_length=64)
    entity_type = models.CharField(max_length=64)
    entity_id = models.UUIDField(null=True)
    reason_code = models.CharField(max_length=64, blank=True)
    correlation_id = models.UUIDField()
    occurred_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict)


class FeatureFlag(TimestampedModel):
    key = models.CharField(max_length=128)
    environment = models.CharField(max_length=32, default="development")
    enabled = models.BooleanField(default=False)
    targeting_rules = models.JSONField(default=dict)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["key", "environment"], name="unique_feature_flag")]


class Operation(TimestampedModel):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.CASCADE, related_name="operations")
    kind = models.CharField(max_length=64)
    state = models.CharField(max_length=32, default="pending")
    result = models.JSONField(default=dict)
    error_code = models.CharField(max_length=64, blank=True)


class QueueWorker(TimestampedModel):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL)
    pid = models.PositiveIntegerField(default=0)
    event_group = models.CharField(max_length=32, default="extraction")
    state = models.CharField(max_length=32, default="starting")
    heartbeat_at = models.DateTimeField(auto_now_add=True)
    message_id = models.UUIDField(null=True, blank=True)
    progress = models.JSONField(default=dict)


class OutboxMessage(UUIDModel):
    event_type = models.CharField(max_length=128)
    schema_version = models.CharField(max_length=32, default="1.0")
    payload = models.JSONField(default=dict)
    correlation_id = models.UUIDField()
    occurred_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    available_after = models.DateTimeField(null=True, blank=True)
    dead_lettered_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True)


class InboxMessage(UUIDModel):
    consumer = models.CharField(max_length=128)
    message_id = models.UUIDField()
    processed_at = models.DateTimeField(auto_now_add=True)
    result = models.CharField(max_length=32)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["consumer", "message_id"], name="unique_inbox_message")]


class DeletionRecord(TimestampedModel):
    operation = models.ForeignKey(Operation, on_delete=models.CASCADE, related_name="deletion_records")
    store = models.CharField(max_length=64)
    state = models.CharField(max_length=32, default="pending")
    completed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    error_code = models.CharField(max_length=64, blank=True)


class IdempotencyRecord(UUIDModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    key = models.CharField(max_length=128)
    scope = models.CharField(max_length=128)
    response_status = models.PositiveSmallIntegerField()
    response_body = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "key", "scope"], name="unique_idempotency_key")]
