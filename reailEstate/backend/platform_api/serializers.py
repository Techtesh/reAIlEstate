from django.utils import timezone
from rest_framework import serializers

from .models import (
    Amenity,
    ComparisonShare,
    Consent,
    EvidenceDocument,
    FactCandidate,
    FloorPlan,
    HomeConfiguration,
    IngestionRun,
    NotificationSubscription,
    Organization,
    OrganizationMembership,
    Project,
    ProjectAmenity,
    ProjectFact,
    ProjectMetric,
    RecommendationItem,
    RecommendationSnapshot,
    RequirementProfile,
    RequirementVersion,
    ReviewTask,
    Shortlist,
    ShortlistProject,
    TransactionRecordLink,
    VerificationSnapshot,
)
from .services import completion_for, normalize_maha_rera_id, validate_requirement_payload
from .feature_flags.serializers import FeatureFlagSerializer as FeatureFlagSerializer


class ConsentSerializer(serializers.ModelSerializer):
    policyVersion = serializers.CharField(source="policy_version")
    grantedAt = serializers.DateTimeField(source="granted_at")
    withdrawnAt = serializers.DateTimeField(source="withdrawn_at", required=False, allow_null=True)

    class Meta:
        model = Consent
        fields = ["id", "purpose", "policyVersion", "grantedAt", "withdrawnAt", "created_at"]
        read_only_fields = ["id", "created_at"]


class RequirementVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = RequirementVersion
        fields = ["id", "version", "payload", "schema_version", "status", "submitted_at", "created_at"]


class RequirementProfileSerializer(serializers.ModelSerializer):
    version = serializers.IntegerField(source="active_version.version", read_only=True)
    sections = serializers.JSONField(source="active_version.payload", read_only=True)
    completion = serializers.SerializerMethodField()

    class Meta:
        model = RequirementProfile
        fields = ["id", "status", "version", "sections", "completion", "created_at", "updated_at"]

    def get_completion(self, obj):
        return completion_for(obj.active_version.payload if obj.active_version else {})


class OrganizationMembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrganizationMembership
        fields = ["id", "user", "role", "status", "version"]


class OrganizationSerializer(serializers.ModelSerializer):
    legalName = serializers.CharField(source="legal_name")
    verificationState = serializers.CharField(source="verification_state", read_only=True)
    memberships = OrganizationMembershipSerializer(many=True, read_only=True)

    class Meta:
        model = Organization
        fields = ["id", "legalName", "status", "verificationState", "memberships", "created_at", "updated_at"]


class HomeConfigurationSerializer(serializers.ModelSerializer):
    class Meta:
        model = HomeConfiguration
        fields = ["id", "configuration_type", "carpet_area_min", "carpet_area_max", "bathrooms", "balconies", "parking_spaces", "price_min", "price_max", "currency", "room_dimensions", "fact_set_version"]


class AmenitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Amenity
        fields = ["id", "taxonomy_code", "display_name", "category"]


class ProjectAmenitySerializer(serializers.ModelSerializer):
    amenity = AmenitySerializer(read_only=True)

    class Meta:
        model = ProjectAmenity
        fields = ["id", "amenity", "availability_state", "distance_meters", "travel_minutes", "evidence_summary"]


class ProjectMetricSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectMetric
        fields = ["metric_code", "numeric_value", "unit", "confidence", "definition_version", "calculated_at", "valid_until"]


class VerificationSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = VerificationSnapshot
        fields = ["provider", "retrieved_at", "match_result", "discrepancies", "adapter_version"]


class TransactionLinkSerializer(serializers.ModelSerializer):
    transaction = serializers.SerializerMethodField()

    class Meta:
        model = TransactionRecordLink
        fields = ["confidence", "reason_codes", "review_state", "transaction"]

    def get_transaction(self, obj):
        record = obj.transaction_record
        return {
            "registrationDate": record.registration_date,
            "amount": record.amount,
            "currency": record.currency,
            "area": record.area,
            "areaUnit": record.area_unit,
            "unitType": record.unit_type,
            "source": record.provider,
            "retrievedAt": record.retrieved_at,
        }


