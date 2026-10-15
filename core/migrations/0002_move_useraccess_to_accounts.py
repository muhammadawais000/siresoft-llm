"""UserAccess moved from core to the reusable accounts app. Copies any
existing rows into accounts' table, then drops core's."""

from django.db import migrations


def copy_rows(apps, schema_editor):
    Old = apps.get_model("core", "UserAccess")
    New = apps.get_model("accounts", "UserAccess")
    New.objects.bulk_create(
        New(
            user_id=row.user_id,
            access_hours=row.access_hours,
            access_starts_at=row.access_starts_at,
            email_verified=row.email_verified,
            last_notice_sent_at=row.last_notice_sent_at,
        )
        for row in Old.objects.all()
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_user_access"),
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(copy_rows, migrations.RunPython.noop),
        migrations.DeleteModel(name="UserAccess"),
    ]
