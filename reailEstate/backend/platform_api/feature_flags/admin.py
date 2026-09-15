"""Opt-in ModelAdmin. The main admin module owns registration (no decorator here)."""

from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError
from django.http import HttpResponse

from ..models import FeatureFlag
from .management import (
    FeatureFlagConflict, FeatureFlagUnavailable, can_manage, create_flag, update_flag, validate_mutation,
)
from .registry import REGISTRY
from .validation import FlagValidationError, active_environment, validate_version


class FeatureFlagForm(forms.ModelForm):
    expected_version = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)
    targeting_rules = forms.JSONField(required=False, initial=dict)

    class Meta:
        model = FeatureFlag
        fields = ("key", "environment", "enabled", "targeting_rules")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance._state.adding and "expected_version" in self.fields:
            self.fields["expected_version"].required = True
            self.initial["expected_version"] = self.instance.version
        elif self.instance._state.adding:
            if "key" in self.fields:
                self.fields["key"] = forms.ChoiceField(choices=[("", "---------")] + [
                    (key, f"{key} — {definition.description}") for key, definition in REGISTRY.items()
                ])
            if "environment" in self.fields:
                self.fields["environment"].initial = active_environment()
                self.fields["environment"].widget = forms.HiddenInput()
                self.initial["environment"] = active_environment()

    def clean(self):
        cleaned = super().clean()
        existing = None if self.instance._state.adding else self.instance
        if self.errors:
            return cleaned
        try:
            # Even forged POST fields excluded by ModelAdmin cannot change identity/version.
            if "version" in self.data:
                raise FlagValidationError("immutable_version")
            if existing is not None:
                for name in ("key", "environment"):
                    if name in self.data and self.data[name] != getattr(existing, name):
                        raise FlagValidationError("immutable_identity")
                expected = validate_version(cleaned.get("expected_version"))
                if expected != existing.version:
                    raise FlagValidationError("version_conflict_reload")
            rules = cleaned.get("targeting_rules")
            if rules is None and not self.data.get("targeting_rules"):
                rules = {}
            cleaned["targeting_rules"] = validate_mutation(
                key=cleaned.get("key", existing.key if existing is not None else None),
                environment=cleaned.get("environment", existing.environment if existing is not None else None),
                enabled=cleaned.get("enabled"), targeting_rules=rules, instance=existing,
            )
        except FlagValidationError as exc:
            raise forms.ValidationError(exc.code) from None
        return cleaned


class FeatureFlagAdmin(admin.ModelAdmin):
    form = FeatureFlagForm
    list_display = ("key", "environment", "enabled", "version", "registered")
    list_filter = ("environment", "enabled")
    search_fields = ("key",)
    ordering = ("environment", "key")
    readonly_fields = ("version", "created_at", "updated_at")
    actions = None
    save_as = False

    @admin.display(boolean=True, description="Registered")
    def registered(self, obj):
        return obj.key in REGISTRY

    def has_view_permission(self, request, obj=None):
        return can_manage(request.user, "view") or can_manage(request.user, "change")

    def has_add_permission(self, request):
        try:
            active_environment()
        except FlagValidationError:
            return False
        return can_manage(request.user, "add")

    def has_change_permission(self, request, obj=None):
        if not can_manage(request.user, "change"):
            return False
        try:
            environment = active_environment()
        except FlagValidationError:
            return False
        return obj is None or (obj.key in REGISTRY and obj.environment == environment)

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        fields = tuple(super().get_readonly_fields(request, obj))
        if obj is not None:
            fields += ("key", "environment")
            if not self.has_change_permission(request, obj):
                fields += ("enabled", "targeting_rules")
        return fields

    def get_fields(self, request, obj=None):
        fields = ["key", "environment", "enabled", "targeting_rules", "version", "created_at", "updated_at"]
        if obj is None or self.has_change_permission(request, obj):
            fields.append("expected_version")
        return fields

    def save_model(self, request, obj, form, change):
        if change:
            saved = update_flag(obj.pk, actor=request.user,
                                expected_version=form.cleaned_data.get("expected_version"),
                                changes={"key": obj.key, "environment": obj.environment,
                                         "enabled": obj.enabled, "targeting_rules": obj.targeting_rules},
                                correlation_id=getattr(request, "correlation_id", None))
        else:
            saved = create_flag(key=obj.key, environment=obj.environment, enabled=obj.enabled,
                                targeting_rules=obj.targeting_rules, actor=request.user,
                                correlation_id=getattr(request, "correlation_id", None))
        # Django continues with this instance for its own admin log and redirect.
        obj.__dict__.update(saved.__dict__)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        try:
            return super().changeform_view(request, object_id, form_url, extra_context)
        except FeatureFlagConflict:
            return HttpResponse("Feature flag changed. Reload before retrying.", status=409)
        except FlagValidationError:
            return HttpResponse("Invalid feature flag change. Reload before retrying.", status=400)
        except (FeatureFlagUnavailable, DatabaseError):
            return HttpResponse("Feature flag storage is unavailable. No change was saved.", status=503)

    def delete_model(self, request, obj):
        raise PermissionDenied("Disable the flag instead; deletion is not supported.")

    def delete_queryset(self, request, queryset):
        raise PermissionDenied("Disable the flag instead; deletion is not supported.")