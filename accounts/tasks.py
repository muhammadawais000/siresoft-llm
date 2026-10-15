"""Celery task for projects that run Celery beat. Without Celery, schedule
`python manage.py expire_accounts` (e.g. cron every 5 minutes) instead."""

from celery import shared_task

from accounts.services import expire_due_users


@shared_task
def expire_user_access():
    return expire_due_users()
