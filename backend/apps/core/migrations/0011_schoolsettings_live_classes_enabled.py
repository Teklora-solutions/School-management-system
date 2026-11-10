from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_schoolsettings_quizzes_enabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="schoolsettings",
            name="live_classes_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Turn on to allow teachers to schedule live classes (Google Meet, Zoom, Teams) and show the shared class calendar. Requires Learning to also be on.",
            ),
        ),
    ]

