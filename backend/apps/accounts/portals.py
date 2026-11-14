"""
apps/accounts/portals.py
------------------------
The school's sign-in screens, one per audience.

A teacher should never have to work out that the form they found is the
student form, so each role gets its own page at ``/login/<slug>/``. This is
the single source for that list: the chooser view, the role-pinned login
view, and the public site's footer "Quick Links" column all read it, so a
portal can only be added or renamed in one place.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import User


@dataclass(frozen=True)
class Portal:
    slug: str
    role: str
    label: str
    heading: str
    blurb: str
    field_label: str
    placeholder: str
    credential: str
    icon: str
    self_reset: bool

    @property
    def uses_email(self) -> bool:
        """Staff sign in with an email. Students and parents do not."""
        return self.role not in {User.Role.STUDENT, User.Role.PARENT}


PORTALS: dict[str, Portal] = {
    portal.slug: portal
    for portal in (
        Portal(
            slug="admin",
            role=User.Role.ADMIN,
            label="Administration",
            heading="Administration Portal",
            blurb="Full control of students, results, fees and staff accounts.",
            field_label="Email address",
            placeholder="Enter your email address",
            credential="your staff email address",
            icon="bi-shield-lock-fill",
            self_reset=False,
        ),
        Portal(
            slug="director",
            role=User.Role.DIRECTOR,
            label="Director",
            heading="Director Portal",
            blurb="Read-only oversight: enrolment, results, attendance and books.",
            field_label="Email address",
            placeholder="Enter your email address",
            credential="your staff email address",
            icon="bi-briefcase-fill",
            self_reset=False,
        ),
        Portal(
            slug="finance",
            role=User.Role.FINANCE,
            label="Finance",
            heading="Finance Portal",
            blurb="Fee structures, invoicing, payments and the ledger.",
            field_label="Email address",
            placeholder="Enter your email address",
            credential="your staff email address",
            icon="bi-cash-stack",
            self_reset=False,
        ),
        Portal(
            slug="teacher",
            role=User.Role.TEACHER,
            label="Teacher",
            heading="Teacher Portal",
            blurb="Your classes, results sheets and homework.",
            field_label="Email address",
            placeholder="Enter your email address",
            credential="your staff email address",
            icon="bi-pencil-square",
            self_reset=False,
        ),
        Portal(
            slug="student",
            role=User.Role.STUDENT,
            label="Student",
            heading="Student Portal",
            blurb="Your classes, results, homework and fee statement.",
            field_label="Admission number",
            placeholder="Enter your admission number",
            credential="the admission number on your letter, such as ADM/2026/014",
            icon="bi-mortarboard-fill",
            self_reset=True,
        ),
        Portal(
            slug="parent",
            role=User.Role.PARENT,
            label="Parent",
            heading="Parent Portal",
            blurb="Follow your children's progress and fees.",
            field_label="Username",
            placeholder="Enter your username",
            credential="the username from your welcome email",
            icon="bi-people-fill",
            self_reset=True,
        ),
    )
}
