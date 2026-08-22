from django.urls import path

from .views import RetrievalSearchView

app_name = "rag"

urlpatterns = [
    path("search/", RetrievalSearchView.as_view(), name="search"),
]
