# Generated for W1 frontend redesign — adds hero_image to SchoolSettings.
# This field is optional (null, blank) so no existing rows are affected.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0020_community_settings_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="schoolsettings",
            name="hero_image",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to="school/hero/",
                help_text=(
                    "Hero background image shown on the public home page. "
                    "Recommended: 1600 × 900 px. Leave blank to use the "
                    "default gradient background."
                ),
            ),
        ),
    ]
