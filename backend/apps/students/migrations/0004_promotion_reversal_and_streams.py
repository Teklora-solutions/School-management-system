import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academics", "0002_schoolclass_grade_scale"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        (
            "students",
            "0003_remove_student_students_student_class_status_idx_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="promotionbatch",
            name="is_reversed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="promotionbatch",
            name="reverse_reason",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="promotionbatch",
            name="reversed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="promotionbatch",
            name="reversed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="promotion_reversals",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="promotionrecord",
            name="previous_status",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="promotionrecord",
            name="previous_stream",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="academics.stream",
            ),
        ),
        migrations.AddField(
            model_name="promotionrecord",
            name="to_stream",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="academics.stream",
            ),
        ),
    ]
