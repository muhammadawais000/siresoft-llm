"""Account lifecycle: verification email, the time-limited access period,
and what happens when it runs out.

Expiry is enforced in several places that all call expire_user(): the
expire_user_access task / `manage.py expire_accounts` (so it happens on
time even if the user never comes back), LoginRequiredMiddleware and the
sign-in form (so a user is never let in between expiry and the next run),
and the admin (when hours are cut to 0).
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.conf import accounts_settings, message
from accounts.models import UserAccess
from accounts.signals import access_expired

logger = logging.getLogger(__name__)


def get_access(user):
    try:
        return user.access
    except UserAccess.DoesNotExist:
        return None


def access_expired_for(user) -> bool:
    if user.is_superuser:
        return False
    access = get_access(user)
    return access is not None and access.is_expired()


@transaction.atomic
def expire_user(user):
    """Deactivate an account whose access period ran out, then let the
    project clean up its data through the access_expired signal."""
    user.is_active = False
    user.save(update_fields=["is_active"])
    logger.info("access expired: user=%s deactivated", user.pk)
    access_expired.send(sender=user.__class__, user=user)


def start_access_period(access, now=None):
    access.access_starts_at = now or timezone.now()
    access.last_notice_sent_at = None


def remaining_text(access, now=None) -> str:
    """'23 hours 59 minutes' -- time left in an access period."""
    seconds = max(0, int((access.expires_at - (now or timezone.now())).total_seconds()))
    hours, minutes = seconds // 3600, (seconds % 3600) // 60
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes or not hours:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " ".join(parts)


def restart_access(user):
    """Re-activate `user` with a fresh access period counting from now."""
    access = UserAccess.objects.filter(user=user, access_hours__isnull=False).first()
    if access is not None:
        start_access_period(access)
        access.email_verified = True
        access.save()
    if not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active"])


def apply_admin_access_change(user, *, reactivated: bool, hours_changed: bool):
    """Act on an admin saving a user's page, after the save.

    - Ticking "Active", or giving an inactive user hours (e.g. 0 -> 24),
      re-activates them with their hours counting from now.
    - Cutting an active user's hours so their time is already up (e.g. to
      0) expires them now; the session guard script logs them out within
      STATUS_POLL_SECONDS.
    """
    access = UserAccess.objects.filter(user=user, access_hours__isnull=False).first()
    if access is None:
        return
    if reactivated or (hours_changed and not user.is_active and access.access_hours > 0):
        restart_access(user)
        access.refresh_from_db()
    if user.is_active and not user.is_superuser and access.is_expired():
        expire_user(user)


def expire_due_users(now=None) -> int:
    """Expire every active user whose time is up. Returns how many."""
    from django.contrib.auth import get_user_model

    now = now or timezone.now()
    candidates = (
        get_user_model()
        .objects.filter(
            is_active=True,
            is_superuser=False,
            access__access_hours__isnull=False,
            access__access_starts_at__isnull=False,
        )
        .select_related("access")
    )
    expired = 0
    for user in candidates:
        if user.access.is_expired(now):
            expire_user(user)
            expired += 1
    return expired


def _notice_allowed(access) -> bool:
    throttle = timedelta(minutes=accounts_settings.NOTICE_THROTTLE_MINUTES)
    return access.last_notice_sent_at is None or timezone.now() - access.last_notice_sent_at >= throttle


def _mark_notice_sent(access):
    access.last_notice_sent_at = timezone.now()
    access.save(update_fields=["last_notice_sent_at"])


def _email_context(**extra):
    return {
        "site_name": accounts_settings.SITE_NAME,
        "admin_email": accounts_settings.admin_email,
        **extra,
    }


def send_verification_email(request, user, *, force=False) -> bool:
    """Email the "your account was created, click below" link.

    Returns False without sending when one went out within
    NOTICE_THROTTLE_MINUTES (unless `force`). Raises if the mail server
    rejects it.
    """
    access = get_access(user)
    if not force and not _notice_allowed(access):
        return False
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    context = _email_context(
        activation_url=request.build_absolute_uri(reverse("accounts:activate", args=[uid, token])),
        access_hours=access.access_hours,
    )
    send_mail(
        subject=message("created_email_subject"),
        message=render_to_string("accounts/emails/account_created.txt", context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
    )
    _mark_notice_sent(access)
    return True


def send_inactive_notice(user) -> bool:
    """Email "your account is inactive, contact the admin". Throttled the
    same way; failures are logged, not raised, since the sign-in page
    shows the same message either way.
    """
    access, _ = UserAccess.objects.get_or_create(user=user)
    if not _notice_allowed(access):
        return False
    try:
        send_mail(
            subject=message("inactive_email_subject"),
            message=render_to_string("accounts/emails/account_inactive.txt", _email_context()),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
        )
    except Exception:
        logger.exception("could not send inactive-account email to user=%s", user.pk)
        return False
    _mark_notice_sent(access)
    return True
