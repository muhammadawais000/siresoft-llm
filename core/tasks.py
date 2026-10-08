from celery import shared_task
from django.contrib.auth.models import User
from django.utils import timezone

from core.access import expire_user


@shared_task
def expire_user_access():
    """Deactivate every user whose access period has run out (scheduled
    by CELERY_BEAT_SCHEDULE). Few enough users have a limit that checking
    each in Python is simpler than expressing hours-to-interval math in SQL.
    """
    now = timezone.now()
    expired = 0
    candidates = User.objects.filter(
        is_active=True,
        is_superuser=False,
        access__access_hours__isnull=False,
        access__access_starts_at__isnull=False,
    ).select_related("access")
    for user in candidates:
        if user.access.is_expired(now):
            expire_user(user)
            expired += 1
    return expired
