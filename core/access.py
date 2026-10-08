"""Account lifecycle for self-signup users: verification email, the
time-limited access period, and what happens when it runs out.

Expiry is enforced in two places that both call expire_user(): the
core.tasks.expire_user_access periodic task (so chat history is deleted
on time even if the user never comes back), and RequireActiveLoginMiddleware
plus the login form (so a user is never let in during the gap between
expiry and the task's next run).
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

from chat.models import ChatSession
from core.models import UserAccess

logger = logging.getLogger(__name__)

NOTICE_THROTTLE = timedelta(minutes=5)


def get_access(user):
    try:
        return user.access
    except UserAccess.DoesNotExist:
        return None


def access_expired(user) -> bool:
    if user.is_superuser:
        return False
    access = get_access(user)
    return access is not None and access.is_expired()


@transaction.atomic
def expire_user(user):
    """Deactivate an account whose access period ran out and delete its chat history."""
    user.is_active = False
    user.save(update_fields=["is_active"])
    deleted, _ = ChatSession.objects.filter(user=user).delete()
    logger.info("access expired: user=%s deactivated, %d chat rows deleted", user.pk, deleted)


def start_access_period(access, now=None):
    access.access_starts_at = now or timezone.now()
    access.last_notice_sent_at = None


def _notice_allowed(access) -> bool:
    return access.last_notice_sent_at is None or timezone.now() - access.last_notice_sent_at >= NOTICE_THROTTLE


def _mark_notice_sent(access):
    access.last_notice_sent_at = timezone.now()
    access.save(update_fields=["last_notice_sent_at"])


def send_verification_email(request, user, *, force=False) -> bool:
    """Email the "your account was created, click below" link.

    Returns False without sending when one went out in the last few
    minutes (unless `force`). Raises if the mail server rejects it.
    """
    access = get_access(user)
    if not force and not _notice_allowed(access):
        return False
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    context = {
        "activation_url": request.build_absolute_uri(reverse("activate", args=[uid, token])),
        "access_hours": access.access_hours,
    }
    send_mail(
        subject="Your Siresoft LLM account has been created",
        message=render_to_string("emails/account_created.txt", context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
    )
    _mark_notice_sent(access)
    return True


def send_inactive_notice(user) -> bool:
    """Email "your account is inactive, contact the admin". Throttled the
    same way as the verification email; failures are logged, not raised,
    since the login page shows the same message either way.
    """
    access, _ = UserAccess.objects.get_or_create(user=user)
    if not _notice_allowed(access):
        return False
    context = {"admin_email": settings.ADMIN_CONTACT_EMAIL}
    try:
        send_mail(
            subject="Your Siresoft LLM account is inactive",
            message=render_to_string("emails/account_inactive.txt", context),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
        )
    except Exception:
        logger.exception("could not send inactive-account email to user=%s", user.pk)
        return False
    _mark_notice_sent(access)
    return True
