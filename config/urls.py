"""Root URL configuration.

Per-app API routes are included from each app's own urls.py. The root path
serves the single-page app shell (core.views.IndexView); everything else
is driven client-side against the DRF/SSE API below.
"""

from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from core.views import IndexView, SiresoftLoginView

urlpatterns = [
    path("", IndexView.as_view(), name="index"),
    path("login/", SiresoftLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
    path("api/documents/", include("documents.urls")),
    path("api/rag/", include("rag.urls")),
    path("api/chat/", include("chat.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]
