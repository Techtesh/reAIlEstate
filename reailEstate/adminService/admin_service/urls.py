from config.service_urls import service_urlpatterns

urlpatterns = service_urlpatterns("admin_service.api_urls", django_admin=True)
