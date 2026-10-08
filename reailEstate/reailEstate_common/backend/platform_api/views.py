import hashlib
import io
import re
import secrets
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.core.files.base import ContentFile
from .artifact_storage import storage as default_storage
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.http import FileResponse, Http404
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework import status, viewsets
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import FileUploadParser
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Amenity,
    AuditEvent,
    ComparisonShare,
    Consent,
    DocumentLayoutPage,
    EvidenceDocument,
    ExtractionJob,
    FactCandidate,
    FloorPlan,
    IdempotencyRecord,
    NotificationSubscription,
    Operation,
    Organization,
    OrganizationMembership,
    OutboxMessage,
    ProfileShare,
    Project,
    ProjectFact,
    RecommendationSnapshot,
    RequirementProfile,
    RequirementVersion,
    ReviewTask,
    Shortlist,
    ShortlistProject,
    VerificationSnapshot,
)
from .serializers import (
    ComparisonShareSerializer,
    ConsentSerializer,
    EvidenceDocumentSerializer,
    FloorPlanSerializer,
    IngestionRunSerializer,
    NotificationSubscriptionSerializer,
    OrganizationSerializer,
    ProjectCreateSerializer,
    ProjectDetailSerializer,
    ProjectListSerializer,
    ProjectPreviewSerializer,
    RecommendationSnapshotSerializer,
    RequirementProfileSerializer,
    ReviewTaskSerializer,
    ShortlistSerializer,
)
from .services import audit, compute_project_metrics, normalize_maha_rera_id, project_frequent_places, score_recommendations, validate_requirement_payload
from .travel import estimate_matrix
from .workers import (
    detect_media_type,
    document_queue_snapshot,
    enqueue_document,
    enqueue_recommendation,
    pending_document_message_ids,
    process_pending_outbox,
)
from .rag import retrieve
from .floor_plan_vision import list_floor_plan_vision_profiles, resolve_vision_selection
from .floor_plan_segmentation_service import (
    FlatSeparationFailed,
    available_flat_separation_ai_profiles,
    separate_floor_plan_units,
)
from .document_formats import FORMATS
from .layout_reanalysis import create_manual_layout_crops
from .layout_pages import validate_document_page
from .project_lifecycle import merge_projects
from .reanalysis import queue_project_reanalysis
from .floor_plan_visibility import project_gallery_assets, visible_floor_plans
from .feature_flags import is_enabled
from .jobs.events import DOCUMENT_EVENT
from .feature_flags.api import FeatureFlagAdminViewSet as FeatureFlagAdminViewSet

ALLOWED_UPLOAD_TYPES = FORMATS
MAX_UPLOAD_BYTES = settings.MAX_UPLOAD_BYTES


def _is_reviewer(user):
    return user.is_staff or user.groups.filter(name__in=["reviewer", "admin"]).exists()


def _project_can_edit(user, project):
    from .builder_access import builder_can_manage

    return bool(user and user.is_authenticated and user.is_active
                and (user.is_staff or builder_can_manage(user, project)))


def _is_project_member(user, project):
    return OrganizationMembership.objects.filter(
        organization=project.organization,
        user=user,
        status="active",
    ).exists()


def _require_active_project(project):
    if project.status in {"retired", "merged"} or Project.objects.filter(pk=project.pk, merge_out__state="applied").exists():
        raise ValidationError({"project": [{"code": "INACTIVE_PROJECT"}]})


def _owned_profile(user, profile_id):
    return get_object_or_404(RequirementProfile.objects.select_related("active_version"), id=profile_id, owner=user)


def _parse_etag(request):
    value = request.headers.get("If-Match", "").strip().strip('W/"')
    if not value.isdigit():
        raise ValidationError({"ifMatch": [{"code": "REQUIRED_VERSION"}]})
    return int(value)


def _etag_response(data, version, response_status=status.HTTP_200_OK):
    response = Response(data, status=response_status)
    response["ETag"] = f'W/"{version}"'
    response["Cache-Control"] = "no-store"
    return response


def _idempotent_response(request, scope):
    key = request.headers.get("Idempotency-Key", "")
    if not key:
        raise ValidationError({"idempotencyKey": [{"code": "REQUIRED"}]})
    existing = IdempotencyRecord.objects.filter(user=request.user, key=key[:128], scope=scope).first()
    return key[:128], existing


def _save_idempotency(user, key, scope, response_status, body):
    try:
        IdempotencyRecord.objects.create(user=user, key=key, scope=scope, response_status=response_status, response_body=body)
    except IntegrityError:
        pass


def _requested_vision_selection(data):
    if "visionProfileId" in data and not isinstance(data["visionProfileId"], str):
        raise ValidationError({"visionProfileId": [{"code": "INVALID_PROFILE"}]})
    try:
        return resolve_vision_selection(data.get("visionProfileId"))
    except ValueError:
        raise ValidationError({"visionProfileId": [{"code": "UNKNOWN_OR_UNAVAILABLE_PROFILE"}]}) from None


def _queue_document_processing(document, owner, correlation_id, selection, *, extractor_version, job_status):
    ExtractionJob.objects.create(
        document=document,
        extractor_version=extractor_version,
        status=job_status,
    )
    enqueue_document(document, correlation_id, vision_selection=selection)
    operation = Operation.objects.create(
        owner=owner,
        kind="document_processing",
        state="pending",
        result={"documentId": str(document.id)},
    )
    return {
        "operationId": str(operation.id),
        "statusUrl": f"/api/v1/operations/{operation.id}",
        "queueStatus": "pending",
        "visionSelection": selection,
    }


class BuyerCursorPagination(CursorPagination):
    ordering = ("-created_at", "id")
    # Demo Browse needs the complete map dataset. Fifty-row pages turn the
    # 1,590-pin fixture into 32 anonymous requests, exceeding its 30/min limit.
    page_size = 250


class ConsentViewSet(viewsets.ModelViewSet):
    serializer_class = ConsentSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = BuyerCursorPagination

    def get_queryset(self):
        return Consent.objects.filter(user=self.request.user).order_by("-granted_at")

    def perform_create(self, serializer):
        consent = serializer.save(user=self.request.user)
        audit(self.request, "consent.granted", consent)

    def perform_update(self, serializer):
        consent = serializer.save()
        audit(self.request, "consent.updated", consent)


@method_decorator(csrf_protect, name="dispatch")
class SessionLoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def login_allowed(self, user):
        return user is not None and user.is_active

    def get(self, request):
        get_token(request)
        response = Response({"status": "ready"})
        response["Cache-Control"] = "no-store"
        return response

    def post(self, request):
        username = str(request.data.get("username", ""))
        password = str(request.data.get("password", ""))
        user = authenticate(request, username=username, password=password)
        if not self.login_allowed(user):
            raise PermissionDenied("Invalid credentials.", code="invalid_credentials")
        login(request, user)
        audit(request, "session.login")
        return Response({"id": user.id, "username": user.get_username(), "email": user.email})


class SessionLogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        audit(request, "session.logout")
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CurrentUserView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({
            "id": request.user.id,
            "username": request.user.get_username(),
            "email": request.user.email,
            "roles": list(request.user.groups.values_list("name", flat=True)),
            "isStaff": request.user.is_staff,
        })


class RequirementProfileViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def list(self, request):
        profiles = RequirementProfile.objects.filter(owner=request.user).select_related("active_version").order_by("-updated_at")
        return Response(RequirementProfileSerializer(profiles, many=True).data)

    @transaction.atomic
    def create(self, request):
        profile = RequirementProfile.objects.create(owner=request.user)
        version = RequirementVersion.objects.create(profile=profile, version=1, payload={}, status="draft")
        profile.active_version = version
        profile.save(update_fields=["active_version", "updated_at"])
        audit(request, "requirement.created", profile)
        return _etag_response(RequirementProfileSerializer(profile).data, 1, status.HTTP_201_CREATED)

    def retrieve(self, request, pk=None):
        profile = _owned_profile(request.user, pk)
        return _etag_response(RequirementProfileSerializer(profile).data, profile.active_version.version)

    @transaction.atomic
    def partial_update(self, request, pk=None):
        profile = _owned_profile(request.user, pk)
        expected = _parse_etag(request)
        current = profile.active_version
        if profile.status != "draft" or current.status != "draft":
            raise ValidationError({"profile": [{"code": "IMMUTABLE_SUBMITTED_VERSION"}]})
        if current.version != expected:
            return Response({"code": "VERSION_CONFLICT", "latestVersion": current.version}, status=status.HTTP_409_CONFLICT)
        if not isinstance(request.data, dict):
            raise ValidationError({"sections": [{"code": "INVALID_OBJECT"}]})
        payload = current.payload.copy()
        for key, value in request.data.items():
            if key not in {"places", "home", "societyAmenities", "nearbyAmenities", "microMarket", "budget", "weights", "additionalNeeds"}:
                raise ValidationError({key: [{"code": "UNKNOWN_SECTION"}]})
            payload[key] = value
        validate_requirement_payload(payload)
        next_version = RequirementVersion.objects.create(profile=profile, version=current.version + 1, payload=payload, status="draft")
        project_frequent_places(next_version)
        profile.active_version = next_version
        profile.save(update_fields=["active_version", "updated_at"])
        audit(request, "requirement.updated", profile, metadata={"version": next_version.version, "sections": list(request.data.keys())})
        return _etag_response(RequirementProfileSerializer(profile).data, next_version.version)

    @transaction.atomic
    def destroy(self, request, pk=None):
        profile = _owned_profile(request.user, pk)
        profile.status = "deleted"
        profile.save(update_fields=["status", "updated_at"])
        profile.shares.update(revoked_at=timezone.now())
        audit(request, "requirement.deleted", profile)
        return Response(status=status.HTTP_204_NO_CONTENT)


