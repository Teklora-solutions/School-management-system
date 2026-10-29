from django.db import migrations

EXAM_COUNT = 10


def create_exams(apps, schema_editor):
    Term = apps.get_model("academics", "Term")
    Exam = apps.get_model("results", "Exam")
    for term in Term.objects.all().iterator():
        existing = set(
            Exam.objects.filter(term_id=term.pk).values_list("order", flat=True)
        )
        for order in range(1, EXAM_COUNT + 1):
            if order not in existing:
                Exam.objects.create(term_id=term.pk, name=f"Exam {order}", order=order)


def remove_exams(apps, schema_editor):
    Exam = apps.get_model("results", "Exam")
    Exam.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("results", "0003_exam_dimension"),
    ]

    operations = [
        migrations.RunPython(create_exams, remove_exams),
    ]
