"""Read-only compatibility links for retired admin project editors."""
from urllib.parse import urlencode
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseNotAllowed, HttpResponseRedirect
from django.urls import reverse
from .models import ProjectMerge


def workspace_url(project, **selection):
    project_id = ProjectMerge.objects.filter(source_project=project, state='applied').values_list('target_project_id', flat=True).first() or project.pk
    return reverse('admin-project-workspace') + '?' + urlencode({
        'organization': str(project.organization_id), 'project': str(project_id), **selection})


def workspace_redirect(model_admin, request, object_id):
    obj = model_admin.get_object(request, str(object_id))
    if obj is None:
        raise Http404
    if not model_admin.has_view_or_change_permission(request, obj):
        raise PermissionDenied
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    project = obj if model_admin.model._meta.model_name == 'project' else obj.project
    section = request.GET.get('section') or ('floor-plans' if getattr(obj, 'plan_scope', '') == 'floor' else 'layouts')
    if section not in {'layouts', 'floor-plans', 'amenities', 'project-images', 'sources'}:
        section = 'layouts'
    return HttpResponseRedirect(workspace_url(project, section=section,
        **({"returnFilters": request.GET["_changelist_filters"]} if request.GET.get("_changelist_filters") else {}),
        **({'plan': str(obj.pk)} if obj is not project else {})))