class RequirementSubmitView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, profile_id):
        key, existing = _idempotent_response(request, f"requirement-submit:{profile_id}")
        if existing:
            return Response(existing.response_body, status=existing.response_status)
        profile = _owned_profile(request.user, profile_id)
        expected = _parse_etag(request)
        version = profile.active_version
        if expected != version.version:
            return Response({"code": "VERSION_CONFLICT", "latestVersion": version.version}, status=status.HTTP_409_CONFLICT)
        validate_requirement_payload(version.payload, final=True)
        required_consents = Consent.objects.filter(user=request.user, purpose="saved_profile", withdrawn_at__isnull=True)
        if not required_consents.exists():
            raise ValidationError({"consent": [{"code": "CONSENT_REQUIRED"}]})
        version.status = "submitted"
        version.submitted_at = timezone.now()
        version.save(update_fields=["status", "submitted_at"])
        profile.status = "submitted"
        profile.save(update_fields=["status", "updated_at"])
        snapshot = RecommendationSnapshot.objects.create(
            requirement_version=version,
            algorithm_version="match-1.0",
            input_catalog_version="pending",
            state="requested",
        )
        operation = Operation.objects.create(
            owner=request.user,
            kind="recommendation",
            state="pending",
            result={"recommendationRequestId": str(snapshot.id)},
        )
        enqueue_recommendation(version, snapshot, operation, request.correlation_id)
        body = {"operationId": str(operation.id), "statusUrl": f"/api/v1/operations/{operation.id}", "recommendationRequestId": str(snapshot.id)}
        _save_idempotency(request.user, key, f"requirement-submit:{profile_id}", status.HTTP_202_ACCEPTED, body)
        audit(request, "requirement.submitted", profile, metadata={"version": version.version})
        return Response(body, status=status.HTTP_202_ACCEPTED)


class RequirementVersionListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, profile_id):
        profile = _owned_profile(request.user, profile_id)
        return Response([
            {"id": str(item.id), "version": item.version, "status": item.status, "schemaVersion": item.schema_version, "createdAt": item.created_at, "submittedAt": item.submitted_at}
            for item in profile.versions.order_by("-version")
        ])


class RequirementShareView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, profile_id):
        profile = _owned_profile(request.user, profile_id)
        if not Consent.objects.filter(user=request.user, purpose="profile_sharing", withdrawn_at__isnull=True).exists():
            raise ValidationError({"consent": [{"code": "SHARING_CONSENT_REQUIRED"}]})
        expires_at = request.data.get("expiresAt")
        from django.utils.dateparse import parse_datetime
        expiry = parse_datetime(expires_at) if isinstance(expires_at, str) else expires_at
        if expiry is None or expiry <= timezone.now() or expiry > timezone.now() + timedelta(days=30):
            raise ValidationError({"expiresAt": [{"code": "INVALID_EXPIRY"}]})
        token = secrets.token_urlsafe(32)
        share = ProfileShare.objects.create(profile=profile, token_hash=hashlib.sha256(token.encode()).hexdigest(), permissions="read", expires_at=expiry)
        audit(request, "requirement.share_created", profile, metadata={"shareId": str(share.id)})
        return Response({"id": str(share.id), "shareUrl": f"/api/v1/shared/requirement-profiles/{token}", "expiresAt": expiry}, status=status.HTTP_201_CREATED)


class RequirementShareRevokeView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, profile_id, share_id):
        profile = _owned_profile(request.user, profile_id)
        share = get_object_or_404(ProfileShare, id=share_id, profile=profile, revoked_at__isnull=True)
        share.revoked_at = timezone.now()
        share.save(update_fields=["revoked_at", "updated_at"])
        audit(request, "requirement.share_revoked", profile, metadata={"shareId": str(share.id)})
        return Response(status=status.HTTP_204_NO_CONTENT)


class SharedRequirementView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, token):
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        share = get_object_or_404(ProfileShare.objects.select_related("profile__active_version"), token_hash=token_hash, revoked_at__isnull=True, expires_at__gt=timezone.now())
        data = RequirementProfileSerializer(share.profile).data
        sections = dict(data.get("sections", {}))
        safe_places = []
        for place in sections.get("places", []):
            safe_place = {key: value for key, value in place.items() if key not in {"coordinates", "address"}}
            if safe_place.get("precision") == "exact":
                safe_place["label"] = "Private exact location"
                safe_place["placeId"] = "redacted"
            safe_places.append(safe_place)
        sections["places"] = safe_places
        data["sections"] = sections
        return Response(data)


def _validate_guest_sections(sections):
    """Reject shapes the shared scorer cannot safely interpret."""
    home, budget = sections["home"], sections["budget"]
    if not isinstance(home, dict) or not isinstance(budget, dict):
        raise ValidationError({"sections": "Home and budget must be objects."})
    configurations = home.get("configurations")
    if not isinstance(configurations, list) or not 1 <= len(configurations) <= 10 or any(
        not isinstance(value, str) or not 1 <= len(value) <= 32 for value in configurations
    ):
        raise ValidationError({"home.configurations": "Choose one or more home configurations."})
    if not isinstance(home.get("carpetAreaSqFt"), dict) or type(home.get("bathroomsMinimum")) is not int or not 0 <= home["bathroomsMinimum"] <= 10 or not isinstance(home.get("bathroomsIsHardConstraint"), bool):
        raise ValidationError({"home": "Invalid home requirements."})
    if any(value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0 or value > 100000) for value in home["carpetAreaSqFt"].values()):
        raise ValidationError({"home.carpetAreaSqFt": "Invalid carpet area."})
    if not isinstance(sections["weights"], dict):
        raise ValidationError({"weights": "Invalid priority weights."})
    if not isinstance(budget.get("isHardConstraint"), bool) or not any(budget.get(key) is not None for key in ("purchaseMaximum", "allInMaximum")):
        raise ValidationError({"budget": "Enter a valid maximum budget."})
    for key in ("purchaseMinimum", "purchaseMaximum", "allInMaximum"):
        value = budget.get(key)
        if value is not None and (type(value) is not int or not 0 < value <= 10**14):
            raise ValidationError({"budget": "Invalid budget amount."})
    if not isinstance(sections["places"], list) or not isinstance(sections["societyAmenities"], list) or not isinstance(sections["nearbyAmenities"], list):
        raise ValidationError({"sections": "Destinations and amenities must be lists."})
    for place in sections["places"]:
        if not isinstance(place, dict) or not isinstance(place.get("label"), str) or not 2 <= len(place["label"]) <= 100 or not isinstance(place.get("modes"), list) or not 1 <= len(place["modes"]) <= 4 or any(not isinstance(mode, str) or mode not in {"car", "transit", "walking", "cycling"} for mode in place["modes"]):
            raise ValidationError({"places": "Invalid destination or travel mode."})
        if "arrivalWindow" in place and not isinstance(place["arrivalWindow"], dict):
            raise ValidationError({"places": "Invalid arrival window."})
    for key in ("societyAmenities", "nearbyAmenities"):
        if len(sections[key]) > 100:
            raise ValidationError({key: "Too many amenities."})
        for amenity in sections[key]:
            if not isinstance(amenity, dict) or not isinstance(amenity.get("code"), str) or not 1 <= len(amenity["code"]) <= 64 or not isinstance(amenity.get("importance"), str) or amenity["importance"] not in {"required", "preferred"}:
                raise ValidationError({key: "Invalid amenity preference."})
            if key == "nearbyAmenities":
                distance = amenity.get("maximumDistanceMeters")
                if type(distance) is not int or not 100 <= distance <= 5000:
                    raise ValidationError({key: "Invalid nearby distance."})
            elif not isinstance(amenity.get("substituteAccepted"), bool):
                raise ValidationError({key: "Invalid amenity substitution choice."})
    if not isinstance(sections["microMarket"], dict) or not isinstance(sections["microMarket"].get("preferredLocalities"), list) or not isinstance(sections["additionalNeeds"], str) or len(sections["additionalNeeds"]) > 2000:
        raise ValidationError({"sections": "Invalid locality or additional needs."})


