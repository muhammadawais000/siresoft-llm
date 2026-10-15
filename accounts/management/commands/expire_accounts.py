from django.core.management.base import BaseCommand

from accounts.services import expire_due_users


class Command(BaseCommand):
    help = "Deactivate every user whose access hours have run out (for cron, when Celery beat isn't used)."

    def handle(self, *args, **options):
        self.stdout.write(f"Expired {expire_due_users()} user(s).")
