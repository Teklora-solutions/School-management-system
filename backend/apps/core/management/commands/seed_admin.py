"""Seed (or reset) a school administrator login with known credentials.

Unlike ``seed_demo_data`` — which generates random per-role passwords and
prints them once — this command guarantees a working admin login:

* if the username does not exist, the account is created;
* if it exists, its password is reset to the requested value;
* ``must_change_password`` is cleared so the login works immediately.

Examples::

    python manage.py seed_admin
    python manage.py seed_admin --username admin --password 'S3cret!2026'
    python manage.py seed_admin --email office@school.edu --dry-run
"""

from __future__ import annotations

import secrets
import string

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()

PASSWORD_ALPHABET = string.ascii_letters + string.digits


def _generate_password(length: int = 16) -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


class Command(BaseCommand):
    help = "Create or reset an admin login and print its credentials."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            default="admin",
            help="Login username (default: admin).",
        )
        parser.add_argument(
            "--password",
            default=None,
            help="Password to set. Omit to generate a random one (printed once).",
        )
        parser.add_argument(
            "--email",
            default=None,
            help="Email for a new account (default: <username>@school.local).",
        )
        parser.add_argument(
            "--first-name",
            default="School",
            help="First name for a new account.",
        )
        parser.add_argument(
            "--last-name",
            default="Administrator",
            help="Last name for a new account.",
        )
        parser.add_argument(
            "--superuser",
            action="store_true",
            help="Also grant is_superuser (Django admin site access).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would change without writing.",
        )

    def handle(self, *args, **options):
        username = options["username"].strip()
        password = options["password"] or _generate_password()
        email = (options["email"] or f"{username}@school.local").strip().lower()
        dry_run = options["dry_run"]

        user = User.objects.filter(username=username).first()
        created = user is None

        if created:
            if User.objects.filter(email=email).exists():
                raise CommandError(
                    f"Email '{email}' is already used by another account; "
                    "pass --email to pick a different one."
                )
            if not dry_run:
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=password,
                    first_name=options["first_name"],
                    last_name=options["last_name"],
                    role=User.Role.ADMIN,
                    is_staff=True,
                    is_superuser=options["superuser"],
                    must_change_password=False,
                    is_active=True,
                )
        else:
            if not dry_run:
                user.set_password(password)
                user.role = User.Role.ADMIN
                user.is_staff = True
                if options["superuser"]:
                    user.is_superuser = True
                user.must_change_password = False
                user.is_active = True
                user.save()

        action = "created" if created else "reset"
        verb = "would be" if dry_run else "was"
        self.stdout.write(self.style.SUCCESS(f"Admin account {verb} {action}."))
        self.stdout.write("")
        self.stdout.write("  Username:   " + username)
        self.stdout.write("  Password:   " + password)
        self.stdout.write("  Email:      " + email)
        self.stdout.write("  Role:       admin")
        self.stdout.write(f"  Superuser:  {'yes' if options['superuser'] or (user and user.is_superuser) else 'no'}")
        self.stdout.write("")
        self.stdout.write(
            "Log in at /login/ — these credentials work immediately "
            "(must_change_password is off)."
        )
