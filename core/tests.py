"""Login/auth tests.

Unlike the rest of the test suite (which calls services/functions
directly), these go through Django's test Client -- the behavior under
test (redirects, 401s, session invalidation) only exists at the
HTTP/middleware layer, there's no lower-level function to call instead.
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from chat.models import ChatMessage, ChatSession
from core.access import apply_admin_access_change
from core.auth_backends import EmailBackend
from core.models import UserAccess
from core.tasks import expire_user_access


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


class SignupTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _signup(self, email="new@example.com", p1="Str0ng-pass-123", p2="Str0ng-pass-123"):
        return self.client.post(reverse("signup"), {"email": email, "password1": p1, "password2": p2})

    def test_signup_page_is_reachable_without_auth_and_linked_from_login(self):
        self.assertEqual(self.client.get(reverse("signup")).status_code, 200)
        self.assertContains(self.client.get(reverse("login")), reverse("signup"))

    def test_signup_creates_inactive_time_limited_account_and_emails_link(self):
        resp = self._signup()
        self.assertRedirects(resp, reverse("login"), fetch_redirect_response=False)

        user = User.objects.get(email="new@example.com")
        self.assertFalse(user.is_active)
        self.assertTrue(user.check_password("Str0ng-pass-123"))
        self.assertEqual(user.access.access_hours, 24)
        self.assertFalse(user.access.email_verified)
        self.assertIsNone(user.access.access_starts_at)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["new@example.com"])
        self.assertIn("/activate/", mail.outbox[0].body)

    def test_mismatched_passwords_are_rejected(self):
        resp = self._signup(p2="Different-pass-456")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("password2", resp.context["form"].errors)
        self.assertFalse(User.objects.filter(email="new@example.com").exists())

    def test_existing_account_email_is_rejected(self):
        make_user(email="new@example.com")
        resp = self._signup()
        self.assertIn("email", resp.context["form"].errors)
        self.assertEqual(len(mail.outbox), 0)

    def _activation_path(self):
        body = mail.outbox[-1].body
        return next(line for line in body.splitlines() if "/activate/" in line).replace("http://testserver", "")

    def test_activation_link_turns_account_on_starts_clock_and_lands_on_login(self):
        self._signup()
        resp = self.client.get(self._activation_path())
        self.assertRedirects(resp, reverse("login"), fetch_redirect_response=False)

        user = User.objects.get(email="new@example.com")
        self.assertTrue(user.is_active)
        self.assertTrue(user.access.email_verified)
        self.assertIsNotNone(user.access.access_starts_at)

        login = self.client.post(reverse("login"), {"username": "new@example.com", "password": "Str0ng-pass-123"})
        self.assertRedirects(login, "/")

    def test_used_activation_link_cannot_reactivate_an_expired_account(self):
        self._signup()
        path = self._activation_path()
        self.client.get(path)
        user = User.objects.get(email="new@example.com")
        user.is_active = False
        user.save()

        self.client.get(path)
        user.refresh_from_db()
        self.assertFalse(user.is_active)


def make_expired_signup_user(email="expired@example.com", password="s3cret-pass"):
    user = make_user(email=email, password=password)
    UserAccess.objects.create(
        user=user, access_hours=24, email_verified=True, access_starts_at=timezone.now() - timedelta(hours=25)
    )
    session = ChatSession.objects.create(user=user, title="old chat")
    ChatMessage.objects.create(session=session, role=ChatMessage.Role.USER, content="hello")
    return user


class AccessExpiryTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_login_after_expiry_deactivates_deletes_history_and_emails_admin_contact(self):
        user = make_expired_signup_user()
        resp = self.client.post(reverse("login"), {"username": "expired@example.com", "password": "s3cret-pass"})

        self.assertEqual(resp.status_code, 200)
        self.assertIn("inactive", resp.context["form"].non_field_errors()[0])
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertFalse(ChatSession.objects.filter(user=user).exists())
        self.assertFalse(ChatMessage.objects.exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("admin@siresoft.com", mail.outbox[0].body)

    def test_inactive_email_is_not_resent_on_every_attempt(self):
        make_expired_signup_user()
        for _ in range(3):
            self.client.post(reverse("login"), {"username": "expired@example.com", "password": "s3cret-pass"})
        self.assertEqual(len(mail.outbox), 1)

    def test_wrong_password_on_inactive_account_sends_nothing(self):
        make_expired_signup_user()
        resp = self.client.post(reverse("login"), {"username": "expired@example.com", "password": "wrong"})
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(resp.context["form"].non_field_errors()[0], "Invalid email or password.")

    def test_session_is_logged_out_once_access_runs_out(self):
        user = make_user(email="live@example.com", password="s3cret-pass")
        access = UserAccess.objects.create(user=user, access_hours=24, access_starts_at=timezone.now())
        ChatSession.objects.create(user=user)
        self.client.login(username="live@example.com", password="s3cret-pass")
        self.assertEqual(self.client.get("/").status_code, 200)

        access.access_starts_at = timezone.now() - timedelta(hours=24, minutes=1)
        access.save()
        resp = self.client.get("/")
        self.assertRedirects(resp, "/login/", fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertFalse(ChatSession.objects.filter(user=user).exists())

    def test_periodic_task_expires_only_users_whose_time_is_up(self):
        expired = make_expired_signup_user()
        fresh = make_user(email="fresh@example.com")
        UserAccess.objects.create(user=fresh, access_hours=24, access_starts_at=timezone.now())
        unlimited = make_user(email="staff-made@example.com")

        self.assertEqual(expire_user_access(), 1)
        for user, active in ((expired, False), (fresh, True), (unlimited, True)):
            user.refresh_from_db()
            self.assertEqual(user.is_active, active, user.email)

    def test_admin_hours_change_moves_the_expiry(self):
        user = make_expired_signup_user()
        user.access.access_hours = 48
        user.access.save()
        self.assertFalse(user.access.is_expired())


class AdminAccessTests(TestCase):
    def setUp(self):
        User.objects.create_superuser(username="admin@example.com", email="admin@example.com", password="admin-pass")
        self.client = Client()
        self.client.login(username="admin@example.com", password="admin-pass")

    def test_reactivate_action_restarts_the_access_clock(self):
        user = make_expired_signup_user()
        user.is_active = False
        user.save()
        self.client.post(
            reverse("admin:auth_user_changelist"),
            {"action": "reactivate_and_restart_access", "_selected_action": [str(user.pk)]},
        )
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertFalse(user.access.is_expired())

    def test_user_list_shows_access_columns(self):
        make_expired_signup_user()
        resp = self.client.get(reverse("admin:auth_user_changelist"))
        self.assertContains(resp, "Access hours")
        self.assertContains(resp, "Expired")


class ChatSessionOwnershipTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.alice = make_user(email="alice@example.com")
        self.bob = make_user(email="bob@example.com")
        self.bob_session = ChatSession.objects.create(user=self.bob, title="bob's chat")
        self.client.login(username="alice@example.com", password="s3cret-pass")

    def test_new_chat_is_saved_against_the_signed_in_user(self):
        resp = self.client.post("/api/chat/sessions/", {}, content_type="application/json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(ChatSession.objects.get(pk=resp.json()["id"]).user, self.alice)

    def test_users_only_see_their_own_chats(self):
        ChatSession.objects.create(user=self.alice, title="alice's chat")
        titles = [s["title"] for s in self.client.get("/api/chat/sessions/").json()]
        self.assertEqual(titles, ["alice's chat"])
        self.assertEqual(self.client.get(f"/api/chat/sessions/{self.bob_session.pk}/").status_code, 404)

    def test_cannot_post_into_someone_elses_chat(self):
        resp = self.client.post(
            f"/api/chat/sessions/{self.bob_session.pk}/messages/",
            {"question": "hi", "model": "llama3.1:8b"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)


class AdminHoursChangeTests(TestCase):
    """core.access.apply_admin_access_change -- what saving a user's admin
    page does when "Active" or the access hours change.
    """

    def _signup_user(self, hours=24, started_hours_ago=1, active=True):
        user = make_user(email="timed@example.com", is_active=active)
        UserAccess.objects.create(
            user=user, access_hours=hours, access_starts_at=timezone.now() - timedelta(hours=started_hours_ago)
        )
        ChatSession.objects.create(user=user)
        return user

    def test_setting_hours_to_zero_expires_the_user_immediately(self):
        user = self._signup_user()
        user.access.access_hours = 0
        user.access.save()
        apply_admin_access_change(user, reactivated=False, hours_changed=True)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertFalse(ChatSession.objects.filter(user=user).exists())

    def test_giving_an_inactive_user_hours_reactivates_them_from_now(self):
        user = self._signup_user(hours=0, started_hours_ago=30, active=False)
        user.access.access_hours = 24
        user.access.save()
        apply_admin_access_change(user, reactivated=False, hours_changed=True)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertFalse(user.access.is_expired())
        self.assertGreater(user.access.expires_at, timezone.now() + timedelta(hours=23))

    def test_reactivated_user_can_log_in_and_is_told_their_hours(self):
        user = self._signup_user(hours=0, started_hours_ago=30, active=False)
        user.access.access_hours = 24
        user.access.save()
        apply_admin_access_change(user, reactivated=False, hours_changed=True)

        client = Client()
        resp = client.post(reverse("login"), {"username": "timed@example.com", "password": "s3cret-pass"}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.wsgi_request.user.is_authenticated)
        texts = [m["text"] for m in resp.context["initial_messages"]]
        self.assertTrue(any("23 hours 59 minutes" in t or "24 hours" in t for t in texts), texts)

    def test_extending_an_active_users_hours_keeps_their_start_time(self):
        user = self._signup_user(hours=24, started_hours_ago=10)
        started = user.access.access_starts_at
        user.access.access_hours = 48
        user.access.save()
        apply_admin_access_change(user, reactivated=False, hours_changed=True)
        user.access.refresh_from_db()
        self.assertEqual(user.access.access_starts_at, started)


class AuthStatusTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = make_user(email="live@example.com")
        self.client.login(username="live@example.com", password="s3cret-pass")

    def test_active_user_gets_200(self):
        self.assertEqual(self.client.get("/api/auth/status/").status_code, 200)

    def test_deactivated_user_gets_401_and_a_reason_on_the_login_page(self):
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.client.get("/api/auth/status/").status_code, 401)
        resp = self.client.get(reverse("login"))
        self.assertContains(resp, "inactive")
