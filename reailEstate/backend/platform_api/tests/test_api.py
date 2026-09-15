from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APIClient

from platform_api.models import Consent, RequirementProfile


@pytest.fixture
def user(db):
    return User.objects.create_user(username="buyer", password="safe-test-password")


@pytest.fixture
def client(user):
    api_client = APIClient()
    api_client.force_authenticate(user)
    return api_client


@pytest.mark.django_db
def test_profile_is_owner_scoped(client, user):
    response = client.post("/api/v1/requirement-profiles/", {}, format="json")
    assert response.status_code == 201
    profile_id = response.json()["id"]

    other = User.objects.create_user(username="other", password="safe-test-password")
    other_client = APIClient()
    other_client.force_authenticate(other)
    assert other_client.get(f"/api/v1/requirement-profiles/{profile_id}/").status_code == 404


@pytest.mark.django_db
def test_profile_update_requires_etag(client):
    created = client.post("/api/v1/requirement-profiles/", {}, format="json")
    profile_id = created.json()["id"]
    response = client.patch(f"/api/v1/requirement-profiles/{profile_id}/", {"budget": {"currency": "INR", "purchaseMaximum": 10000000}}, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_submission_requires_saved_profile_consent(client, user):
    created = client.post("/api/v1/requirement-profiles/", {}, format="json")
    profile = RequirementProfile.objects.get(id=created.json()["id"])
    response = client.post(
        f"/api/v1/requirement-profiles/{profile.id}:submit",
        {},
        format="json",
        HTTP_IF_MATCH='W/"1"',
        HTTP_IDEMPOTENCY_KEY="submit-once",
    )
    assert response.status_code == 400

    Consent.objects.create(user=user, purpose="saved_profile", policy_version="1.0", granted_at=timezone.now())
    assert Consent.objects.filter(user=user, withdrawn_at__isnull=True).exists()
