"""Reusable platform permissions and organization access with bulk member selection."""

from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import GroupAdmin
from django.contrib.auth.models import Group, Permission
from django.db import transaction
from django.db.models import Q

from .models import OrganizationRole
from .organization_roles import active_owners, assign_role_members


class PlatformRoleForm(forms.ModelForm):
    members = forms.ModelMultipleChoiceField(queryset=get_user_model().objects.filter(is_active=True),
        required=False, help_text="Assign this permission role to several existing accounts at once.",
        widget=admin.widgets.FilteredSelectMultiple("Members", False))
    grant_admin_login = forms.BooleanField(required=False,
        help_text="Also allow selected members to sign in to the admin site. Their role still determines permissions.")

    class Meta:
        model = Group
        fields = ["name", "permissions"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["members"].initial = self.instance.user_set.all()
        if not self.request.user.is_superuser:
            allowed = Q(pk__in=[])
            for code in self.request.user.get_all_permissions():
                app, codename = code.split(".", 1)
                allowed |= Q(content_type__app_label=app, codename=codename)
            self.fields["permissions"].queryset = Permission.objects.filter(allowed)

    def clean(self):
        values = super().clean()
        members = values.get("members")
        existing = set(self.instance.user_set.values_list("pk", flat=True)) if self.instance.pk else set()
        selected = {member.pk for member in members} if members is not None else set()
        if (selected != existing or values.get("grant_admin_login")) and not self.request.user.has_perm("auth.change_user"):
            raise forms.ValidationError("Changing role membership requires permission to change users.")
        if not self.request.user.is_superuser and members is not None and members.filter(is_superuser=True).exists():
            raise forms.ValidationError("Only a superuser can change superuser roles.")
        return values


class PlatformRoleAdmin(GroupAdmin):
    form = PlatformRoleForm
    fields = ("name", "permissions", "members", "grant_admin_login")

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # Bind request on a per-request subclass, never a shared form class.
        return type("RequestPlatformRoleForm", (form,), {"request": request})

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        if obj and not request.user.is_superuser:
            return all(f"{permission.content_type.app_label}.{permission.codename}" in request.user.get_all_permissions()
                       for permission in obj.permissions.select_related("content_type"))
        return True

    @transaction.atomic
    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.instance.user_set.set(form.cleaned_data["members"])
        if form.cleaned_data["grant_admin_login"]:
            form.cleaned_data["members"].update(is_staff=True)


class OrganizationRoleForm(forms.ModelForm):
    members = forms.ModelMultipleChoiceField(queryset=get_user_model().objects.filter(is_active=True),
        required=False, help_text="Select accounts to onboard with this organization role.",
        widget=admin.widgets.FilteredSelectMultiple("Members", False))

    class Meta:
        model = OrganizationRole
        fields = ["organization", "name", "description", "base_role"]

    def clean(self):
        values = super().clean()
        if self.instance.pk and self.instance.memberships.exists():
            original = OrganizationRole.objects.get(pk=self.instance.pk)
            if values.get("organization") != original.organization or values.get("base_role") != original.base_role:
                raise forms.ValidationError("Create a new role to change organization or access for an assigned role.")
        if values.get("members") and not self.request.user.has_perm("platform_api.change_organizationmembership"):
            raise forms.ValidationError("Onboarding requires permission to change organization memberships.")
        if values.get("members") is not None and len(values["members"]) > 100:
            raise forms.ValidationError("Onboard at most 100 members at a time.")
        members, organization = values.get("members"), values.get("organization")
        if members and organization and values.get("base_role") != "owner":
            owners = active_owners(organization)
            if owners.exists() and not owners.exclude(user__in=members).exists():
                raise forms.ValidationError("Keep at least one active organization owner.")
        return values


@admin.register(OrganizationRole)
class OrganizationRoleAdmin(admin.ModelAdmin):
    form = OrganizationRoleForm
    list_display = ("name", "organization", "base_role")
    list_filter = ("base_role", "organization")
    search_fields = ("name", "organization__legal_name")
    fields = ("organization", "name", "description", "base_role", "members")

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        return type("RequestOrganizationRoleForm", (form,), {"request": request})

    @transaction.atomic
    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if form.cleaned_data["members"]:
            assign_role_members(form.instance.organization, form.instance, list(form.cleaned_data["members"]))


admin.site.unregister(Group)
admin.site.register(Group, PlatformRoleAdmin)
