"""
apps/common/pagination.py
─────────────────────────
Standard pagination class used across all listing APIs.
"""

from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """
    Standard pagination for all listing endpoints.
    - Default page size: 25
    - Client-customizable page size: ?page_size=N (up to 1000)
    - Client-customizable page number: ?page=N
    - Export all parameter: ?export_all=true (or ?export=all or ?all=true)
      bypasses pagination and returns all records without pagination envelope.
    """
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 1000

    def paginate_queryset(self, queryset, request, view=None):
        export_all = (
            request.query_params.get("export_all", "").lower() in ("true", "1", "yes")
            or request.query_params.get("export", "").lower() in ("all", "true")
            or request.query_params.get("all", "").lower() in ("true", "1")
        )
        if export_all:
            return None
        return super().paginate_queryset(queryset, request, view=view)
