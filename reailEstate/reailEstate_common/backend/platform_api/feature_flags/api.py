"""Drop-in admin endpoint adapter; no operations or authorization gates are wired."""

import re

from django.db import DatabaseError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, MethodNotAllowed, ValidationError
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from ..models import FeatureFlag
from .management import (
    FeatureFlagConflict, FeatureFlagUnavailable, can_manage, disable_flag,
)
from .serializers import FeatureFlagSerializer
from .validation import FlagValidationError, active_environment, validate_version


class FlagAPIError(APIException):
    def __init__(self, code, response_status):
        self.status_code = response_status
        super().__init__(detail={"code": code}, code=code)


def parse_if_match(value: str | None) -> int:
    if value is None:
        raise FlagAPIError("EXPECTED_VERSION_REQUIRED", 428)
    # Accept the existing API's weak numeric ETags as well as strong/numeric ones;
    # never accept *, lists, malformed quote stripping, zero or negative versions.
    match = re.fullmatch(r'(?:W/"([0-9]+)"|"([0-9]+)"|([0-9]+))', value.strip())
    if match is None:
        raise ValidationError({"ifMatch": "invalid_version"})
    try:
        number = next(group for group in match.groups() if group is not None)
        if len(number) > 10:
            raise FlagValidationError("invalid_version")
        return validate_version(int(number))
    except (FlagValidationError, ValueError):
        raise ValidationError({"ifMatch": "invalid_version"}) from None


class FeatureFlagPermission(BasePermission):
    def has_permission(self, request, view):
        action_name = getattr(view, "action", None)
        required = "add" if action_name == "create" else (
            "change" if action_name in {"update", "partial_update", "disable"} else "view"
        )
        return can_manage(request.user, required)


class FeatureFlagPagination(CursorPagination):
    ordering = ("key", "id")


class FeatureFlagAdminViewSet(viewsets.ModelViewSet):
    queryset = FeatureFlag.objects.all().order_by("key", "id")
    serializer_class = FeatureFlagSerializer
    permission_classes = [FeatureFlagPermission]
    pagination_class = FeatureFlagPagination
    http_method_names = ["get", "post", "put", "patch", "head", "options"]
    ordering = ("key", "id")

    def get_queryset(self):
        return super().get_queryset().filter(environment=active_environment())

    def handle_exception(self, exc):
        if isinstance(exc, FeatureFlagConflict):
            exc = FlagAPIError(exc.code, status.HTTP_409_CONFLICT)
        elif isinstance(exc, (FeatureFlagUnavailable, DatabaseError)):
            exc = FlagAPIError("FEATURE_FLAG_STORAGE_UNAVAILABLE", status.HTTP_503_SERVICE_UNAVAILABLE)
        elif isinstance(exc, FlagValidationError):
            exc = ValidationError({"code": exc.code})
        return super().handle_exception(exc)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    def _response(self, instance, response_status=status.HTTP_200_OK):
        response = Response(self.get_serializer(instance).data, status=response_status)
        response["ETag"] = f'W/"{instance.version}"'
        return response

    def retrieve(self, request, *args, **kwargs):
        return self._response(self.get_object())

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return self._response(serializer.instance, status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        expected = parse_if_match(request.headers.get("If-Match"))
        instance = self.get_object()
        context = self.get_serializer_context() | {"expected_version": expected}
        serializer = self.get_serializer(instance, data=request.data, partial=partial, context=context)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return self._response(serializer.instance)

    @action(detail=True, methods=["post"])
    def disable(self, request, *args, **kwargs):
        expected = parse_if_match(request.headers.get("If-Match"))
        if request.data:
            raise ValidationError("Disable accepts no body fields.")
        instance = self.get_object()
        saved = disable_flag(instance.pk, actor=request.user, expected_version=expected,
                             correlation_id=getattr(request, "correlation_id", None))
        return self._response(saved)

    def destroy(self, request, *args, **kwargs):
        raise MethodNotAllowed("DELETE", detail="Disable the flag instead; deletion is not supported.")