from django.urls import path
from apps.dids.views import (
    DIDDetailView,
    DIDListView,
    DepartmentListCreateView,
    DepartmentDetailView,
    UserDIDDepartmentAssignmentListCreateView,
    UserDIDDepartmentAssignmentDetailView,
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

    path(
        "department-assignments/",
        UserDIDDepartmentAssignmentListCreateView.as_view(),
        name="user-did-department-assignment-list",
    ),
    path(
        "department-assignments/<uuid:id>/",
        UserDIDDepartmentAssignmentDetailView.as_view(),
        name="user-did-department-assignment-detail",
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