class ProjectListSerializer(serializers.ModelSerializer):
    project_pic = serializers.SerializerMethodField()
    configurations = serializers.SerializerMethodField()
    amenities = serializers.SerializerMethodField()

    def to_representation(self, instance):
        if instance.status == "published":
            return super().to_representation(instance)
        from django.db.models import Q
        from .models import ReviewTask

        reference = ReviewTask.objects.filter(project=instance, task_type="external_sources").filter(
            Q(resolution__manualLocation__isnull=False) | Q(resolution__googlePlaceId__isnull=False)
        ).order_by("-created_at", "-pk").first()
        accepted_location = bool(reference and reference.state == "resolved" and reference.resolution.get("reviewState") == "accepted")
        return {
            "id": str(instance.pk), "submitted_name": instance.submitted_name, "locality": instance.locality,
            "project_pic": self.get_project_pic(instance),
            "status": instance.status, "maha_rera_id": "", "verification_status": "unavailable",
            "verification_checked_at": None, "updated_at": instance.updated_at.isoformat(),
            "latitude": str(instance.latitude) if accepted_location and instance.latitude is not None else None,
            "longitude": str(instance.longitude) if accepted_location and instance.longitude is not None else None,
            "configurations": [], "amenities": [],
        }

    class Meta:
        model = Project
        fields = ["id", "submitted_name", "project_pic", "maha_rera_id", "locality", "status", "verification_status", "verification_checked_at", "latitude", "longitude", "configurations", "amenities", "updated_at"]

    def get_project_pic(self, obj):
        from django.conf import settings
        from .project_pictures import first_picture_document

        available = bool(obj.project_pic) or (
            settings.PROJECT_PUBLIC_PREVIEW_ENABLED and first_picture_document(obj) is not None)
        return f"/api/v1/projects/{obj.pk}/picture?v={obj.updated_at.timestamp()}" if available else None

    def get_configurations(self, obj):
        items = HomeConfiguration.objects.filter(building__project=obj, fact_set_version__lte=obj.published_fact_set_version)
        return HomeConfigurationSerializer(items, many=True).data

    def get_amenities(self, obj):
        return list(obj.project_amenities.values_list("amenity__taxonomy_code", flat=True))


class ProjectDetailSerializer(ProjectListSerializer):
    facts = serializers.SerializerMethodField()
    project_amenities = ProjectAmenitySerializer(many=True, read_only=True)
    metrics = ProjectMetricSerializer(many=True, read_only=True)
    verification = serializers.SerializerMethodField()
    transactions = serializers.SerializerMethodField()

    class Meta(ProjectListSerializer.Meta):
        fields = ProjectListSerializer.Meta.fields + ["address", "published_fact_set_version", "facts", "project_amenities", "metrics", "verification", "transactions"]

    def get_facts(self, obj):
        from .floor_plan_visibility import visible_plan_facts

        return [
            {
                "code": fact.field_code,
                "value": fact.typed_value,
                "unit": fact.unit,
                "sourceClass": fact.source_class,
                "reviewedAt": fact.reviewed_at,
                "factSetVersion": fact.fact_set_version,
                "evidence": {
                    "documentId": str(fact.selected_candidate.document_id),
                    "pageNumber": fact.selected_candidate.page_number,
                    "boundingRegion": fact.selected_candidate.bounding_region,
                    "confidence": fact.selected_candidate.confidence,
                    "extractorVersion": fact.selected_candidate.extractor_version,
                } if fact.selected_candidate_id and fact.selected_candidate.document_id else None,
            }
            for fact in visible_plan_facts(obj.facts.filter(
                fact_set_version=obj.published_fact_set_version, status="published"
            )).select_related("selected_candidate")
        ]

    def get_verification(self, obj):
        item = obj.verification_snapshots.order_by("-retrieved_at").first()
        return VerificationSnapshotSerializer(item).data if item else None

    def get_transactions(self, obj):
        links = obj.transaction_links.filter(review_state__in=["approved", "auto_approved"]).select_related("transaction_record")
        return TransactionLinkSerializer(links, many=True).data


class ProjectPreviewSerializer(ProjectDetailSerializer):
    class Meta(ProjectDetailSerializer.Meta):
        fields = [field for field in ProjectDetailSerializer.Meta.fields if field != "address"]

    def to_representation(self, instance):
        return serializers.ModelSerializer.to_representation(self, instance)

    def get_configurations(self, obj):
        return HomeConfigurationSerializer(HomeConfiguration.objects.filter(building__project=obj), many=True).data

    def get_facts(self, obj):
        from .floor_plan_visibility import visible_plan_facts

        return [{"code": fact.field_code, "value": fact.typed_value, "unit": fact.unit,
                 "sourceClass": fact.source_class, "reviewedAt": fact.reviewed_at,
                 "status": fact.status, "factSetVersion": fact.fact_set_version}
                for fact in visible_plan_facts(obj.facts.exclude(status__in=["rejected", "superseded", "removed"]))]


