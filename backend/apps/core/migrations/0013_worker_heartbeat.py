from django.db import migrations, models
from django.utils import timezone

HEARTBEAT_NAME = "Worker heartbeat"
HEARTBEAT_FUNC = "apps.core.tasks.worker_heartbeat"


def create_schedule(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    if Schedule.objects.filter(name=HEARTBEAT_NAME).exists():
        return
    Schedule.objects.create(
        name=HEARTBEAT_NAME,
        func=HEARTBEAT_FUNC,
        schedule_type="I",  # Schedule.MINUTES
        minutes=5,
        repeats=-1,
        next_run=timezone.now(),
    )


def remove_schedule(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    Schedule.objects.filter(name=HEARTBEAT_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_schoolsettings_feed_community_enabled"),
        ("django_q", "__latest__"),
    ]

    operations = [
        migrations.CreateModel(
            name="WorkerHeartbeat",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "last_run_at",
                    models.DateTimeField(blank=True, null=True),
                ),
            ],
            options={
                "verbose_name": "worker heartbeat",
                "verbose_name_plural": "worker heartbeats",
                "db_table": "core_worker_heartbeat",
            },
        ),
        migrations.RunPython(create_schedule, remove_schedule),
    ]
