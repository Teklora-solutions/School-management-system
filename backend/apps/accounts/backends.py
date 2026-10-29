"""
apps/accounts/backends.py
--------------------------
Custom authentication backend.

Login rules:
  - Staff (admin, director, teacher, finance): login with EMAIL + password
  - Students: login with ADMISSION NUMBER (username) + password
  - Parents: login with USERNAME + password (auto-generated on student enrolment)

If the credential field contains '@', we treat it as an email and look up the
user by email before handing off to Django's standard password check.
Otherwise we fall back to the default username lookup.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

User = get_user_model()


class EmailOrAdmissionBackend(ModelBackend):
    """
    Authenticate by email (staff) or username/admission-number (students).
    Completely replaces ModelBackend — password validation and
    permission checks are inherited from it.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None or password is None:
            return None

        credential = username.strip()

        if "@" in credential:
            # Treat as email — look up user by email
            try:
                user = User.objects.get(email=credential.lower())
            except User.DoesNotExist:
                # Run the hasher anyway to prevent timing attacks
                User().set_password(password)
                return None
            # Delegate password check + active-flag check to parent
            return super().authenticate(
                request, username=user.username, password=password, **kwargs
            )

        # No '@' — treat as username / admission number (students, superusers)
        return super().authenticate(
            request, username=credential, password=password, **kwargs
        )
