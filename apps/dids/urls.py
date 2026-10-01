from django.urls import path
from apps.dids.views import (
    DIDDetailView,
    DIDListView,
    DepartmentListCreateView,
    DepartmentDetailView,
    AccountListCreateView,
    AccountDetailView,
    DivisionListCreateView,
    DivisionDetailView,
    UserDIDDivisionAssignmentListCreateView,
    UserDIDDivisionAssignmentDetailView,
    AccessGroupListCreateView,
    AccessGroupDetailView,
    AccessGroupEntryListCreateView,
    AccessGroupEntryDetailView,
    TLGroupAccessListCreateView,
    TLGroupAccessDetailView,
    MyLogAccessRosterView,
)

urlpatterns = [
    path("", DIDListView.as_view(), name="did-list"),
    path("<uuid:id>/", DIDDetailView.as_view(), name="did-detail"),

    path("departments/", DepartmentListCreateView.as_view(), name="department-list"),
    path("departments/<uuid:id>/", DepartmentDetailView.as_view(), name="department-detail"),

    path("accounts/", AccountListCreateView.as_view(), name="account-list"),
    path("accounts/<uuid:id>/", AccountDetailView.as_view(), name="account-detail"),

    path("divisions/", DivisionListCreateView.as_view(), name="division-list"),
    path("divisions/<uuid:id>/", DivisionDetailView.as_view(), name="division-detail"),

    path(
        "division-assignments/",
        UserDIDDivisionAssignmentListCreateView.as_view(),
        name="user-did-division-assignment-list",
    ),
    path(
        "division-assignments/<uuid:id>/",
        UserDIDDivisionAssignmentDetailView.as_view(),
        name="user-did-division-assignment-detail",
    ),

    path("access-groups/", AccessGroupListCreateView.as_view(), name="access-group-list"),
    path("access-groups/<uuid:id>/", AccessGroupDetailView.as_view(), name="access-group-detail"),
    path(
        "access-groups/<uuid:group_id>/entries/",
        AccessGroupEntryListCreateView.as_view(),
        name="access-group-entry-list",
    ),
    path(
        "access-groups/<uuid:group_id>/entries/<uuid:id>/",
        AccessGroupEntryDetailView.as_view(),
        name="access-group-entry-detail",
    ),

    path("tl-group-access/", TLGroupAccessListCreateView.as_view(), name="tl-group-access-list"),
    path("tl-group-access/<uuid:id>/", TLGroupAccessDetailView.as_view(), name="tl-group-access-detail"),

    path("my-log-access-roster/", MyLogAccessRosterView.as_view(), name="my-log-access-roster"),
]
