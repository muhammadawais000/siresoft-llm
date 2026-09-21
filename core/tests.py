"""Login/auth tests.

Unlike the rest of the test suite (which calls services/functions
directly), these go through Django's test Client -- the behavior under
test (redirects, 401s, session invalidation) only exists at the
HTTP/middleware layer, there's no lower-level function to call instead.
"""

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from core.auth_backends import EmailBackend


def make_user(email="user@example.com", password="s3cret-pass", is_active=True, **kwargs):
    return User.objects.create_user(
        username=email, email=email, password=password, is_active=is_active, **kwargs
    )


class EmailBackendTests(TestCase):
    def setUp(self):
        self.backend = EmailBackend()
        self.user = make_user()

    def test_authenticates_by_email_with_correct_password(self):
        result = self.backend.authenticate(None, username="user@example.com", password="s3cret-pass")
        self.assertEqual(result, self.user)

    def test_email_lookup_is_case_insensitive(self):
        result = self.backend.authenticate(None, username="USER@EXAMPLE.COM", password="s3cret-pass")
        self.assertEqual(result, self.user)

    def test_wrong_password_returns_none(self):
        result = self.backend.authenticate(None, username="user@example.com", password="wrong")
        self.assertIsNone(result)

    def test_unknown_email_returns_none(self):
        result = self.backend.authenticate(None, username="nobody@example.com", password="s3cret-pass")
        self.assertIsNone(result)

    def test_inactive_user_cannot_authenticate_even_with_correct_password(self):
        make_user(email="inactive@example.com", password="s3cret-pass", is_active=False)
        result = self.backend.authenticate(None, username="inactive@example.com", password="s3cret-pass")
        self.assertIsNone(result)


class LoginViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = make_user()

    def test_login_page_is_reachable_without_auth(self):
        resp = self.client.get(reverse("login"))
        self.assertEqual(resp.status_code, 200)

    def test_correct_credentials_log_in_and_redirect(self):
        resp = self.client.post(reverse("login"), {"username": "user@example.com", "password": "s3cret-pass"})
        self.assertRedirects(resp, "/")
        self.assertTrue(resp.wsgi_request.user.is_authenticated)

    def test_wrong_password_shows_form_error_without_logging_in(self):
        resp = self.client.post(reverse("login"), {"username": "user@example.com", "password": "wrong"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["form"].errors)

    def test_deactivated_user_cannot_log_in(self):
        make_user(email="inactive@example.com", password="s3cret-pass", is_active=False)
        resp = self.client.post(
            reverse("login"), {"username": "inactive@example.com", "password": "s3cret-pass"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["form"].errors)


class RequireActiveLoginMiddlewareTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = make_user()

    def test_anonymous_page_request_redirects_to_login(self):
        resp = self.client.get("/")
        self.assertRedirects(resp, "/login/")

    def test_anonymous_api_request_gets_401_json(self):
        resp = self.client.get("/api/documents/")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json(), {"detail": "Authentication required."})

    def test_authenticated_active_user_can_reach_the_app(self):
        self.client.login(username="user@example.com", password="s3cret-pass")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_deactivating_a_user_logs_them_out_on_their_next_request(self):
        self.client.login(username="user@example.com", password="s3cret-pass")
        self.assertEqual(self.client.get("/").status_code, 200)

        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        resp = self.client.get("/", follow=False)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/login/")

        # The session was actually torn down (logout()), not just
        # re-checked and left alive -- a follow-up request has no session
        # to fall back on, so it's still anonymous rather than briefly
        # authenticated again.
        second = self.client.get("/api/documents/")
        self.assertEqual(second.status_code, 401)


class ForceLogoutAndDeactivateActionTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="admin@example.com", email="admin@example.com", password="admin-pass"
        )
        self.client = Client()
        self.client.login(username="admin@example.com", password="admin-pass")
        self.target = make_user(email="target@example.com")

    def test_action_deactivates_selected_users(self):
        resp = self.client.post(
            reverse("admin:auth_user_changelist"),
            {
                "action": "force_logout_and_deactivate",
                "_selected_action": [str(self.target.pk)],
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)
