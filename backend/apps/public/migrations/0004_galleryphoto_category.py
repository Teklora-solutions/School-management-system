# Generated for W1 frontend redesign — adds category to GalleryPhoto.
# Blank by default so all existing photos remain visible (they fall into
# the "All" filter tab automatically).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("public", "0003_admissionstep_eventitem_faq_featurecard_heroslide_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="galleryphoto",
            name="category",
            field=models.CharField(
                max_length=100,
                blank=True,
                db_index=True,
                help_text=(
                    "Optional category tag for the gallery filter, e.g. "
                    '"Sports", "Arts", "Science". Leave blank for no filter label.'
                ),
            ),
        ),
    ]
