from rest_framework.routers import DefaultRouter

from django.urls import include, path

from .views import DocumentUploadFilesView, DocumentUploadURLView, DocumentViewSet

app_name = "documents"

router = DefaultRouter()
router.register("", DocumentViewSet, basename="document")

urlpatterns = [
    path("upload/files/", DocumentUploadFilesView.as_view(), name="upload-files"),
    path("upload/url/", DocumentUploadURLView.as_view(), name="upload-url"),
    path("", include(router.urls)),
]
