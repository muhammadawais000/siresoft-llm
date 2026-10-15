from django.contrib.messages import get_messages
from django.core import mail
from django.urls import reverse

from accounts.backends import EmailBackend
from accounts.tests.base import PASSWORD, AccountsTestCase, User, make_user


class EmailBackendTests(AccountsTestCase):
    def setUp(self):
        self.backend = EmailBackend()
        self.user = make_user()

    def test_authenticates_by_email_case_insensitively(self):
        self.assertEqual(self.backend.authenticate(None, username="USER@example.com", password=PASSWORD), self.user)

    def test_wrong_password_or_unknown_email_returns_none(self):
        self.assertIsNone(self.backend.authenticate(None, username="user@example.com", password="wrong"))
        self.assertIsNone(self.backend.authenticate(None, username="nobody@example.com", password=PASSWORD))

    def test_inactive_user_cannot_authenticate(self):
        make_user(email="off@example.com", is_active=False)
        self.assertIsNone(self.backend.authenticate(None, username="off@example.com", password=PASSWORD))


class LoginTests(AccountsTestCase):
    def setUp(self):
        make_user()

    def test_correct_credentials_sign_in(self):
        resp = self.client.post(reverse("accounts:login"), {"username": "user@example.com", "password": PASSWORD})
        self.assertRedirects(resp, "/")

    def test_wrong_password_shows_generic_error(self):
        resp = self.client.post(reverse("accounts:login"), {"username": "user@example.com", "password": "wrong"})
        self.assertEqual(resp.context["form"].non_field_errors()[0], "Invalid email or password.")

    def test_page_uses_configured_site_name_and_links_to_signup(self):
        resp = self.client.get(reverse("accounts:login"))
        self.assertContains(resp, "Test Site")
        self.assertContains(resp, reverse("accounts:signup"))


class LoginRequiredMiddlewareTests(AccountsTestCase):
    def setUp(self):
        self.user = make_user()

    def test_anonymous_page_redirects_and_api_gets_401(self):
        self.assertRedirects(self.client.get("/"), reverse("accounts:login"), fetch_redirect_response=False)
        resp = self.client.get("/api/ping/")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json(), {"detail": "Authentication required."})

    def test_deactivated_user_is_logged_out_with_a_reason(self):
        self.client.login(username="user@example.com", password=PASSWORD)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.user.is_active = False
        self.user.save()

        self.assertEqual(self.client.get(reverse("accounts:status")).status_code, 401)
        self.assertContains(self.client.get(reverse("accounts:login")), "admin@example.com")
        self.assertEqual(self.client.get("/api/ping/").status_code, 401)

    def test_login_not_required_setting_lets_visitors_through(self):
        with self.settings(ACCOUNTS={"LOGIN_REQUIRED": False}):
            self.assertEqual(self.client.get("/").status_code, 200)


class SignupTests(AccountsTestCase):
    def _signup(self, email="new@example.com", p1="Str0ng-pass-123", p2="Str0ng-pass-123"):
        return self.client.post(reverse("accounts:signup"), {"email": email, "password1": p1, "password2": p2})

    def _activation_path(self):
        line = next(l for l in mail.outbox[-1].body.splitlines() if "/activate/" in l)
        return line.replace("http://testserver", "")

    def test_signup_creates_inactive_time_limited_account_and_emails_link(self):
        self.assertRedirects(self._signup(), reverse("accounts:login"), fetch_redirect_response=False)
        user = User.objects.get(email="new@example.com")
        self.assertFalse(user.is_active)
        self.assertEqual(user.access.access_hours, 24)
        self.assertFalse(user.access.email_verified)
        self.assertEqual(mail.outbox[0].subject, "Your Test Site account has been created")

    def test_mismatched_passwords_and_existing_email_are_rejected(self):
        self.assertIn("password2", self._signup(p2="Other-pass-456").context["form"].errors)
        make_user(email="taken@example.com")
        self.assertIn("email", self._signup(email="taken@example.com").context["form"].errors)

    def test_activation_link_turns_account_on_and_lands_on_login(self):
        self._signup()
        self.assertRedirects(self.client.get(self._activation_path()), reverse("accounts:login"), fetch_redirect_response=False)
        user = User.objects.get(email="new@example.com")
        self.assertTrue(user.is_active)
        self.assertIsNotNone(user.access.access_starts_at)

        resp = self.client.post(reverse("accounts:login"), {"username": "new@example.com", "password": "Str0ng-pass-123"})
        self.assertIn("of usage on your account", [str(m) for m in get_messages(resp.wsgi_request)][-1])

    def test_used_activation_link_cannot_reactivate_a_deactivated_account(self):
        self._signup()
        path = self._activation_path()
        self.client.get(path)
        User.objects.filter(email="new@example.com").update(is_active=False)
        self.client.get(path)
        self.assertFalse(User.objects.get(email="new@example.com").is_active)

    def test_signup_can_be_disabled(self):
        with self.settings(ACCOUNTS={"SIGNUP_ENABLED": False}):
            self.assertEqual(self.client.get(reverse("accounts:signup")).status_code, 404)
            self.assertNotContains(self.client.get(reverse("accounts:login")), reverse("accounts:signup"))

    def test_messages_can_be_overridden(self):
        with self.settings(ACCOUNTS={"SITE_NAME": "Acme", "MESSAGES": {"login_heading": "Hello {site_name}"}}):
            self.assertContains(self.client.get(reverse("accounts:login")), "Hello Acme")