@method_decorator(csrf_protect, name="dispatch")
class GuestRecommendationView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        if len(request.body) > 16384 or not isinstance(request.data, dict):
            raise ValidationError({"sections": "Invalid or oversized request."})
        if request.data.get("consent") is not True:
            raise ValidationError({"consent": "Cookie consent is required."})
        sections = request.data.get("sections")
        if not isinstance(sections, dict):
            raise ValidationError({"sections": "Provide requirement sections."})
        allowed = {"places", "home", "societyAmenities", "nearbyAmenities", "microMarket", "budget", "weights", "additionalNeeds"}
        if set(sections) != allowed:
            raise ValidationError({"sections": "Provide only the complete requirement sections."})
        _validate_guest_sections(sections)
        validate_requirement_payload(sections, final=True)
        _, items, exclusions = score_recommendations(sections, persist_routes=False)
        generated_at = timezone.now()
        return Response({
            "id": f"guest-{uuid.uuid4()}", "status": "ready", "profileVersion": 1,
            "algorithmVersion": "match-1.0", "generatedAt": generated_at,
            "expiresAt": generated_at + timedelta(hours=24), "items": items,
            "excludedSummary": exclusions,
        })


class RecommendationRequestViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def list(self, request):
        latest = (RecommendationSnapshot.objects
                  .filter(requirement_version__profile__owner=request.user,
                          state__in=["ready", "partially_ready", "expired"])
                  .select_related("requirement_version")
                  .order_by(F("generated_at").desc(nulls_last=True), "-created_at")
                  .first())
        return Response({"latest": RecommendationSnapshotSerializer(latest).data if latest else None})

    def create(self, request):
        key, existing = _idempotent_response(request, "recommendation-request")
        if existing:
            return Response(existing.response_body, status=existing.response_status)
        profile_id = request.data.get("profileId")
        version_number = request.data.get("profileVersion")
        profile = _owned_profile(request.user, profile_id)
        version = get_object_or_404(profile.versions, version=version_number, status="submitted")
        snapshot = RecommendationSnapshot.objects.create(
            requirement_version=version,
            algorithm_version="match-1.0",
            input_catalog_version="pending",
            state="requested",
        )
        operation = Operation.objects.create(
            owner=request.user,
            kind="recommendation",
            state="pending",
            result={"recommendationRequestId": str(snapshot.id)},
        )
        enqueue_recommendation(version, snapshot, operation, request.correlation_id)
        body = {"requestId": str(snapshot.id), "status": snapshot.state, "statusUrl": f"/api/v1/recommendation-requests/{snapshot.id}"}
        _save_idempotency(request.user, key, "recommendation-request", status.HTTP_202_ACCEPTED, body)
        return Response(body, status=status.HTTP_202_ACCEPTED)

    def retrieve(self, request, pk=None):
        snapshot = get_object_or_404(RecommendationSnapshot.objects.select_related("requirement_version__profile"), pk=pk, requirement_version__profile__owner=request.user)
        return Response(RecommendationSnapshotSerializer(snapshot).data)


class ProjectViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [AllowAny]
    pagination_class = BuyerCursorPagination

    def get_queryset(self):
        params = self.request.query_params
        include_processing = params.get("includeProcessing", "false")
        if include_processing not in {"true", "false"}:
            raise ValidationError({"includeProcessing": "Use true or false."})
        preview_detail = self.action == "retrieve" and settings.PROJECT_PUBLIC_PREVIEW_ENABLED
        states = ["published", "ingestion", "needs_review"] if preview_detail or (self.action == "list" and include_processing == "true") else ["published"]
        self.show_demo_pins = bool(
            self.action == "list" and include_processing == "true"
            and is_enabled("showDemoPinsOnBrowse", actor=self.request.user)
        )
        queryset = Project.objects.filter(status__in=states).exclude(address__has_key="mergedIntoProjectId").exclude(
            merge_out__state="applied").prefetch_related("project_amenities__amenity", "metrics", "verification_snapshots", "facts")
        if not self.show_demo_pins:
            queryset = queryset.exclude(organization__legal_name="DemoPins")
        from .layout_pricing import annotate_project_prices

        queryset = annotate_project_prices(queryset, can_review=settings.PROJECT_PUBLIC_PREVIEW_ENABLED)
        if any(params.get(key) for key in ("configuration", "carpetAreaMinimum", "allInPriceMaximum", "mahaReraStatus", "amenity")):
            queryset = queryset.filter(status="published")
        if params.get("locality"):
            queryset = queryset.filter(locality__iexact=params["locality"])
        if params.get("configuration"):
            queryset = queryset.filter(buildings__configurations__configuration_type=params["configuration"],
                                       buildings__configurations__fact_set_version__lte=F("published_fact_set_version"))
        if params.get("carpetAreaMinimum"):
            queryset = queryset.filter(buildings__configurations__carpet_area_max__gte=params["carpetAreaMinimum"],
                                       buildings__configurations__fact_set_version__lte=F("published_fact_set_version"))
        if params.get("allInPriceMaximum"):
            from decimal import Decimal, InvalidOperation

            try:
                maximum = Decimal(params["allInPriceMaximum"])
            except InvalidOperation:
                raise ValidationError({"allInPriceMaximum": "Enter a positive amount in INR."}) from None
            if not maximum.is_finite() or maximum <= 0:
                raise ValidationError({"allInPriceMaximum": "Enter a positive amount in INR."})
            queryset = queryset.filter(layout_price_min__lte=maximum)
        if params.get("mahaReraStatus"):
            queryset = queryset.filter(verification_status=params["mahaReraStatus"])
        if params.get("amenity"):
            queryset = queryset.filter(project_amenities__amenity__taxonomy_code=params["amenity"])
        return queryset.distinct().order_by("submitted_name")

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["show_demo_pins"] = bool(getattr(self, "show_demo_pins", False))
        return context

    def get_serializer_class(self):
        if self.action == "retrieve" and settings.PROJECT_PUBLIC_PREVIEW_ENABLED:
            return ProjectPreviewSerializer
        return ProjectDetailSerializer if self.action == "retrieve" and self.request.user.is_authenticated else ProjectListSerializer

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response


class ProjectPictureView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, project_id):
        project = get_object_or_404(Project, pk=project_id)
        public = project.status in {"published", "ingestion", "needs_review"} and not (
            "mergedIntoProjectId" in project.address or Project.objects.filter(pk=project.pk, merge_out__state="applied").exists())
        privileged = request.user.is_authenticated and request.user.is_active and (
            _project_can_edit(request.user, project) or _is_reviewer(request.user))
        if not (public or privileged):
            raise NotFound()
        try:
            if project.project_pic:
                picture = project.project_pic.open("rb")
            elif privileged or settings.PROJECT_PUBLIC_PREVIEW_ENABLED:
                from .project_pictures import default_picture

                picture = io.BytesIO(default_picture(project))
            else:
                raise NotFound()
        except (OSError, ValueError):
            raise NotFound() from None
        response = FileResponse(picture, content_type="image/png")
        response["Content-Disposition"] = 'inline; filename="project.png"'
        response["Cache-Control"] = "private, no-store"
        return response


class ProjectGalleryView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, project_id):
        project = get_object_or_404(Project.objects.exclude(address__has_key="mergedIntoProjectId").exclude(
            merge_out__state="applied"), pk=project_id, status__in=["published", "ingestion", "needs_review"])
        can_review = bool(request.user.is_authenticated and request.user.is_active and
            (_project_can_edit(request.user, project) or _is_reviewer(request.user)))
        allow_unreviewed = can_review or settings.PROJECT_PUBLIC_PREVIEW_ENABLED
        assets = project_gallery_assets(project, can_review=allow_unreviewed).order_by("created_at", "pk")
        paginator = BuyerCursorPagination()
        paginator.ordering = ("created_at", "pk")
        page = paginator.paginate_queryset(assets, request, view=self)
        plans = visible_floor_plans(project.floor_plans.filter(active=True, asset_id__in=[asset.pk for asset in page]))
        if not allow_unreviewed:
            plans = plans.filter(review_state="approved")
        layouts = {}
        from .layout_pricing import pricing_layouts
        from .domain.room_dimensions import calculate_room_areas

        priced_plans = dict(pricing_layouts(can_review=allow_unreviewed).filter(
            project=project, asset_id__in=[asset.pk for asset in page],
        ).values_list("pk", "price"))
        for plan in plans.order_by("plan_name", "pk"):
            rooms = [{key: room[key] for key in ("room", "lengthFt", "widthFt", "areaSqFt")
                      if key in room and isinstance(room[key], (str, int, float))}
                     for room in calculate_room_areas(plan.dimensions)]
            layouts.setdefault(plan.asset_id, []).append({
                "id": str(plan.pk), "name": plan.display_title, "configuration": plan.configuration_type,
                "carpetAreaSqFt": plan.carpet_area_sqft, "rooms": rooms,
                "roomRectangleSumSqFt": plan.room_rectangle_sum_sqft,
                "price": format(priced_plans[plan.pk], ".2f") if priced_plans.get(plan.pk) is not None else None,
                "roomCount": plan.room_count, "bathrooms": plan.bathroom_count, "balconies": plan.balcony_count,
                "orientation": plan.orientation, "reviewState": plan.review_state,
            })
        items = []
        from .floor_plan_visibility import gallery_layout_ids

        layout_ids = gallery_layout_ids(project, page, can_review=allow_unreviewed)
        for asset in page:
            associated = layouts.get(asset.pk, [])
            category = asset.source_bounds.get("category", "") if isinstance(asset.source_bounds, dict) else ""
            if category not in {"floor-plans", "layouts", "general", "amenities"}:
                category = "layouts" if asset.asset_type == "unit_crop" else "floor-plans" if associated else "general"
            items.append({"id": str(asset.pk), "url": reverse("floor-plan-asset-media", args=[asset.pk]),
                "category": category, "title": associated[0]["name"] if associated else f"Project image {asset.page_number or ''}".strip(),
                "width": asset.pixel_width, "height": asset.pixel_height, "layouts": associated,
                "layoutIds": layout_ids[str(asset.pk)],
                "reviewState": "approved" if associated and all(plan["reviewState"] == "approved" for plan in associated) else "unreviewed"})
        response = paginator.get_paginated_response(items)
        response.data["access"] = "preview" if settings.PROJECT_PUBLIC_PREVIEW_ENABLED else "review" if can_review else "published" if project.status == "published" else "restricted"
        if allow_unreviewed and project.status != "published":
            response.data["project"] = ProjectPreviewSerializer(project).data
        response["Cache-Control"] = "private, no-store"
        return response


class MapStationListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        from .map_catalogue import map_stations

        return Response(map_stations())


class ProjectComparisonView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        project_ids = request.data.get("projectIds", [])
        if not isinstance(project_ids, list) or not 2 <= len(set(project_ids)) <= 4:
            raise ValidationError({"projectIds": [{"code": "SELECT_TWO_TO_FOUR"}]})
        projects = list(Project.objects.filter(id__in=project_ids, status="published"))
        if len(projects) != len(set(project_ids)):
            raise NotFound()
        by_id = {str(project.id): project for project in projects}
        return Response({"projects": [ProjectDetailSerializer(by_id[str(project_id)]).data for project_id in project_ids], "units": {"area": "sqft", "currency": "INR"}})


class OrganizationViewSet(viewsets.ModelViewSet):
    serializer_class = OrganizationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if self.request.user.is_staff:
            return Organization.objects.all()
        return Organization.objects.filter(status="active", memberships__user=self.request.user, memberships__status="active").distinct()

    def _require_platform_admin(self):
        if not self.request.user.is_staff:
            raise PermissionDenied("Organizations and memberships are provisioned by a platform administrator.")

    @transaction.atomic
    def perform_create(self, serializer):
        self._require_platform_admin()
        organization = serializer.save()
        OrganizationMembership.objects.create(organization=organization, user=self.request.user, role="owner", status="active")
        audit(self.request, "organization.created", organization)

    def perform_update(self, serializer):
        self._require_platform_admin()
        organization = serializer.save()
        audit(self.request, "organization.updated", organization)

    def perform_destroy(self, instance):
        self._require_platform_admin()
        super().perform_destroy(instance)


class DeveloperProjectCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, organization_id):
        organization = get_object_or_404(Organization, id=organization_id, status="active")
        from .builder_access import builder_memberships
        if not request.user.is_staff and not builder_memberships(request.user).filter(organization=organization).exists():
            raise PermissionDenied()
        return self.create_project(request, organization, request.data)

    def create_project(self, request, organization, data):
        serializer = ProjectCreateSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        maha_id = normalize_maha_rera_id(serializer.validated_data["maha_rera_id"])
        duplicate = Project.objects.filter(maha_rera_id=maha_id).first()
        if duplicate:
            return Response({"code": "DUPLICATE_PROJECT"}, status=status.HTTP_409_CONFLICT)
        project = serializer.save(organization=organization, maha_rera_id=maha_id, locality=serializer.validated_data.get("address", {}).get("locality", ""), status="verifying")
        VerificationSnapshot.objects.create(
            project=project,
            provider="maharera",
            provider_record_id=maha_id,
            retrieved_at=timezone.now(),
            fields={},
            snapshot_hash=hashlib.sha256(maha_id.encode()).hexdigest(),
            match_result="unavailable",
            discrepancies=[],
            adapter_version="manual-fallback-1.0",
        )
        project.verification_checked_at = timezone.now()
        project.save(update_fields=["verification_checked_at", "updated_at"])
        audit(request, "project.created", project)
        return Response(ProjectCreateSerializer(project).data, status=status.HTTP_201_CREATED)


