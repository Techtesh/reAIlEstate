from config.service_urls import service_urlpatterns

urlpatterns = service_urlpatterns("buyer_service.api_urls", frontend=True)
