from datetime import timedelta

from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts.services import apply_admin_access_change, expire_due_users
from accounts.signals import access_expired
from accounts.tests.base import PASSWORD, AccountsTestCase, User, make_timed_user, make_user


class ExpiryTests(AccountsTestCase):
    def setUp(self):
        self.expired_users = []
        access_expired.connect(self._record, dispatch_uid="accounts-test-recorder")

    def tearDown(self):
        access_expired.disconnect(dispatch_uid="accounts-test-recorder")

    def _record(self, sender, user, **kwargs):
        self.expired_users.append(user.pk)

    def test_login_after_expiry_deactivates_signals_and_emails_contact(self):
        user = make_timed_user(started_hours_ago=25)
        resp = self.client.post(reverse("accounts:login"), {"username": user.email, "password": PASSWORD})
        self.assertIn("inactive", resp.context["form"].non_field_errors()[0])
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertEqual(self.expired_users, [user.pk])
        self.assertIn("admin@example.com", mail.outbox[0].body)

    def test_inactive_email_is_throttled_and_needs_the_right_password(self):
        user = make_timed_user(started_hours_ago=25)
        self.client.post(reverse("accounts:login"), {"username": user.email, "password": "wrong"})
        self.assertEqual(len(mail.outbox), 0)
        for _ in range(3):
            self.client.post(reverse("accounts:login"), {"username": user.email, "password": PASSWORD})
        self.assertEqual(len(mail.outbox), 1)

    def test_session_ends_once_time_is_up(self):
        user = make_timed_user()
        self.client.login(username=user.email, password=PASSWORD)
        self.assertEqual(self.client.get("/").status_code, 200)
        user.access.access_starts_at = timezone.now() - timedelta(hours=25)
        user.access.save()
        self.assertRedirects(self.client.get("/"), reverse("accounts:login"), fetch_redirect_response=False)
        self.assertEqual(self.expired_users, [user.pk])

    def test_scheduled_expiry_only_touches_users_whose_time_is_up(self):
        expired = make_timed_user(email="old@example.com", started_hours_ago=25)
        fresh = make_timed_user(email="fresh@example.com")
        unlimited = make_user(email="admin-made@example.com")
        self.assertEqual(expire_due_users(), 1)
        for user, active in ((expired, False), (fresh, True), (unlimited, True)):
            user.refresh_from_db()
            self.assertEqual(user.is_active, active, user.email)


class AdminAccessChangeTests(AccountsTestCase):
    def test_hours_to_zero_expires_now(self):
        user = make_timed_user()
        user.access.access_hours = 0
        user.access.save()
        apply_admin_access_change(user, reactivated=False, hours_changed=True)
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_superusers_never_expire(self):
        user = make_timed_user()
        User.objects.filter(pk=user.pk).update(is_superuser=True)
        user.refresh_from_db()
        user.access.access_hours = 0
        user.access.save()
        apply_admin_access_change(user, reactivated=False, hours_changed=True)
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_giving_an_inactive_user_hours_reactivates_from_now(self):
        user = make_timed_user(hours=0, started_hours_ago=30, is_active=False)
        user.access.access_hours = 24
        user.access.save()
        apply_admin_access_change(user, reactivated=False, hours_changed=True)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertGreater(user.access.expires_at, timezone.now() + timedelta(hours=23))

    def test_extending_an_active_user_keeps_their_start(self):
        user = make_timed_user(started_hours_ago=10)
        started = user.access.access_starts_at
        user.access.access_hours = 48
        user.access.save()
        apply_admin_access_change(user, reactivated=False, hours_changed=True)
        user.access.refresh_from_db()
        self.assertEqual(user.access.access_starts_at, started)


class UserAdminTests(AccountsTestCase):
    def setUp(self):
        User.objects.create_superuser(username="root@example.com", email="root@example.com", password="root-pass")
        self.client.login(username="root@example.com", password="root-pass")

    def test_list_shows_access_columns(self):
        make_timed_user(started_hours_ago=25)
        resp = self.client.get(reverse("admin:auth_user_changelist"))
        self.assertContains(resp, "Access hours")
        self.assertContains(resp, "Expired")

    def test_reactivate_action_restarts_the_clock(self):
        user = make_timed_user(started_hours_ago=25, is_active=False)
        self.client.post(
            reverse("admin:auth_user_changelist"),
            {"action": "reactivate_and_restart_access", "_selected_action": [str(user.pk)]},
        )
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertFalse(user.access.is_expired())

    def test_force_logout_action_deactivates(self):
        user = make_user(email="target@example.com")
        self.client.post(
            reverse("admin:auth_user_changelist"),
            {"action": "force_logout_and_deactivate", "_selected_action": [str(user.pk)]},
        )
        user.refresh_from_db()
        self.assertFalse(user.is_active)
