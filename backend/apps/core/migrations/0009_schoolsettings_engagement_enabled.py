# Generated migration — adds the engagement_enabled switch to SchoolSettings.
# Additive only: no existing columns are altered.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_schoolsettings_admissions_email_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="schoolsettings",
            name="engagement_enabled",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Turn on to show progress bars, lesson streaks and the teacher "
                    "class-progress view inside the Learning module. Requires Learning "
                    "to also be on. Has no effect until you also turn it on per class "
                    "in Class Engagement Settings."
                ),
            ),
        ),
    ]