class ProjectCreateSerializer(serializers.ModelSerializer):
    mahaReraId = serializers.CharField(source="maha_rera_id")
    submittedName = serializers.CharField(source="submitted_name")
    processingAttested = serializers.BooleanField(source="processing_attested", required=False)

    class Meta:
        model = Project
        fields = ["id", "mahaReraId", "submittedName", "address", "processingAttested", "status", "created_at"]
        read_only_fields = ["id", "status", "created_at"]

    def validate_mahaReraId(self, value):
        return normalize_maha_rera_id(value)


class EvidenceDocumentSerializer(serializers.ModelSerializer):
    storageBucket = serializers.SerializerMethodField()

    def get_storageBucket(self, obj):
        if obj.object_key.startswith("user/media/completed/"):
            return "completed"
        if obj.object_key.startswith("user/media/todo/"):
            return "todo"
        return "legacy"

    class Meta:
        model = EvidenceDocument
        fields = ["id", "original_name", "declared_media_type", "detected_media_type", "byte_length", "sha256", "scan_status", "status", "created_at", "retention_until", "error_category", "storageBucket"]


class IngestionRunSerializer(serializers.ModelSerializer):
    documentId = serializers.UUIDField(source="document_id", read_only=True)
    agentWorkflows = serializers.SerializerMethodField()
    summary = serializers.SerializerMethodField()

    def get_summary(self, obj):
        result = dict(obj.summary)
        if "outputs" in result:
            result["outputs"] = {name: {"uri": item["uri"], "mediaType": item["mediaType"]}
                                 for name, item in result["outputs"].items()}
        return result

    class Meta:
        model = IngestionRun
        fields = ["id", "documentId", "status", "pipeline_version", "extraction_provider", "embedding_provider", "started_at", "completed_at", "summary", "error_code", "agentWorkflows", "created_at"]

    def get_agentWorkflows(self, obj):
        return [
            {
                "id": str(workflow.id),
                "type": workflow.workflow_type,
                "version": workflow.workflow_version,
                "status": workflow.status,
                "currentStep": workflow.current_step,
                "confidence": workflow.confidence,
                "result": workflow.result,
                "errorCode": workflow.error_code,
                "steps": [
                    {
                        "ordinal": step.ordinal,
                        "name": step.name,
                        "status": step.status,
                        "confidence": step.confidence,
                        "output": step.output,
                        "errorCode": step.error_code,
                    }
                    for step in workflow.steps.all()
                ],
            }
            for workflow in obj.agent_workflow_runs.prefetch_related("steps").all()
        ]


class FloorPlanSerializer(serializers.ModelSerializer):
    documentId = serializers.UUIDField(source="document_id", read_only=True)
    workflowRunId = serializers.UUIDField(source="workflow_run_id", read_only=True)
    # The camel-case renderer retains the existing public planName contract.
    plan_name = serializers.CharField(source="display_title", read_only=True)
    displayName = serializers.CharField(source="display_name", read_only=True)
    displayTitle = serializers.CharField(source="display_title", read_only=True)
    evidenceReference = serializers.SerializerMethodField()
    crop = serializers.SerializerMethodField()
    sourceKind = serializers.SerializerMethodField()
    isPreferredSource = serializers.SerializerMethodField()

    class Meta:
        model = FloorPlan
        fields = [
            "id", "documentId", "workflowRunId", "page_number", "plan_scope", "plan_name",
            "configuration_type", "carpet_area_sqft", "room_count", "bathroom_count",
            "balcony_count", "orientation", "dimensions", "features", "confidence", "review_state",
            "extractor_version", "evidenceReference", "crop", "created_at", "updated_at",
            "sourceKind", "isPreferredSource", "displayName", "displayTitle",
        ]

    def get_evidenceReference(self, obj):
        return f"/api/v1/media/{obj.document_id}/pages/{obj.page_number or 1}"

    def get_crop(self, obj):
        if not obj.asset_id:
            return None
        return {
            "id": str(obj.asset_id),
            "mediaType": obj.asset.media_type,
            "pixelWidth": obj.asset.pixel_width,
            "pixelHeight": obj.asset.pixel_height,
            "sourceBounds": obj.asset.source_bounds,
            "detectionConfidence": obj.asset.detection_confidence,
            "contentUrl": f"/api/v1/media/floor-plan-assets/{obj.asset_id}",
        }

    def get_sourceKind(self, obj):
        from .floor_plan_rules import plan_source_kind
        return plan_source_kind(obj)

    def get_isPreferredSource(self, obj):
        return obj.plan_scope == "flat" and self.get_sourceKind(obj) == "precut_source"


