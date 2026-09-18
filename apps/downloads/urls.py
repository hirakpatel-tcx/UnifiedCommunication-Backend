from django.urls import path

from apps.downloads.views import DownloadFetchView, DownloadLinkCreateView

urlpatterns = [
    path("link/", DownloadLinkCreateView.as_view(), name="downloads-link-create"),
    path("fetch/<str:token>/", DownloadFetchView.as_view(), name="downloads-fetch"),
]
