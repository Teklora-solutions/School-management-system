"""
learning/0012_digest_schedule.py
---------------------------------
Create the django-q schedule row for the morning teacher digest.

The digest runs daily at 07:00 Nairobi time (EAT, UTC+3).  Saturday and
Sunday runs fire but exit immediately (``send_teacher_digests`` returns 0),
so teachers get at most one digest per school day.

Why a daily schedule and not a cron expression
----------------------------------------------
``Schedule`` supports ``schedule_type="C"`` (cron), but django-q requires the
optional ``croniter`` package for cron schedules and it is not installed here.
Creating a cron row would raise ImportError the first time the worker read it.
A daily row needs no extra dependency: django-q adds one day to ``next_run``
and re-anchors it in the configured time zone, so 07:00 Nairobi stays 07:00.

How the time zone is set
------------------------
The Schedule row stores ``next_run`` in UTC (Django converts the aware
datetime on save).  django-q's ``Q_CLUSTER`` has no ``time_zone`` key, so it
falls back to ``settings.TIME_ZONE``, which is ``"Africa/Nairobi"``.  The
worker therefore compares the stored UTC instant against Nairobi local time;
07:00 EAT is 04:00 UTC.  Nairobi has no daylight saving, so the slot does not
drift across the year.

What happens when the worker is down at 07:00
---------------------------------------------
``Q_CLUSTER["catch_up"]`` is False, so the missed run is skipped that day — no
late catch-up.  The WorkerHeartbeat (F2d) page shows the gap so an admin can
see the slot was missed.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import migrations

DIGEST_NAME = "Morning teacher digest"
DIGEST_FUNC = "apps.learning.digest_tasks.send_teacher_digests"


def _next_seven_am():
    """Next 07:00 in the project time zone, returned as an aware datetime."""
    tz = ZoneInfo(settings.TIME_ZONE)
    now = datetime.now(tz)
    candidate = datetime.combine(now.date(), time(7, 0), tzinfo=tz)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def create_digest_schedule(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    if Schedule.objects.filter(name=DIGEST_NAME).exists():
        return
    Schedule.objects.create(
        name=DIGEST_NAME,
        func=DIGEST_FUNC,
        schedule_type="D",  # Schedule.DAILY
        repeats=-1,
        next_run=_next_seven_am(),
    )


def remove_digest_schedule(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    Schedule.objects.filter(name=DIGEST_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("learning", "0011_classpost_digest_sent_at"),
        ("django_q", "__latest__"),
    ]

    operations = [
        migrations.RunPython(
            create_digest_schedule,
            reverse_code=remove_digest_schedule,
        ),
    ]
