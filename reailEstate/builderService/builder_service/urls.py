from config.service_urls import service_urlpatterns

urlpatterns = service_urlpatterns("builder_service.api_urls", frontend=True)
