"""
0002_indexes.py
---------------
Add performance indexes for common student query patterns.

  students_student:
    (current_class, status)  — student_list() / school_kpis() class+active filter
    (status, current_class)  — alternate order for coverage
    current_stream           — stream-based drill-down queries

  students_student_guardian:
    (student_id, is_primary) — "find primary guardian for student" look-up
    (parent_id, is_primary)  — "find primary child for parent" look-up (context processor)
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0001_initial"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="student",
            index=models.Index(
                fields=["current_class", "status"],
                name="students_student_class_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="studentguardian",
            index=models.Index(
                fields=["student", "is_primary"],
                name="students_guardian_student_primary_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="studentguardian",
            index=models.Index(
                fields=["parent", "is_primary"],
                name="students_guardian_parent_primary_idx",
            ),
        ),
    ]
