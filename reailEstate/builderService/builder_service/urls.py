from django.urls import re_path

from config.buyer_frontend import index
from config.service_urls import service_urlpatterns

urlpatterns = service_urlpatterns("builder_service.api_urls")
urlpatterns.append(re_path(r"^(?!api/|admin/|health/|static/).*$", index, name="service-frontend"))
