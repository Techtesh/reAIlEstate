from django.urls import path
from .admin_console import ConsoleLogin, ConsolePassword, ConsoleResource, ConsoleSession, SessionLogoutView
from .admin_console_actions import ConsoleQueue, ConsoleProjectAction, ConsoleTaskAction, ConsoleRoleMembers, ConsoleTransit, ConsoleDocumentRetry, ConsoleReplay, ConsoleBulkProjects

urlpatterns = [
    path("session", ConsoleSession.as_view()),
    path("login", ConsoleLogin.as_view()),
    path("logout", SessionLogoutView.as_view()),
    path("password", ConsolePassword.as_view()),
    path("users/<int:user_id>/password", ConsolePassword.as_view()),
    path("queue", ConsoleQueue.as_view()),
    path("bulk-projects", ConsoleBulkProjects.as_view()),
    path("documents/<uuid:document_id>/retry", ConsoleDocumentRetry.as_view()),
    path("outbox/<uuid:message_id>/replay", ConsoleReplay.as_view()),
    path("projects/<uuid:project_id>/actions", ConsoleProjectAction.as_view()),
    path("projects/<uuid:project_id>/transit", ConsoleTransit.as_view()),
    path("roles/<slug:resource>/<str:role_id>/members", ConsoleRoleMembers.as_view()),
    path("tasks/<uuid:task_id>/actions", ConsoleTaskAction.as_view()),
    path("records/<slug:resource>", ConsoleResource.as_view()),
    path("records/<slug:resource>/<str:object_id>", ConsoleResource.as_view()),
]
