"""
config/urls.py
"""

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("config.api_urls")),
    path("webhooks/", include("apps.webhooks.urls")),
    path("api-auth/", include("rest_framework.urls")),
]