class UploadIntentView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, project_id):
        project = get_object_or_404(Project, id=project_id)
        if not _project_can_edit(request.user, project):
            raise PermissionDenied()
        _require_active_project(project)
        filename = Path(str(request.data.get("filename", ""))).name
        media_type = str(request.data.get("declaredMediaType", ""))
        byte_length = request.data.get("byteLength")
        sha256 = str(request.data.get("sha256", "")).lower()
        suffix = Path(filename).suffix.lower()
        if not filename or media_type not in ALLOWED_UPLOAD_TYPES or suffix not in ALLOWED_UPLOAD_TYPES[media_type]:
            raise ValidationError({"file": [{"code": "UNSUPPORTED_MEDIA_TYPE"}]})
        if not isinstance(byte_length, int) or byte_length <= 0 or byte_length > MAX_UPLOAD_BYTES:
            raise ValidationError({"byteLength": [{"code": "OUT_OF_RANGE"}]})
        if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
            raise ValidationError({"sha256": [{"code": "INVALID_SHA256"}]})
        document, created = EvidenceDocument.objects.get_or_create(
            project=project,
            sha256=sha256,
            defaults={
                "object_key": f"user/media/todo/{project.id}/{secrets.token_urlsafe(24)}{suffix}",
                "original_name": filename,
                "declared_media_type": media_type,
                "byte_length": byte_length,
                "status": "awaiting_upload",
                "uploaded_by": request.user,
                "retention_until": timezone.now() + timedelta(days=getattr(settings, "REJECTED_UPLOAD_RETENTION_DAYS", 7)),
            },
        )
        if not created:
            if document.byte_length != byte_length or document.declared_media_type != media_type:
                raise ValidationError({"detail": "This file hash already exists with different upload metadata."})
            if document.status != "awaiting_upload":
                return Response({
                    "type": "https://realestate.invalid/problems/document-already-exists",
                    "title": "Document already exists", "status": 409, "code": "DOCUMENT_ALREADY_EXISTS",
                    "errors": {
                        "code": "DOCUMENT_ALREADY_EXISTS", "documentId": str(document.pk),
                        "detail": f'This file already exists in this project as "{document.original_name}" '
                                  f'(status: {document.status}). Refresh documents to see its progress; '
                                  "uploading it again will not restart processing.",
                    },
                }, status=status.HTTP_409_CONFLICT)
        else:
            audit(request, "document.intent_created", document, metadata={"mediaType": media_type, "bytes": byte_length})
        return Response({
            "documentId": str(document.id),
            "uploadAction": f"/api/v1/developer-projects/{project.id}/documents/{document.id}/content",
            "method": "PUT",
            "requiredHeaders": {"Content-Type": media_type},
            "maximumSize": MAX_UPLOAD_BYTES,
            "expiresAt": timezone.now() + timedelta(minutes=10),
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class DocumentContentUploadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [FileUploadParser]

    @transaction.atomic
    def put(self, request, project_id, document_id):
        document = get_object_or_404(EvidenceDocument.objects.select_for_update(), id=document_id, project_id=project_id, status="awaiting_upload")
        if not _project_can_edit(request.user, document.project):
            raise PermissionDenied()
        _require_active_project(document.project)
        uploaded = request.data.get("file")
        if uploaded is None:
            raise ValidationError({"file": [{"code": "REQUIRED"}]})
        content = uploaded.read(MAX_UPLOAD_BYTES + 1)
        if len(content) != document.byte_length or len(content) > MAX_UPLOAD_BYTES:
            raise ValidationError({"file": [{"code": "SIZE_MISMATCH"}]})
        if hashlib.sha256(content).hexdigest() != document.sha256:
            raise ValidationError({"file": [{"code": "HASH_MISMATCH"}]})
        if detect_media_type(io.BytesIO(content), document.declared_media_type) != document.declared_media_type:
            raise ValidationError({"file": [{"code": "MEDIA_TYPE_MISMATCH"}]})
        document.object_key = default_storage.save(document.object_key, ContentFile(content))
        document.status = "uploaded"
        document.save(update_fields=["object_key", "status", "updated_at"])
        audit(request, "document.uploaded", document)
        return Response(status=status.HTTP_204_NO_CONTENT)


class DocumentCompleteView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, project_id, document_id):
        document = get_object_or_404(EvidenceDocument.objects.select_for_update(), id=document_id, project_id=project_id)
        if not _project_can_edit(request.user, document.project):
            raise PermissionDenied()
        _require_active_project(document.project)
        key, existing = _idempotent_response(request, f"document-complete:{document_id}")
        if existing:
            return Response(existing.response_body, status=existing.response_status)
        if document.status != "uploaded" or not default_storage.exists(document.object_key):
            raise ValidationError({"document": [{"code": "UPLOAD_NOT_FOUND"}]})
        selection = _requested_vision_selection(request.data)
        document.status = "quarantined"
        document.scan_status = "pending"
        document.save(update_fields=["status", "scan_status", "updated_at"])
        body = _queue_document_processing(
            document, request.user, request.correlation_id, selection,
            extractor_version="extractor-1.0", job_status="awaiting_scan",
        )
        _save_idempotency(request.user, key, f"document-complete:{document_id}", status.HTTP_202_ACCEPTED, body)
        audit(request, "document.processing_queued", document)
        return Response(body, status=status.HTTP_202_ACCEPTED)


class ProjectIngestionQueueView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = get_object_or_404(Project, id=project_id)
        if not _project_can_edit(request.user, project) and not _is_reviewer(request.user):
            raise PermissionDenied()
        return Response(document_queue_snapshot(project))

    def post(self, request, project_id):
        project = get_object_or_404(Project, id=project_id)
        if not _project_can_edit(request.user, project):
            raise PermissionDenied()
        _require_active_project(project)
        limit = request.data.get("limit", 10)
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValidationError({"limit": [{"code": "OUT_OF_RANGE"}]})
        message_ids = pending_document_message_ids(project, limit=limit)
        results = process_pending_outbox(limit=limit, message_ids=message_ids)
        response = {
            "requested": len(message_ids),
            "results": results,
            "queue": document_queue_snapshot(project),
        }
        audit(request, "document.processing_batch_triggered", project, metadata={"requested": len(message_ids), "results": results})
        return Response(response)


class ProjectVisionProfileListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = get_object_or_404(Project, id=project_id)
        if not _project_can_edit(request.user, project) and not _is_reviewer(request.user):
            raise PermissionDenied()
        try:
            return Response(list_floor_plan_vision_profiles())
        except ValueError:
            return Response({"code": "VISION_CONFIGURATION_INVALID"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


class DeveloperDocumentListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = get_object_or_404(Project, id=project_id)
        if not _project_can_edit(request.user, project) and not _is_reviewer(request.user):
            raise PermissionDenied()
        return Response(EvidenceDocumentSerializer(project.documents.order_by("-created_at"), many=True).data)


class ProjectIngestionRunListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = get_object_or_404(Project, id=project_id)
        if not _project_can_edit(request.user, project) and not _is_reviewer(request.user):
            raise PermissionDenied()
        runs = project.ingestion_runs.select_related("document").order_by("-created_at")
        return Response(IngestionRunSerializer(runs, many=True).data)


class ProjectKnowledgeSearchView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, project_id):
        project = get_object_or_404(Project, id=project_id)
        if not _project_can_edit(request.user, project) and not _is_reviewer(request.user):
            raise PermissionDenied()
        query = str(request.data.get("query", "")).strip()
        if not 2 <= len(query) <= 500:
            raise ValidationError({"query": [{"code": "INVALID_LENGTH"}]})
        limit = request.data.get("limit", 8)
        if not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValidationError({"limit": [{"code": "OUT_OF_RANGE"}]})
        matches = retrieve(project, query, limit=limit)
        return Response({
            "query": query,
            "items": [
                {
                    "score": round(item["score"], 6),
                    "content": item["chunk"].content,
                    "documentId": str(item["chunk"].document_id),
                    "pageNumber": item["chunk"].page_number,
                    "metadata": item["chunk"].metadata,
                    "evidenceReference": f"/api/v1/media/{item['chunk'].document_id}/pages/{item['chunk'].page_number or 1}",
                }
                for item in matches
            ],
        })


class ProjectFloorPlanListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = get_object_or_404(Project, id=project_id)
        if not _project_can_edit(request.user, project) and not _is_reviewer(request.user):
            raise PermissionDenied()
        from .floor_plan_rules import plan_display_priority
        plans = list(visible_floor_plans(project.floor_plans.all()).select_related("asset", "document"))
        if request.query_params.get("sort") == "unit_name":
            plans.sort(key=lambda plan: [
                int(value) if value.isdigit() else value.casefold()
                for value in re.split(r"(\d+)", plan.plan_name or "")
            ])
        else:
            plans.sort(key=plan_display_priority)
        return Response(FloorPlanSerializer(plans, many=True).data)


class ResolveFloorPlanView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, floor_plan_id):
        if not _is_reviewer(request.user):
            raise PermissionDenied()
        from .models import FloorPlan

        plan = get_object_or_404(visible_floor_plans(FloorPlan.objects.select_for_update()), id=floor_plan_id)
        project = Project.objects.select_for_update().get(id=plan.project_id)
        if not request.user.is_superuser and _is_project_member(request.user, project):
            raise PermissionDenied("Reviewers cannot approve their own organization's floor plans.", code="self_approval_forbidden")
        decision = request.data.get("decision")
        if decision not in {"approve", "correct", "reject"}:
            raise ValidationError({"decision": [{"code": "INVALID_DECISION"}]})
        corrections = request.data.get("corrections", {})
        if decision == "correct":
            if not isinstance(corrections, dict):
                raise ValidationError({"corrections": [{"code": "INVALID_OBJECT"}]})
            editable = {
                "planName": "plan_name",
                "planScope": "plan_scope",
                "configurationType": "configuration_type",
                "carpetAreaSqFt": "carpet_area_sqft",
                "roomCount": "room_count",
                "bathroomCount": "bathroom_count",
                "balconyCount": "balcony_count",
                "orientation": "orientation",
                "dimensions": "dimensions",
                "features": "features",
            }
            unknown = set(corrections) - set(editable)
            if unknown:
                raise ValidationError({"corrections": [{"code": "UNKNOWN_FIELDS", "fields": sorted(unknown)}]})
            for field, value in corrections.items():
                setattr(plan, editable[field], value)
            if plan.plan_scope not in {"flat", "floor", "unknown"}:
                raise ValidationError({"corrections.planScope": [{"code": "INVALID_CHOICE"}]})
        plan.review_state = "approved" if decision in {"approve", "correct"} else "rejected"
        plan.save()
        if plan.review_state == "approved":
            ProjectFact.objects.update_or_create(
                project=project,
                fact_set_version=project.published_fact_set_version + 1,
                field_code=f"floor_plan_{plan.id.hex}",
                defaults={
                    "typed_value": {
                        "floorPlanId": str(plan.id),
                        "assetId": str(plan.asset_id) if plan.asset_id else None,
                        "planName": plan.plan_name,
                        "planScope": plan.plan_scope,
                        "configurationType": plan.configuration_type,
                        "carpetAreaSqFt": float(plan.carpet_area_sqft) if plan.carpet_area_sqft is not None else None,
                        "roomCount": plan.room_count,
                        "bathroomCount": plan.bathroom_count,
                        "balconyCount": plan.balcony_count,
                        "orientation": plan.orientation,
                        "dimensions": plan.dimensions,
                        "features": plan.features,
                    },
                    "unit": "sqft",
                    "source_class": "human_approved",
                    "status": "draft",
                    "valid_from": timezone.now(),
                    "reviewed_by": request.user,
                    "reviewed_at": timezone.now(),
                },
            )
        ReviewTask.objects.filter(
            project=plan.project,
            task_type__in=["floor_plan", "floor_plan_vision", "layout_quality"],
            state="open",
            resolution__floorPlanId=str(plan.id),
        ).update(state="resolved", completed_at=timezone.now(), resolution={"floorPlanId": str(plan.id), "decision": decision})
        audit(request, "floor_plan.reviewed", plan, reason_code=str(request.data.get("reasonCode", decision))[:64], metadata={"decision": decision})
        return Response(FloorPlanSerializer(plan).data)


class DeveloperSubmitReviewView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, project_id):
        project = get_object_or_404(Project.objects.select_for_update(), id=project_id)
        if not _project_can_edit(request.user, project):
            raise PermissionDenied()
        _require_active_project(project)
        if not project.processing_attested:
            raise ValidationError({"processingAttestation": [{"code": "REQUIRED"}]})
        if not project.verification_snapshots.exists():
            raise ValidationError({"verification": [{"code": "ATTEMPT_REQUIRED"}]})
        if not project.documents.exists():
            raise ValidationError({"documents": [{"code": "REQUIRED"}]})
        ReviewTask.objects.get_or_create(project=project, task_type="publication", state="open", defaults={"priority": 2})
        project.status = "needs_review"
        project.save(update_fields=["status", "updated_at"])
        audit(request, "project.submitted_for_review", project)
        return Response({"status": project.status}, status=status.HTTP_202_ACCEPTED)


class ReviewTaskViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ReviewTaskSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if not _is_reviewer(self.request.user):
            raise PermissionDenied()
        queryset = ReviewTask.objects.select_related("project", "candidate", "assigned_to").order_by("priority", "created_at")
        if self.request.query_params.get("status"):
            queryset = queryset.filter(state=self.request.query_params["status"])
        if self.request.query_params.get("type"):
            queryset = queryset.filter(task_type=self.request.query_params["type"])
        return queryset


class ResolveReviewTaskView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, task_id):
        if not _is_reviewer(request.user):
            raise PermissionDenied()
        task = get_object_or_404(ReviewTask.objects.select_for_update(), id=task_id)
        project = Project.objects.select_for_update().get(id=task.project_id)
        if not request.user.is_superuser and _is_project_member(request.user, project):
            raise PermissionDenied("Reviewers cannot approve their own organization's evidence.", code="self_approval_forbidden")
        expected = request.data.get("expectedVersion")
        if expected != task.version:
            return Response({"code": "VERSION_CONFLICT", "latestVersion": task.version}, status=status.HTTP_409_CONFLICT)
        candidate_id = request.data.get("selectedCandidateId")
        candidate = get_object_or_404(FactCandidate, id=candidate_id, project=task.project) if candidate_id else task.candidate
        corrected = request.data.get("correctedValue")
        if candidate is None and corrected is None:
            raise ValidationError({"selectedCandidateId": [{"code": "REQUIRED"}]})
        reason = str(request.data.get("reasonCode", ""))
        if not reason:
            raise ValidationError({"reasonCode": [{"code": "REQUIRED"}]})
        value = corrected if corrected is not None else candidate.typed_value
        next_set = project.published_fact_set_version + 1
        ProjectFact.objects.update_or_create(
            project=project,
            fact_set_version=next_set,
            field_code=candidate.field_code if candidate else "reviewer_correction",
            defaults={
                "typed_value": value,
                "unit": candidate.unit if candidate else "",
                "source_class": "human_approved",
                "selected_candidate": candidate,
                "status": "draft",
                "valid_from": timezone.now(),
                "reviewed_by": request.user,
                "reviewed_at": timezone.now(),
            },
        )
        if candidate:
            candidate.review_state = "approved"
            candidate.save(update_fields=["review_state", "updated_at"])
            from .models import FactConflict

            FactConflict.objects.filter(
                project=project,
                field_code=candidate.field_code,
                state="open",
            ).update(
                state="resolved",
                resolution={"selectedCandidateId": str(candidate.id), "reasonCode": reason},
            )
        task.state = "resolved"
        task.version += 1
        task.completed_at = timezone.now()
        task.resolution = {"selectedCandidateId": str(candidate.id) if candidate else None, "corrected": corrected is not None, "reasonCode": reason, "comment": str(request.data.get("comment", ""))[:1000]}
        task.save(update_fields=["state", "version", "completed_at", "resolution", "updated_at"])
        audit(request, "review_task.resolved", task, reason_code=reason)
        return Response(ReviewTaskSerializer(task).data)


class PublishProjectView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, project_id):
        if not _is_reviewer(request.user):
            raise PermissionDenied()
        key, existing = _idempotent_response(request, f"project-publish:{project_id}")
        if existing:
            return Response(existing.response_body, status=existing.response_status)
        project = get_object_or_404(Project.objects.select_for_update(), id=project_id)
        if not request.user.is_superuser and _is_project_member(request.user, project):
            raise PermissionDenied("Reviewers cannot publish their own organization's project.", code="self_approval_forbidden")
        expected = request.data.get("expectedFactSetVersion")
        next_set = project.published_fact_set_version + 1
        if expected != next_set:
            return Response({"code": "VERSION_CONFLICT", "latestFactSetVersion": project.published_fact_set_version}, status=status.HTTP_409_CONFLICT)
        if project.review_tasks.filter(state="open").exclude(task_type="publication").exists():
            raise ValidationError({"reviewTasks": [{"code": "OPEN_TASKS_REMAIN"}]})
        if not project.verification_snapshots.exists():
            raise ValidationError({"verification": [{"code": "ATTEMPT_REQUIRED"}]})
        draft_facts = project.facts.filter(fact_set_version=next_set, status="draft")
        if not draft_facts.exists():
            raise ValidationError({"facts": [{"code": "NO_APPROVED_FACTS"}]})
        draft_facts.update(status="published")
        project.published_fact_set_version = next_set
        project.status = "published"
        project.save(update_fields=["published_fact_set_version", "status", "updated_at"])
        compute_project_metrics(project)
        project.review_tasks.filter(task_type="publication", state="open").update(state="resolved", completed_at=timezone.now())
        body = {"projectId": str(project.id), "status": project.status, "factSetVersion": next_set}
        _save_idempotency(request.user, key, f"project-publish:{project_id}", status.HTTP_200_OK, body)
        audit(request, "project.published", project, metadata={"factSetVersion": next_set})
        return Response(body)


class MediaPageView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, document_id, page_number):
        document = get_object_or_404(EvidenceDocument.objects.select_related("project"), id=document_id)
        allowed = _project_can_edit(request.user, document.project) or _is_reviewer(request.user) or document.project.status == "published"
        if not allowed or document.status not in ["ready", "extracting", "needs_review", "approved", "published"]:
            raise NotFound()
        if not default_storage.exists(document.object_key):
            raise Http404
        response = FileResponse(default_storage.open(document.object_key, "rb"), content_type=document.detected_media_type or document.declared_media_type)
        response["Content-Disposition"] = f'inline; filename="evidence-{document.id}{Path(document.original_name).suffix.lower()}"'
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        audit(request, "evidence.viewed", document, metadata={"page": page_number})
        return response


class FloorPlanAssetMediaView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, asset_id):
        from .models import FloorPlanAsset

        asset = get_object_or_404(FloorPlanAsset.objects.select_related("project", "document"), id=asset_id, status="ready")
        active_project = asset.project.status in {"published", "ingestion", "needs_review"} and not (
            "mergedIntoProjectId" in asset.project.address or Project.objects.filter(pk=asset.project_id, merge_out__state="applied").exists())
        approved_public_plan = active_project and project_gallery_assets(
            asset.project, can_review=settings.PROJECT_PUBLIC_PREVIEW_ENABLED).filter(pk=asset.pk).exists()
        privileged = request.user.is_authenticated and request.user.is_active and (
            _project_can_edit(request.user, asset.project) or _is_reviewer(request.user))
        allowed = privileged or approved_public_plan
        if not allowed or not default_storage.exists(asset.object_key):
            raise NotFound()
        response = FileResponse(default_storage.open(asset.object_key, "rb"), content_type=asset.media_type)
        response["Content-Disposition"] = f'inline; filename="floor-plan-{asset.id}.png"'
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        if request.user.is_authenticated:
            audit(request, "floor_plan_asset.viewed", asset, metadata={"documentId": str(asset.document_id)})
        return response


class ShortlistViewSet(viewsets.ModelViewSet):
    serializer_class = ShortlistSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = BuyerCursorPagination

    def get_queryset(self):
        return Shortlist.objects.filter(owner=self.request.user).prefetch_related("shortlistproject_set")

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class ShortlistProjectView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, shortlist_id):
        shortlist = get_object_or_404(Shortlist, id=shortlist_id, owner=request.user)
        project = get_object_or_404(Project, id=request.data.get("projectId"), status="published")
        entry, created = ShortlistProject.objects.get_or_create(shortlist=shortlist, project=project, defaults={"note": str(request.data.get("note", ""))[:4000]})
        if not created and "note" in request.data:
            entry.note = str(request.data["note"])[:4000]
            entry.save(update_fields=["note", "updated_at"])
        audit(request, "shortlist.project_added", shortlist, metadata={"projectId": str(project.id)})
        return Response(ShortlistSerializer(shortlist).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    def delete(self, request, shortlist_id):
        shortlist = get_object_or_404(Shortlist, id=shortlist_id, owner=request.user)
        deleted, _ = ShortlistProject.objects.filter(shortlist=shortlist, project_id=request.data.get("projectId")).delete()
        if not deleted:
            raise NotFound()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ComparisonShareViewSet(viewsets.ModelViewSet):
    serializer_class = ComparisonShareSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ComparisonShare.objects.filter(owner=self.request.user)

    def create(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = secrets.token_urlsafe(32)
        share = serializer.save(owner=request.user, token_hash=hashlib.sha256(token.encode()).hexdigest())
        data = serializer.data | {"shareUrl": f"/api/v1/shared/comparisons/{token}"}
        audit(request, "comparison.share_created", share)
        return Response(data, status=status.HTTP_201_CREATED)

    def perform_destroy(self, instance):
        instance.revoked_at = timezone.now()
        instance.save(update_fields=["revoked_at", "updated_at"])


class SharedComparisonView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, token):
        share = get_object_or_404(ComparisonShare, token_hash=hashlib.sha256(token.encode()).hexdigest(), revoked_at__isnull=True, expires_at__gt=timezone.now())
        projects = Project.objects.filter(id__in=share.project_ids, status="published")
        by_id = {str(project.id): project for project in projects}
        return Response({"projects": [ProjectDetailSerializer(by_id[str(project_id)]).data for project_id in share.project_ids if str(project_id) in by_id], "expiresAt": share.expires_at})


class NotificationSubscriptionViewSet(viewsets.ModelViewSet):
    serializer_class = NotificationSubscriptionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return NotificationSubscription.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        subscription = serializer.save(user=self.request.user)
        audit(self.request, "notification.subscribed", subscription)


class OperationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, operation_id):
        operation = get_object_or_404(Operation, id=operation_id, owner=request.user)
        return Response({"id": str(operation.id), "kind": operation.kind, "status": operation.state, "result": operation.result, "errorCode": operation.error_code, "updatedAt": operation.updated_at})


class BuyerAmenityListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(list(Amenity.objects.order_by("display_name", "id").values("taxonomy_code", "display_name", "category")))


class PlaceSuggestView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = request.query_params.get("q", "").strip()
        if len(query) < 2 or len(query) > 100:
            raise ValidationError({"q": [{"code": "INVALID_LENGTH"}]})
        return Response({"items": [{"placeId": f"manual:{hashlib.sha256(query.casefold().encode()).hexdigest()[:20]}", "label": query, "precision": "locality", "provider": "manual"}], "providerStatus": "manual_fallback"})


class TravelPreviewView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        origins = request.data.get("origins", [])
        destinations = request.data.get("destinations", [])
        modes = request.data.get("modes", [])
        if not all(isinstance(item, list) for item in [origins, destinations, modes]) or len(origins) * len(destinations) * max(1, len(modes)) > 25:
            raise ValidationError({"matrix": [{"code": "TOO_LARGE"}]})
        if not origins or not destinations or not modes or not set(modes).issubset({"car", "transit", "walking", "cycling"}):
            raise ValidationError({"matrix": [{"code": "INVALID_TRAVEL_REQUEST"}]})
        for location in origins + destinations:
            if not isinstance(location, dict) or set(location) - {"placeId", "latitude", "longitude", "precision"}:
                raise ValidationError({"locations": [{"code": "INVALID_LOCATION"}]})
            if location.get("placeId"):
                if not isinstance(location["placeId"], str) or len(location["placeId"]) > 255:
                    raise ValidationError({"locations": [{"code": "INVALID_PLACE_ID"}]})
            else:
                latitude, longitude = location.get("latitude"), location.get("longitude")
                if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                    raise ValidationError({"locations": [{"code": "INVALID_COORDINATES"}]})
        departure_bucket = str(request.data.get("departureBucket", "any"))[:64]
        result = estimate_matrix(origins, destinations, modes, departure_bucket)
        if result["status"] == "unavailable":
            result["unavailableReason"] = "ROUTING_PROVIDER_NOT_CONFIGURED"
        return Response(result)


class AccountExportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        profiles = RequirementProfile.objects.filter(owner=request.user).select_related("active_version")
        consents = Consent.objects.filter(user=request.user)
        shares = ComparisonShare.objects.filter(owner=request.user)
        return Response({
            "generatedAt": timezone.now(),
            "account": {"id": request.user.id, "username": request.user.get_username(), "email": request.user.email},
            "consents": ConsentSerializer(consents, many=True).data,
            "requirementProfiles": RequirementProfileSerializer(profiles, many=True).data,
            "comparisonShares": ComparisonShareSerializer(shares, many=True).data,
        })


class AccountDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        if request.data.get("confirmation") != "DELETE":
            raise ValidationError({"confirmation": [{"code": "REAUTH_CONFIRMATION_REQUIRED"}]})
        user = request.user
        operation = Operation.objects.create(owner=user, kind="account_deletion", state="completed", result={"anonymizedAt": timezone.now().isoformat()})
        RequirementProfile.objects.filter(owner=user).update(status="deleted")
        ProfileShare.objects.filter(profile__owner=user).update(revoked_at=timezone.now())
        ComparisonShare.objects.filter(owner=user).update(revoked_at=timezone.now())
        NotificationSubscription.objects.filter(user=user).update(enabled=False)
        audit(request, "account.deletion_requested", operation)
        user.email = ""
        user.first_name = ""
        user.last_name = ""
        user.is_active = False
        user.save(update_fields=["email", "first_name", "last_name", "is_active"])
        logout(request)
        return Response({"operationId": str(operation.id), "status": operation.state}, status=status.HTTP_202_ACCEPTED)


class AmenityAdminViewSet(viewsets.ModelViewSet):
    queryset = Amenity.objects.all().order_by("category", "display_name")
    permission_classes = [IsAdminUser]

    def get_serializer_class(self):
        from .serializers import AmenitySerializer
        return AmenitySerializer

    def perform_create(self, serializer):
        instance = serializer.save()
        audit(self.request, "taxonomy.created", instance)


class IntegrationHealthAdminView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        configured = getattr(settings, "INTEGRATION_PROVIDERS", {}) | {
            "routing": settings.ROUTING_PROVIDER != "unavailable",
            "malwareScanner": settings.CLAMAV_ENABLED or settings.DOCUMENT_SCANNER != "unavailable",
        }
        return Response({
            "providers": [
                {"code": code, "status": "configured" if bool(configured.get(code)) else "unavailable"}
                for code in ["maharera", "routing", "transactions", "malwareScanner", "documentExtraction"]
            ],
            "checkedAt": timezone.now(),
        })


class OperationalMetricsAdminView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        from django.db.models import Avg, Count, ExpressionWrapper, F, fields
        from .models import IngestionRun

        profile_counts = dict(RequirementProfile.objects.values_list("status").annotate(count=Count("id")))
        extraction_counts = dict(IngestionRun.objects.values_list("status").annotate(count=Count("id")))
        review_backlog = dict(ReviewTask.objects.filter(state="open").values_list("task_type").annotate(count=Count("id")))
        duration = ExpressionWrapper(F("updated_at") - F("created_at"), output_field=fields.DurationField())
        ranking_latency = Operation.objects.filter(kind="recommendation", state="completed").annotate(duration=duration).aggregate(average=Avg("duration"))["average"]
        stale_before = timezone.now() - timedelta(days=7)
        return Response({
            "generatedAt": timezone.now(),
            "onboarding": {
                "profilesByStatus": profile_counts,
                "completionRate": round(profile_counts.get("submitted", 0) / max(1, sum(profile_counts.values())), 4),
            },
            "extraction": {
                "runsByStatus": extraction_counts,
                "failureCount": extraction_counts.get("failed", 0),
            },
            "reviewBacklog": review_backlog,
            "ranking": {
                "averageLatencyMilliseconds": round(ranking_latency.total_seconds() * 1000, 2) if ranking_latency else None,
            },
            "sourceFreshness": {
                "projectsWithStaleVerification": Project.objects.filter(status="published").filter(Q(verification_checked_at__lt=stale_before) | Q(verification_checked_at__isnull=True)).count(),
            },
            "partners": {
                "routing": "available" if settings.ROUTING_PROVIDER != "unavailable" else "unavailable",
                "malwareScanner": "available" if (
                    settings.CLAMAV_ENABLED or settings.DOCUMENT_SCANNER != "unavailable"
                ) else "unavailable",
                "mahaRera": "available" if bool(getattr(settings, "INTEGRATION_PROVIDERS", {}).get("maharera")) else "unavailable",
                "transactions": "available" if bool(getattr(settings, "INTEGRATION_PROVIDERS", {}).get("transactions")) else "unavailable",
            },
        })


class AuditEventAdminView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        events = AuditEvent.objects.select_related("actor").order_by("-occurred_at")[:200]
        return Response([
            {
                "id": str(event.id),
                "actorId": event.actor_id,
                "action": event.action,
                "entityType": event.entity_type,
                "entityId": event.entity_id,
                "reasonCode": event.reason_code,
                "correlationId": event.correlation_id,
                "occurredAt": event.occurred_at,
                "metadata": event.metadata,
            }
            for event in events
        ])


