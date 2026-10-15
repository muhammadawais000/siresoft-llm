from django.contrib.auth.views import LogoutView
from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.AccountsLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("signup/", views.SignupView.as_view(), name="signup"),
    path("activate/<str:uidb64>/<str:token>/", views.ActivateAccountView.as_view(), name="activate"),
    path("account/status/", views.AccountStatusView.as_view(), name="status"),
]
