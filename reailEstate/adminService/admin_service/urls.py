from config.admin_frontend_urls import frontend_urls
from config.service_urls import service_urlpatterns

urlpatterns = [
    *frontend_urls,
    *service_urlpatterns("admin_service.api_urls"),
]