class SegmentFloorPlanUnitsAdminView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request, floor_plan_id=None):
        return Response({
            "modes": ["script", "ai", "both"],
            "ai": available_flat_separation_ai_profiles(),
            "expectedCount": {"required": False, "minimum": 1, "maximum": 40},
        })

    def post(self, request, floor_plan_id):
        key, existing = _idempotent_response(request, f"floor-plan-segmentation:{floor_plan_id}")
        if existing:
            return Response(existing.response_body, status=existing.response_status)
        mode = request.data.get("mode", "script")
        expected_count = request.data.get("expectedCount")
        vision_profile_id = request.data.get("visionProfileId")
        supersede_source = request.data.get("supersedeSource", False)
        if not isinstance(mode, str) or mode not in {"script", "ai", "both"}:
            raise ValidationError({"mode": [{"code": "INVALID_SEGMENTATION_MODE"}]})
        if expected_count is not None and (
            isinstance(expected_count, bool) or not isinstance(expected_count, int) or not 1 <= expected_count <= 40
        ):
            raise ValidationError({"expectedCount": [{"code": "INVALID_EXPECTED_COUNT"}]})
        if vision_profile_id is not None and not isinstance(vision_profile_id, str):
            raise ValidationError({"visionProfileId": [{"code": "INVALID_PROFILE"}]})
        if not isinstance(supersede_source, bool):
            raise ValidationError({"supersedeSource": [{"code": "INVALID_BOOLEAN"}]})
        try:
            body = separate_floor_plan_units(
                floor_plan_id, mode=mode, expected_count=expected_count,
                vision_profile_id=vision_profile_id, supersede_source=supersede_source,
                actor=request.user,
            )
        except FloorPlan.DoesNotExist:
            raise NotFound() from None
        except FlatSeparationFailed as exc:
            body = exc.result
            response_status = status.HTTP_422_UNPROCESSABLE_ENTITY
            _save_idempotency(request.user, key, f"floor-plan-segmentation:{floor_plan_id}", response_status, body)
            return Response(body, status=response_status)
        except ValueError as exc:
            raise ValidationError({"segmentation": [{"code": str(exc)[:100]}]}) from None
        _save_idempotency(request.user, key, f"floor-plan-segmentation:{floor_plan_id}", status.HTTP_200_OK, body)
        return Response(body)


class ManualFloorPlanCropsAdminView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, floor_plan_id):
        key, existing = _idempotent_response(request, f"floor-plan-manual-crops:{floor_plan_id}")
        if existing:
            return Response(existing.response_body, status=existing.response_status)
        plan = get_object_or_404(visible_floor_plans(FloorPlan.objects.select_related("asset", "document")), id=floor_plan_id)
        regions = request.data.get("regions")
        if not isinstance(regions, list):
            raise ValidationError({"regions": [{"code": "REQUIRED"}]})
        try:
            body = create_manual_layout_crops(
                plan, regions, request.user, request.data.get("visionProfileId"),
                request.data.get("queueEvaluation", True) is True,
                request.data.get("rotationDegrees", 0),
            )
        except ValueError as exc:
            raise ValidationError({"regions": [{"code": str(exc)[:100]}]}) from None
        _save_idempotency(request.user, key, f"floor-plan-manual-crops:{floor_plan_id}", status.HTTP_202_ACCEPTED, body)
        return Response(body, status=status.HTTP_202_ACCEPTED)


class MarkDocumentLayoutPageView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, project_id, document_id):
        document = get_object_or_404(EvidenceDocument, id=document_id, project_id=project_id)
        if not _project_can_edit(request.user, document.project):
            raise PermissionDenied()
        _require_active_project(document.project)
        page_number = request.data.get("pageNumber")
        if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
            raise ValidationError({"pageNumber": [{"code": "INVALID_PAGE_NUMBER"}]})
        state_value = request.data.get("state", "layout")
        if state_value not in {"layout", "excluded"}:
            raise ValidationError({"state": [{"code": "INVALID_PAGE_STATE"}]})
        if state_value == "layout":
            from .source_page_additions import add_source_pages
            from .ingestion_runtime import WorkAlreadyClaimed
            try:
                additions = add_source_pages(
                    document.project, document.pk, [page_number], "layouts", request.user,
                    request.data.get("visionProfileId"), mark_layout=True, notes=request.data.get("notes", ""),
                )
            except (ValueError, OSError, WorkAlreadyClaimed):
                raise ValidationError({"pageNumber": [{"code": "PAGE_QUEUE_UNAVAILABLE"}]}) from None
            decision = DocumentLayoutPage.objects.get(document=document, page_number=page_number)
            return Response({"id": str(decision.id), "pageNumber": page_number, "state": state_value,
                             "queueStatus": "existing" if additions[0]["existing"] else "pending",
                             "assetId": additions[0]["assetId"]}, status=status.HTTP_202_ACCEPTED)
        try:
            validate_document_page(document, page_number)
        except ValueError as exc:
            raise ValidationError({"pageNumber": [{"code": str(exc)[:100]}]}) from None
        decision, _ = DocumentLayoutPage.objects.update_or_create(
            document=document, page_number=page_number,
            defaults={"state": state_value, "source": "user_flag", "reason_code": "MISSED_LAYOUT",
                      "notes": str(request.data.get("notes", ""))[:1000], "marked_by": request.user},
        )
        audit(request, "document.layout_page_marked", decision, metadata={"documentId": str(document.id), "pageNumber": page_number})
        return Response({"id": str(decision.id), "pageNumber": page_number, "state": state_value})


class ProjectReanalysisView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, project_id):
        project = get_object_or_404(Project, id=project_id)
        if not _project_can_edit(request.user, project):
            raise PermissionDenied()
        _require_active_project(project)
        queued = queue_project_reanalysis(
            project, request.user, failed_only=request.data.get("failedOnly", False) is True,
            vision_profile_id=request.data.get("visionProfileId"),
        )
        return Response({"projectId": str(project.id), "queued": queued}, status=status.HTTP_202_ACCEPTED)


class ProjectMergeAdminView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, project_id):
        target_id = request.data.get("targetProjectId")
        reason = str(request.data.get("reasonCode", ""))[:64]
        if not target_id or not reason:
            raise ValidationError({"merge": [{"code": "TARGET_AND_REASON_REQUIRED"}]})
        try:
            merge = merge_projects(project_id, target_id, request.user, reason, str(request.data.get("notes", "")))
        except (Project.DoesNotExist, ValueError) as exc:
            raise ValidationError({"merge": [{"code": str(exc)[:100]}]}) from None
        return Response({"projectMergeId": str(merge.id), "targetProjectId": str(merge.target_project_id)})


class ReprocessDocumentAdminView(APIView):
    permission_classes = [IsAdminUser]

    @transaction.atomic
    def post(self, request, document_id, project_id=None):
        documents = EvidenceDocument.objects.select_for_update()
        if project_id is not None:
            documents = documents.filter(project_id=project_id)
        document = get_object_or_404(documents, id=document_id)
        if not _project_can_edit(request.user, document.project):
            raise PermissionDenied()
        _require_active_project(document.project)
        key, existing = _idempotent_response(request, f"document-reprocess:{document_id}")
        if existing:
            return Response(existing.response_body, status=existing.response_status)
        if OutboxMessage.objects.filter(
            event_type=DOCUMENT_EVENT, payload__documentId=str(document.id),
            published_at__isnull=True, dead_lettered_at__isnull=True,
        ).exists():
            raise ValidationError({"document": [{"code": "ALREADY_QUEUED"}]})
        if document.status not in ["quarantined", "extraction_failed", "needs_review", "approved"]:
            raise ValidationError({"document": [{"code": "NOT_REPROCESSABLE"}]})
        extractor_version = str(request.data.get("extractorVersion", "extractor-1.0"))[:64]
        reason = str(request.data.get("reasonCode", ""))[:64]
        if not reason:
            raise ValidationError({"reasonCode": [{"code": "REQUIRED"}]})
        selection = _requested_vision_selection(request.data)
        document.status = "quarantined" if document.scan_status != "clean" else "extraction_failed"
        document.error_category = ""
        document.save(update_fields=["status", "error_category", "updated_at"])
        body = {
            "documentId": str(document.id),
            "status": document.status,
            **_queue_document_processing(
                document, request.user, request.correlation_id, selection,
                extractor_version=extractor_version, job_status="queued",
            ),
        }
        _save_idempotency(request.user, key, f"document-reprocess:{document_id}", status.HTTP_202_ACCEPTED, body)
        audit(request, "document.reprocessing_requested", document, reason_code=reason, metadata={"extractorVersion": extractor_version})
        return Response(body, status=status.HTTP_202_ACCEPTED)


class ReprocessDeveloperDocumentView(ReprocessDocumentAdminView):
    permission_classes = [IsAuthenticated]


class ReplayOutboxAdminView(APIView):
    permission_classes = [IsAdminUser]

    @transaction.atomic
    def post(self, request, message_id):
        message = get_object_or_404(OutboxMessage, id=message_id)
        reason = str(request.data.get("reasonCode", ""))[:64]
        if not reason:
            raise ValidationError({"reasonCode": [{"code": "REQUIRED"}]})
        replay = OutboxMessage.objects.create(
            event_type=message.event_type,
            schema_version=message.schema_version,
            payload=message.payload,
            correlation_id=request.correlation_id,
        )
        audit(request, "outbox.replayed", replay, reason_code=reason, metadata={"originalMessageId": str(message.id)})
        return Response({"messageId": str(replay.id), "status": "pending"}, status=status.HTTP_202_ACCEPTED)


class LiveHealthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"status": "healthy"})


class ReadyHealthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return Response({"status": "ready"})