class FactCandidateSerializer(serializers.ModelSerializer):
    evidence_reference = serializers.SerializerMethodField()

    class Meta:
        model = FactCandidate
        fields = ["id", "field_code", "typed_value", "unit", "page_number", "bounding_region", "source_class", "confidence", "extractor_version", "review_state", "evidence_reference"]

    def get_evidence_reference(self, obj):
        if not obj.document_id or not obj.page_number:
            return None
        return f"/api/v1/media/{obj.document_id}/pages/{obj.page_number}"


class ReviewTaskSerializer(serializers.ModelSerializer):
    candidate = FactCandidateSerializer(read_only=True)
    candidates = serializers.SerializerMethodField()

    class Meta:
        model = ReviewTask
        fields = ["id", "project", "task_type", "priority", "state", "assigned_to", "version", "candidate", "candidates", "resolution", "created_at", "completed_at"]

    def get_candidates(self, obj):
        if obj.task_type == "layout_quality":
            return []
        candidates = FactCandidate.objects.filter(project=obj.project)
        if obj.candidate_id:
            candidates = candidates.filter(field_code=obj.candidate.field_code)
        return FactCandidateSerializer(candidates.order_by("-confidence"), many=True).data


class RecommendationItemSerializer(serializers.ModelSerializer):
    project_id = serializers.UUIDField(source="project.id")
    freshness = serializers.SerializerMethodField()

    class Meta:
        model = RecommendationItem
        fields = ["project_id", "rank", "score", "confidence", "components", "reason_codes", "freshness"]

    def get_freshness(self, obj):
        return {"projectFactsAsOf": obj.project.updated_at}


class RecommendationSnapshotSerializer(serializers.ModelSerializer):
    profile_version = serializers.IntegerField(source="requirement_version.version")
    items = serializers.SerializerMethodField()
    status = serializers.CharField(source="state")
    generated_at = serializers.DateTimeField()

    def get_items(self, obj):
        return RecommendationItemSerializer(
            obj.items.filter(project__status="published").select_related("project").order_by("rank"), many=True
        ).data

    class Meta:
        model = RecommendationSnapshot
        fields = ["id", "status", "profile_version", "algorithm_version", "generated_at", "expires_at", "items", "excluded_summary"]


class ShortlistProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShortlistProject
        fields = ["project", "note", "created_at"]


class ShortlistSerializer(serializers.ModelSerializer):
    entries = serializers.SerializerMethodField()

    class Meta:
        model = Shortlist
        fields = ["id", "name", "entries", "created_at", "updated_at"]

    def get_entries(self, obj):
        return ShortlistProjectSerializer(obj.shortlistproject_set.all(), many=True).data


class ComparisonShareSerializer(serializers.ModelSerializer):
    projectIds = serializers.JSONField(source="project_ids")
    expiresAt = serializers.DateTimeField(source="expires_at")

    class Meta:
        model = ComparisonShare
        fields = ["id", "projectIds", "expiresAt", "revoked_at", "created_at"]
        read_only_fields = ["id", "revoked_at", "created_at"]

    def validate_projectIds(self, value):
        if not isinstance(value, list) or not 2 <= len(set(value)) <= 4:
            raise serializers.ValidationError("Select between 2 and 4 unique projects.", code="invalid_project_count")
        return list(dict.fromkeys(value))

    def validate_expiresAt(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError("Expiry must be in the future.", code="invalid_expiry")
        return value


class NotificationSubscriptionSerializer(serializers.ModelSerializer):
    eventType = serializers.CharField(source="event_type")

    class Meta:
        model = NotificationSubscription
        fields = ["id", "eventType", "channel", "frequency", "enabled", "last_sent_at", "created_at", "updated_at"]
        read_only_fields = ["id", "last_sent_at", "created_at", "updated_at"]


