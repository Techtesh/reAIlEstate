"""Validated compatibility serializer; persistence always uses shared mutations."""

from collections.abc import Mapping

from rest_framework import serializers

from ..models import FeatureFlag
from .management import create_flag, update_flag, validate_mutation
from .registry import REGISTRY
from .validation import FlagValidationError, active_environment, validate_configuration, validate_version


class FeatureFlagSerializer(serializers.ModelSerializer):
    registered = serializers.SerializerMethodField()
    editable = serializers.SerializerMethodField()

    class Meta:
        model = FeatureFlag
        fields = ["id", "key", "environment", "enabled", "targeting_rules", "version",
                  "created_at", "updated_at", "registered", "editable"]
        read_only_fields = ["id", "version", "created_at", "updated_at", "registered", "editable"]
        extra_kwargs = {"environment": {"required": False}, "enabled": {"required": False},
                        "targeting_rules": {"required": False}}
        # Uniqueness races are handled transactionally by the shared writer.
        validators = []

    def get_registered(self, obj):
        return obj.key in REGISTRY

    def get_editable(self, obj):
        try:
            return obj.key in REGISTRY and obj.environment == active_environment()
        except FlagValidationError:
            return False

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Historical unknown/malformed JSON must not leak arbitrary stored data.
        try:
            data["targeting_rules"] = validate_configuration(
                instance.key, instance.enabled, instance.targeting_rules,
            )
        except FlagValidationError:
            data["targeting_rules"] = {}
        return data

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError("Expected an object.")
        data = dict(data)
        # The existing global renderer camelizes response fields recursively.
        # Accept that wire format and the historical snake_case request fields,
        # while keeping one canonical stored rules schema.
        if "targetingRules" in data:
            if "targeting_rules" in data:
                raise serializers.ValidationError("duplicate_targeting_rules")
            data["targeting_rules"] = data.pop("targetingRules")
        if isinstance(data.get("targeting_rules"), dict):
            rules = dict(data["targeting_rules"])
            for alias, canonical in (("projectIds", "project_ids"), ("userIds", "user_ids"),
                                     ("organizationIds", "organization_ids")):
                if alias in rules:
                    if canonical in rules:
                        raise serializers.ValidationError("duplicate_targeting_dimension")
                    rules[canonical] = rules.pop(alias)
            data["targeting_rules"] = rules
        allowed = {"key", "environment", "enabled", "targeting_rules"}
        if set(data) - allowed:
            raise serializers.ValidationError("immutable_or_unknown_field")
        # DRF's BooleanField normally coerces strings and integers. This schema
        # deliberately accepts JSON booleans only.
        if "enabled" in data and type(data["enabled"]) is not bool:
            raise serializers.ValidationError({"enabled": "invalid_enabled"})
        if "environment" in data:
            try:
                if data["environment"] != active_environment():
                    raise FlagValidationError("inactive_environment")
            except FlagValidationError as exc:
                raise serializers.ValidationError({"environment": exc.code}) from None
        return super().to_internal_value(data)

    def validate(self, attrs):
        instance = self.instance
        try:
            if instance is None:
                key = attrs.get("key")
                if key not in REGISTRY:
                    raise FlagValidationError("unknown_key")
                attrs.setdefault("environment", active_environment())
                attrs.setdefault("enabled", REGISTRY[key].default_enabled)
                attrs.setdefault("targeting_rules", {})
            else:
                validate_version(self.context.get("expected_version"))
            key = attrs.get("key", instance.key if instance is not None else None)
            environment = attrs.get("environment", instance.environment if instance is not None else None)
            enabled = attrs.get("enabled", instance.enabled if instance is not None else None)
            rules = attrs.get("targeting_rules", instance.targeting_rules if instance is not None else None)
            normalized = validate_mutation(key=key, environment=environment, enabled=enabled,
                                           targeting_rules=rules, instance=instance)
            if "targeting_rules" in attrs:
                attrs["targeting_rules"] = normalized
        except FlagValidationError as exc:
            raise serializers.ValidationError(exc.code) from None
        return attrs

    def _actor(self):
        request = self.context.get("request")
        return getattr(request, "user", self.context.get("actor"))

    def _correlation(self):
        return getattr(self.context.get("request"), "correlation_id", None)

    def create(self, validated_data):
        return create_flag(**validated_data, actor=self._actor(), correlation_id=self._correlation())

    def update(self, instance, validated_data):
        return update_flag(instance.pk, actor=self._actor(), changes=validated_data,
                           expected_version=self.context.get("expected_version"),
                           correlation_id=self._correlation())