from rest_framework.routers import DefaultRouter

from django.urls import include, path

from .views import ChatSessionViewSet, ModelListView, SendMessageView

app_name = "chat"

router = DefaultRouter()
router.register("sessions", ChatSessionViewSet, basename="session")

urlpatterns = [
    path("models/", ModelListView.as_view(), name="models"),
    path("sessions/<int:session_id>/messages/", SendMessageView.as_view(), name="send-message"),
    path("", include(router.urls)),
]
