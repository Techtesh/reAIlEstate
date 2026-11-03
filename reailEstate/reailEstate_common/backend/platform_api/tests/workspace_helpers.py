"""HTTP helpers for tests migrated from retired Django admin editors."""
from pathlib import Path
from uuid import uuid4
from django.test import override_settings

PREFIX = '/api/v1/admin/project-workspace/builder'


def workspace_request(client, project, suffix='', payload=None, *, method='get'):
    path = f'{PREFIX}/projects/{project.pk}{suffix}'
    with override_settings(ROOT_URLCONF='config.urls'):
        if payload is None:
            return client.get(path)
        return getattr(client, method)(path, payload, content_type='application/json')


def media_action(client, plan, action, **values):
    payload = {'action': action, 'targets': [{'planId': str(plan.pk), 'expectedUpdatedAt': values.pop('version', plan.updated_at.isoformat())}],
        'confirm': True, 'requestId': str(uuid4()), **values}
    return workspace_request(client, plan.project, '/media/actions', payload, method='post')


def frontend_source(name='BuilderMediaWorkspace.tsx'):
    return (Path(__file__).resolve().parents[3] / 'frontend/src' / name).read_text(encoding='utf-8')
