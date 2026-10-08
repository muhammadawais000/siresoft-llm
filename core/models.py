from django.db import models


class TimeStampedModel(models.Model):
    """Abstract base adding created/updated timestamps to every model."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UserAccess(models.Model):
    """How long a user may use the app before their account goes inactive.

    Self-signup accounts get one of these with `access_hours` from
    settings.SIGNUP_ACCESS_HOURS (24 by default); the clock starts when
    they click the link in their verification email, not at signup. Admins
    change `access_hours` on the user's admin page, and re-activating an
    expired user there restarts the clock (see core.admin). Users created
    by an admin have no row, or a blank `access_hours`, and never expire.
    """

    user = models.OneToOneField("auth.User", on_delete=models.CASCADE, related_name="access")
    access_hours = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Hours the account stays active after the access period starts. Blank = no limit.",
    )
    access_starts_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when the user verifies their email, and reset whenever an admin re-activates the account.",
    )
    email_verified = models.BooleanField(default=True)
    # Last verification or "account inactive" email sent, so repeated
    # signup or login attempts can't flood someone's inbox.
    last_notice_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "access"
        verbose_name_plural = "access"

    def __str__(self):
        return f"access for {self.user}"

    @property
    def expires_at(self):
        if self.access_hours is None or self.access_starts_at is None:
            return None
        from datetime import timedelta

        return self.access_starts_at + timedelta(hours=self.access_hours)

    def is_expired(self, now=None):
        from django.utils import timezone

        expires_at = self.expires_at
        return expires_at is not None and (now or timezone.now()) >= expires_at
