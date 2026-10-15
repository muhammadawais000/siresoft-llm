from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import UserAccess

User = get_user_model()

PASSWORD = "s3cret-pass"

TEST_SETTINGS = dict(
    ROOT_URLCONF="accounts.tests.urls",
    LOGIN_URL="accounts:login",
    LOGIN_REDIRECT_URL="/",
    LOGOUT_REDIRECT_URL="accounts:login",
    AUTHENTICATION_BACKENDS=["accounts.backends.EmailBackend", "django.contrib.auth.backends.ModelBackend"],
    MIDDLEWARE=[
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.common.CommonMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "django.contrib.messages.middleware.MessageMiddleware",
        "accounts.middleware.LoginRequiredMiddleware",
    ],
    ACCOUNTS={"SITE_NAME": "Test Site", "ADMIN_CONTACT_EMAIL": "admin@example.com"},
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)


@override_settings(**TEST_SETTINGS)
class AccountsTestCase(TestCase):
    pass


def make_user(email="user@example.com", password=PASSWORD, is_active=True, **kwargs):
    return User.objects.create_user(username=email, email=email, password=password, is_active=is_active, **kwargs)


def make_timed_user(email="timed@example.com", hours=24, started_hours_ago=1, is_active=True):
    user = make_user(email=email, is_active=is_active)
    UserAccess.objects.create(
        user=user,
        access_hours=hours,
        email_verified=True,
        access_starts_at=timezone.now() - timedelta(hours=started_hours_ago),
    )
    return user
