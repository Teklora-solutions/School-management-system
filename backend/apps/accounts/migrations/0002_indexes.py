"""
0002_indexes.py
---------------
Add composite and missing indexes identified during production hardening.

  accounts_user:
    (role, is_active)  — used by staff_directory() and school_kpis()
    (last_name, first_name)  — explicit covering index to back the default ordering
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="user",
            index=models.Index(
                fields=["role", "is_active"],
                name="accounts_user_role_active_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="user",
            index=models.Index(
                fields=["last_name", "first_name"],
                name="accounts_user_name_ordering_idx",
            ),
        ),
    ]
