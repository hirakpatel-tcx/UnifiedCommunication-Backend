from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from apps.users.views import (
    CurrentUserView,
    LoginView,
    LogoutView,
    SipCredentialsView,
    UserDetailView,
    UserDIDView,
    UserExtensionView,
    UserFaxBoxView,
    UserListCreateView,
    UserVoicemailBoxView,
)

# Authentication endpoints: /api/v1/auth/
auth_urlpatterns = [
    path("login/", LoginView.as_view(), name="auth-login"),
    path("logout/", LogoutView.as_view(), name="auth-logout"),
    path("token/refresh/", TokenRefreshView.as_view(), name="auth-token-refresh"),
    path("me/", CurrentUserView.as_view(), name="auth-me"),
]

# User management endpoints: /api/v1/users/
user_urlpatterns = [
    path("", UserListCreateView.as_view(), name="user-list-create"),
    path("<uuid:id>/", UserDetailView.as_view(), name="user-detail"),
    path("<uuid:id>/sip-credentials/", SipCredentialsView.as_view(), name="user-sip-credentials"),
    # Resource assignments
    path("<uuid:id>/extension/", UserExtensionView.as_view(), name="user-extension"),
    path("<uuid:id>/dids/", UserDIDView.as_view(), name="user-dids"),
    path("<uuid:id>/dids/<uuid:did_id>/", UserDIDView.as_view(), name="user-did-delete"),
    path("<uuid:id>/fax-boxes/", UserFaxBoxView.as_view(), name="user-fax-boxes"),
    path("<uuid:id>/fax-boxes/<str:fax_uuid>/", UserFaxBoxView.as_view(), name="user-fax-box-delete"),
    path("<uuid:id>/voicemail-boxes/", UserVoicemailBoxView.as_view(), name="user-voicemail-boxes"),
    path("<uuid:id>/voicemail-boxes/<int:box_id>/", UserVoicemailBoxView.as_view(), name="user-voicemail-box-delete"),
]
