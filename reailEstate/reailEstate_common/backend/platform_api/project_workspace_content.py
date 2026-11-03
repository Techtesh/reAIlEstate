"""Shared amenities, external references and owner-scoped draft cart APIs."""
import hashlib
import json
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.db.models import Count
from django.conf import settings
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from .builder_views import BuilderView, BuilderConflict, StrictInput, builder_projects, locked_project, require_new_review
from .builder_media import scoped_project
from .external_sources import GoogleProjectMaps, registry_lookup
from .models import IdempotencyRecord, LayoutCropDraft
from .project_amenities import add_manual_amenities
from .project_sources import RegistryEvidenceForm, commit_references
from .services import audit


class ContentInput(StrictInput):
    action = serializers.ChoiceField(choices=['search', 'registry-lookup', 'submit', 'amenities'])
    expectedUpdatedAt = serializers.DateTimeField()
    requestId = serializers.UUIDField()
    confirm = serializers.BooleanField(default=False)
    query = serializers.CharField(max_length=500, min_length=3, required=False)
    placeId = serializers.CharField(max_length=512, required=False)
    registry = serializers.JSONField(required=False)
    text = serializers.CharField(max_length=3000, required=False)


class ProjectContentView(BuilderView):
    def get(self, request, project_id):
        project = scoped_project(request.user, project_id, workspace=self.workspace)
        return Response({
            'updatedAt': project.updated_at,
            'mapsConfigured': bool(getattr(settings, 'GOOGLE_MAPS_API_KEY', '')),
            'registryConfigured': bool(getattr(settings, 'PROJECT_ENRICHMENT_BACKEND', '')),
            'references': [{'id': str(t.pk), 'state': t.resolution.get('reviewState', 'unreviewed'),
                'placeId': t.resolution.get('googlePlaceId'), 'registry': t.resolution.get('maharera')}
                for t in project.review_tasks.filter(task_type='external_sources').order_by('-created_at')[:20]],
            'amenities': [{'name': c.amenity.display_name, 'state': c.evidence_summary.get('reviewState', 'unreviewed')}
                for c in project.project_amenities.select_related('amenity').all()],
        })

    def post(self, request, project_id):
        data = ContentInput(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        fingerprint = hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()
        scope = f"project-content:{project_id}"
        # Scope the project before checking replay records to avoid exposing revoked access.
        scoped_project(request.user, project_id, edit=True, workspace=self.workspace)
        with transaction.atomic():
            get_object_or_404(builder_projects(request.user, workspace=self.workspace).select_for_update(of=("self",)), pk=project_id)
            existing = IdempotencyRecord.objects.filter(user=request.user, scope=scope, key=str(values['requestId'])).first()
            if existing:
                if existing.response_body['fingerprint'] != fingerprint:
                    raise BuilderConflict('This request ID was used for different content.')
                return Response(existing.response_body['result'], status=existing.response_status)
            project = locked_project(request.user, project_id, values['expectedUpdatedAt'], workspace=self.workspace)
            action = values['action']
            try:
                if action == 'search':
                    if not values.get('query'):
                        raise ValueError('Enter a project name and address.')
                    return Response({'places': GoogleProjectMaps().search(values['query'])})
                if action == 'registry-lookup':
                    return Response({'registry': registry_lookup(project.maha_rera_id)})
                if not values['confirm']:
                    raise ValueError('Confirm submitting these claims for review.')
                if action == 'amenities':
                    codes = add_manual_amenities(project, request.user, values.get('text', ''))
                    if not codes:
                        raise ValueError('No recognized amenity names were found.')
                    require_new_review(project)
                    project.save(update_fields=['status', 'updated_at'])
                    result = {'amenities': codes}
                else:
                    return self.submit_sources(request, project, values, fingerprint, scope)
                return self.saved_response(request, values, fingerprint, scope, result)
            except ValueError as exc:
                raise ValidationError({'detail': str(exc)}) from exc

    def submit_sources(self, request, project, values, fingerprint, scope):
        try:
            draft = {'projectVersion': project.updated_at.isoformat()}
            if values.get('placeId'):
                draft['selected'] = GoogleProjectMaps().details(values['placeId'])
            if 'registry' in values:
                if not isinstance(values['registry'], dict):
                    raise ValueError('Registry evidence must be an object.')
                evidence = RegistryEvidenceForm(values['registry'])
                if not evidence.is_valid():
                    raise ValidationError(evidence.errors)
                evidence = evidence.cleaned_data
                if evidence['registration_number'] != project.maha_rera_id:
                    raise ValueError('Registry evidence must match this project registration number.')
                draft['registry'] = {'registrationNumber': evidence['registration_number'],
                    'sourceUrl': evidence['source_url'], 'checkedOn': evidence['checked_on'].isoformat(),
                    'evidence': evidence['evidence'], 'evidenceMethod': 'manual_website', 'status': 'unreviewed'}
            task = commit_references(project, request.user, draft, str(values['requestId']))
            audit(request, 'project.sources_submitted', project)
            return self.saved_response(request, values, fingerprint, scope, {'referenceId': str(task.pk), 'state': 'unreviewed'})
        except ValueError as exc:
            raise ValidationError({'detail': str(exc)}) from exc

    def saved_response(self, request, values, fingerprint, scope, result):
        IdempotencyRecord.objects.create(user=request.user, scope=scope, key=str(values['requestId']),
            response_status=201, response_body={'fingerprint': fingerprint, 'result': result})
        return Response(result, status=201)


class ProjectCropCartView(BuilderView):
    def get(self, request):
        rows = LayoutCropDraft.objects.filter(owner=request.user,
            project__in=builder_projects(request.user, workspace=self.workspace)).values(
                'project_id', 'project__submitted_name', 'project__organization_id').annotate(count=Count('pk')).order_by('project__submitted_name')
        return Response([{'projectId': str(row['project_id']), 'name': row['project__submitted_name'],
            'organizationId': str(row['project__organization_id']), 'count': row['count']} for row in rows])
