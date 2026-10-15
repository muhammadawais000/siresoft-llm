"""Stand-alone URLconf so the accounts tests don't depend on the host
project's URLs: the accounts pages, the admin, a protected page at "/"
and a protected API endpoint."""

from django.contrib import admin
from django.http import HttpResponse, JsonResponse
from django.urls import include, path

urlpatterns = [
    path("", lambda request: HttpResponse("home")),
    path("api/ping/", lambda request: JsonResponse({"ok": True})),
    path("", include("accounts.urls")),
    path("admin/", admin.site.urls),
]
