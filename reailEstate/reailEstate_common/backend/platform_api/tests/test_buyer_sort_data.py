from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APIClient

from platform_api.models import Organization, Project, ProjectFact, RecommendationSnapshot, RequirementProfile, RequirementVersion


@pytest.mark.django_db
def test_public_project_list_exposes_only_reviewed_possession_date():
    project = Project.objects.create(
        organization=Organization.objects.create(legal_name="Sort test builder"),
        submitted_name="Possession project", status="published", published_fact_set_version=2,
    )
    for version, status, code, value in [(1, "published", "possession_date", "2026-01-01"), (2, "draft", "possession", "2027-01-01"), (2, "published", "possession_date", "2028-12-31")]:
        ProjectFact.objects.create(
            project=project, field_code=code, typed_value=value,
            source_class="maharera", status=status, fact_set_version=version,
            valid_from=timezone.now(),
        )
    response = APIClient().get("/api/v1/projects/")
    assert response.status_code == 200
    assert response.json()["results"][0]["possessionDate"] == "2028-12-31"


@pytest.mark.django_db
def test_latest_match_sort_source_is_owner_scoped():
    owner = User.objects.create_user(username="sort-owner")
    other = User.objects.create_user(username="sort-other")
    client = APIClient()
    client.force_authenticate(owner)
    assert client.get("/api/v1/recommendation-requests/").json() == {"latest": None}
    for user, offset in [(owner, 2), (other, 0), (owner, 1)]:
        profile = RequirementProfile.objects.create(owner=user)
        version = RequirementVersion.objects.create(profile=profile, version=1, payload={})
        RecommendationSnapshot.objects.create(
            requirement_version=version, state="ready", input_catalog_version="test",
            generated_at=timezone.now() - timedelta(days=offset),
        )
    response = client.get("/api/v1/recommendation-requests/")
    assert response.status_code == 200
    assert response.json()["latest"]["generatedAt"] is not None
    assert response.json()["latest"]["id"] == str(
        RecommendationSnapshot.objects.filter(requirement_version__profile__owner=owner)
        .order_by("-generated_at").first().id
    )
    assert APIClient().get("/api/v1/recommendation-requests/").status_code == 403
