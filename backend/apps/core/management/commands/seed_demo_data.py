"""
apps/core/management/commands/seed_demo_data.py
------------------------------------------------
Populate the database with a realistic demo school so that every screen has
something to show: role dashboards, the fee ledger, results governance,
homework, announcements and the in-app notification centre.

Writes go through the existing service layer rather than the models directly,
so demo rows carry the same audit-log entries and double-entry ledger trail
that real ones do.

The command is idempotent: every step looks up its natural key first and skips
what already exists, so re-running it never duplicates data.  Pass --dry-run to
exercise the whole seed inside a transaction that is rolled back.

``--students-per-class`` sets the class size (default 10). A testing database
usually wants a full register — ``--students-per-class 30`` gives four classes
of thirty, which is enough for class-wide screens, the homework review page and
the finance ageing lists to show something real.

Demo credentials match ``locustfile.py``.  They are seeded with
``must_change_password=False`` so they work immediately, which is why the
command refuses to run under the ``config.settings.prod`` settings module
unless ``--force`` is passed.  A username that already exists with the same
role but a different email (a database's own ``admin``, say) is reused as the
actor and its password is left exactly as it was.
"""

from __future__ import annotations

import datetime as dt
import logging
import random
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.academics import calendar as academics_calendar
from apps.academics import services as academics_services
from apps.academics.models import (
    AcademicYear,
    ClassSubject,
    SchoolClass,
    Subject,
    Term,
)
from apps.accounts import services as accounts_services
from apps.accounts.models import ParentProfile, User
from apps.communications import services as comms_services
from apps.communications.models import Announcement, MessageThread
from apps.core.models import AuditLog, Notification, SchoolSettings
from apps.finance import services as finance_services
from apps.finance.chart_of_accounts import GENERAL_EXPENSES_CODE
from apps.finance.models import (
    Account,
    Adjustment,
    Expense,
    FeeStructure,
    Invoice,
    Payment,
)
from apps.homework import services as homework_services
from apps.homework.models import Homework, HomeworkSubmission
from apps.results import services as results_services
from apps.results.models import (
    GradeScale,
    GradingConfig,
    ResultSheet,
    ScoreEntry,
)
from apps.students import services as students_services
from apps.students.models import Student, StudentGuardian
from apps.teachers import services as teachers_services
from apps.teachers.models import TeacherAssignment, TeacherProfile

# ─── Demo dataset ─────────────────────────────────────────────────────────────

SEED_RNG = 20260901  # fixed so the generated names/scores repeat per run

DEMO_EMAIL_DOMAIN = "demo.school"

# Kept in sync with the credentials in locustfile.py.
DEMO_PASSWORDS = {
    User.Role.ADMIN: "REMOVED-DEMO-PASSWORD",
    User.Role.DIRECTOR: "REMOVED-DEMO-PASSWORD",
    User.Role.FINANCE: "REMOVED-DEMO-PASSWORD",
    User.Role.TEACHER: "REMOVED-DEMO-PASSWORD",
    User.Role.STUDENT: "REMOVED-DEMO-PASSWORD",
    User.Role.PARENT: "REMOVED-DEMO-PASSWORD",
}

SCHOOL_IDENTITY = {
    "name": "Greenfield Academy",
    "motto": "Knowledge, Discipline, Service",
    "phone": "+233 30 200 0000",
    "email": "office@greenfield-academy.demo.school",
    "currency": "GHS",
}

SUBJECTS = [
    ("MATH", "Mathematics"),
    ("ENG", "English Language"),
    ("SCI", "Integrated Science"),
    ("SOC", "Social Studies"),
    ("ICT", "Computing"),
    ("RLE", "Religious & Moral Education"),
]

# (class name, promotion order, term tuition in the demo currency)
CLASSES = [
    ("Basic 5", 5, Decimal("420.00")),
    ("Basic 6", 6, Decimal("450.00")),
    ("JHS 1", 7, Decimal("520.00")),
    ("JHS 2", 8, Decimal("560.00")),
]

STUDENTS_PER_CLASS = 10
MIN_STUDENTS_PER_CLASS = 2
MAX_STUDENTS_PER_CLASS = 100

TEACHERS = [
    ("tch_001", "Grace", "Adjei", "B.Ed Mathematics", "Sciences & Maths"),
    ("tch_002", "Kofi", "Mensah", "B.A English", "Languages"),
    ("tch_003", "Ama", "Boateng", "B.Sc Biology", "Sciences & Maths"),
    ("tch_004", "Yaw", "Owusu", "B.A Social Sciences", "Humanities"),
    ("tch_005", "Esi", "Frimpong", "B.Sc IT", "Languages"),
]

# Which teacher owns which subject, and who runs each classroom.
SUBJECT_OWNER = {
    "MATH": "tch_001",
    "ENG": "tch_002",
    "SCI": "tch_003",
    "SOC": "tch_004",
    "RLE": "tch_004",
    "ICT": "tch_005",
}
CLASS_OWNER = {
    "Basic 5": "tch_001",
    "Basic 6": "tch_002",
    "JHS 1": "tch_003",
    "JHS 2": "tch_005",
}

# One sheet per subject, so the governance screens each get a live example.
SHEET_TARGETS = [
    ResultSheet.Status.PUBLISHED,
    ResultSheet.Status.PUBLISHED,
    ResultSheet.Status.PUBLISHED,
    ResultSheet.Status.CONFIRMED,
    ResultSheet.Status.SUBMITTED,
    ResultSheet.Status.DRAFT,
]

# Upper bounds stop at x9.99 so a mark of 39.5 resolves to a grade
# instead of falling through a gap in the scale.
GRADE_BANDS = [
    ("0", "39.99", "F", "Fail", "0"),
    ("40", "49.99", "E", "Borderline pass", "1"),
    ("50", "59.99", "D", "Credit", "2"),
    ("60", "69.99", "C", "Credit", "3"),
    ("70", "79.99", "B", "Very good", "4"),
    ("80", "100", "A", "Excellent", "5"),
]

MALE_NAMES = [
    "Kwame", "Kofi", "Yaw", "Kojo", "Kwaku", "Fiifi", "Ebo", "Kwesi", "Nii",
    "Kobby", "Selorm", "Delali", "Kwadwo", "Mawuli", "Edem", "Kelvin",
    "Bright", "Emmanuel", "Isaac", "Prince",
]
FEMALE_NAMES = [
    "Ama", "Akosua", "Abena", "Efua", "Adjoa", "Esi", "Afia", "Yaa", "Naa",
    "Ewurama", "Sandra", "Priscilla", "Gifty", "Akua", "Dede", "Linda",
    "Comfort", "Esther", "Adwoa", "Mabel",
]
SURNAMES = [
    "Mensah", "Owusu", "Boateng", "Osei", "Adjei", "Asante", "Danso",
    "Frimpong", "Amoah", "Appiah", "Tetteh", "Quaye", "Annan", "Aidoo",
    "Darko", "Oppong", "Yeboah", "Agyeman", "Asare", "Baidoo",
]
OCCUPATIONS = [
    "Trader", "Teacher", "Nurse", "Driver", "Carpenter", "Seamstress",
    "Engineer", "Bank officer", "Farmer", "Pharmacist", "Tailor", "Chef",
]
AREAS = [
    "Adenta New Extension", "Dansoman", "East Legon", "Kasoa", "Madina",
    "Spintex", "Teshie", "Achimota",
]

PAYMENT_METHODS = [
    Payment.Method.CHEQUE,
    Payment.Method.MOBILE_MONEY,
    Payment.Method.BANK,
]

EXPENSES = [
    ("Repairs to the Block B roof guttering", Decimal("1450.00"), 26),
    ("Integrated Science lab consumables", Decimal("380.00"), 12),
    ("Diesel for the standby generator", Decimal("620.00"), 4),
]

# Row counts are diffed before/after the seed so the summary reports what was
# actually created rather than what the code intended to create.
TRACKED = [
    ("users", User),
    ("parent profiles", ParentProfile),
    ("students", Student),
    ("academic years", AcademicYear),
    ("terms", Term),
    ("classes", SchoolClass),
    ("subjects", Subject),
    ("class-subject maps", ClassSubject),
    ("teacher assignments", TeacherAssignment),
    ("fee structures", FeeStructure),
    ("invoices", Invoice),
    ("payments", Payment),
    ("adjustments", Adjustment),
    ("expenses", Expense),
    ("result sheets", ResultSheet),
    ("score entries", ScoreEntry),
    ("homework", Homework),
    ("homework submissions", HomeworkSubmission),
    ("announcements", Announcement),
    ("message threads", MessageThread),
    ("notifications", Notification),
    ("audit log rows", AuditLog),
]


class _DryRunRollback(Exception):
    """Raised at the end of --dry-run so the atomic block unwinds every write."""


def production_settings_module() -> bool:
    """
    True when the running settings module is the production one.

    ``DEBUG`` cannot be the signal here: the test suite deliberately runs with
    it forced off, so a DEBUG guard would make this command untestable.
    """
    return str(getattr(settings, "SETTINGS_MODULE", "")).endswith(".prod")


class Command(BaseCommand):
    help = "Seed the database with a realistic demo school."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run the whole seed, then roll every write back.",
        )
        parser.add_argument(
            "--students-per-class",
            type=int,
            default=STUDENTS_PER_CLASS,
            metavar="N",
            help=(
                "Pupils in each of the four demo classes "
                f"(default {STUDENTS_PER_CLASS}, "
                f"{MIN_STUDENTS_PER_CLASS}-{MAX_STUDENTS_PER_CLASS})."
            ),
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even under the production settings module.",
        )

    def handle(self, *args, **options):
        if production_settings_module() and not options["force"]:
            raise CommandError(
                "Demo logins are seeded with must_change_password=False. "
                f"Refusing to run under {settings.SETTINGS_MODULE}; pass "
                "--force if that is really what you want."
            )
        per_class = options["students_per_class"]
        if not MIN_STUDENTS_PER_CLASS <= per_class <= MAX_STUDENTS_PER_CLASS:
            raise CommandError(
                "--students-per-class must be between "
                f"{MIN_STUDENTS_PER_CLASS} and {MAX_STUDENTS_PER_CLASS} "
                f"(got {per_class})."
            )
        self.students_per_class = per_class
        self.dry_run = options["dry_run"]
        self.today = dt.date.today()
        self.rng = random.Random(SEED_RNG)
        self.notes: dict[str, int] = {}
        self.adopted: set[str] = set()
        self.world: dict = {}

        # The service layer emits business events through the "apps" logger;
        # silence it so the seed output stays readable.
        apps_logger = logging.getLogger("apps")
        previous_level = apps_logger.level
        apps_logger.setLevel(logging.ERROR)
        try:
            with transaction.atomic():
                before = self._snapshot()
                self.seed_all()
                deltas = {
                    label: after - before[label]
                    for label, after in self._snapshot().items()
                }
                if self.dry_run:
                    raise _DryRunRollback
        except _DryRunRollback:
            pass
        finally:
            apps_logger.setLevel(previous_level)

        self.summarise(deltas)

    # ─── Steps ───────────────────────────────────────────────────────────

    def seed_all(self) -> None:
        self.seed_school_settings()
        self.seed_staff()
        self.seed_academics()
        self.seed_teaching_structure()
        self.seed_students_and_guardians()
        self.seed_grading()
        self.seed_results()
        self.seed_finance()
        self.seed_homework()
        self.seed_communications()

    def seed_school_settings(self) -> None:
        """Fill blank identity fields only — never overwrite what is stored."""
        row = SchoolSettings.load()
        filled = 0
        for field, value in SCHOOL_IDENTITY.items():
            if not getattr(row, field):
                setattr(row, field, value)
                filled += 1
        if filled:
            row.save()
        self.notes["school settings fields filled"] = filled
        self.world["settings"] = row

    def seed_staff(self) -> None:
        staff = {}
        specs = [
            ("admin", User.Role.ADMIN, "Nana", "Osei-Bonsu", "STF-ADM-001"),
            ("director", User.Role.DIRECTOR, "Abena", "Kran", "STF-DIR-001"),
            ("finance", User.Role.FINANCE, "Samuel", "Aidoo", "STF-FIN-001"),
        ]
        for username, role, first, last, staff_id in specs:
            staff[username] = self.ensure_staff(username, role, first, last, staff_id)

        for index, (username, first, last, qualification, department) in enumerate(
            TEACHERS
        ):
            staff[username] = self.ensure_staff(
                username,
                User.Role.TEACHER,
                first,
                last,
                staff_id=f"TCH-{username[-3:]}",
                extra_profile={
                    "qualification": qualification,
                    "department": department,
                },
            )
            # ensure_teacher_profile() has no joining-date argument, so the
            # demo back-dates it on the profile — the staff list looks stale
            # otherwise.
            profile = staff[username].teacher_profile
            profile.date_joined = self.today - dt.timedelta(days=700 + 130 * index)
            profile.save()
        self.world["staff"] = staff
        self.world["admin"] = staff["admin"]
        if self.adopted:
            self.notes["staff accounts adopted"] = len(self.adopted)

    def ensure_staff(
        self,
        username: str,
        role: str,
        first: str,
        last: str,
        staff_id: str,
        *,
        extra_profile: dict | None = None,
    ) -> User:
        email = f"{username}@{DEMO_EMAIL_DOMAIN}"

        existing = User.objects.filter(username=username).first()
        if existing:
            if existing.role != role:
                raise CommandError(
                    f"Username '{username}' already belongs to a {existing.role} "
                    f"account; the seed needs it for a {role}. Rename or "
                    "deactivate that account before seeding demo data."
                )
            if existing.email != email:
                # A real account already owns this username — the project's own
                # admin, for example. It can act for the seed, but its password
                # belongs to its owner, so it is never reset to a demo one.
                self.adopted.add(username)
                return existing
            return self.apply_demo_login(existing, role)

        # create_staff_user derives the username from the email prefix, which
        # is what pins these accounts to the names in locustfile.py.
        user, _temp = accounts_services.create_staff_user(
            role=role,
            first_name=first,
            last_name=last,
            email=email,
            phone=self._phone_number(),
            staff_id=staff_id,
            send_credentials=False,
            extra_profile=extra_profile,
        )
        return self.apply_demo_login(user, role)

    def apply_demo_login(self, user: User, role: str) -> User:
        """Give a demo account its known password and skip the forced change."""
        user.set_password(DEMO_PASSWORDS[role])
        user.must_change_password = False
        user.save(
            update_fields=["password", "must_change_password", "updated_at"]
        )
        return user

    def assert_demo_username_available(self, username: str, email: str) -> None:
        """
        Refuse to touch an account whose username is already taken by someone
        else — seeding would otherwise reset a real user's password.
        """
        clash = User.objects.filter(username=username).exclude(email=email).first()
        if clash:
            raise CommandError(
                f"Username '{username}' already belongs to {clash.email}. "
                "Rename or deactivate that account before seeding demo data."
            )

    def seed_academics(self) -> None:
        admin = self.world["admin"]
        start_year = academics_calendar.current_start_year(self.today)
        self.world["start_year"] = start_year

        year_name = academics_calendar.year_name(start_year)
        year = AcademicYear.objects.filter(name=year_name).first()
        if year is None:
            start, end = academics_calendar.year_dates(start_year)
            year = academics_services.create_academic_year(
                name=year_name,
                start_date=start,
                end_date=end,
                is_current=True,
                actor=admin,
            )
        self.world["year"] = year

        terms = {}
        for term_name, start, end in academics_calendar.term_windows(start_year):
            term = Term.objects.filter(academic_year=year, name=term_name).first()
            if term is None:
                term = academics_services.create_term(
                    academic_year=year,
                    name=term_name,
                    start_date=start,
                    end_date=end,
                    actor=admin,
                )
            terms[term_name] = term

        in_or_before = [
            term for term in terms.values() if term.start_date <= self.today
        ]
        current = next(
            (
                term
                for term in terms.values()
                if term.start_date <= self.today <= term.end_date
            ),
            in_or_before[-1] if in_or_before else terms[Term.TermName.TERM_1],
        )
        if not current.is_current:
            academics_services.set_current_term(term=current, actor=admin)

        subjects = {}
        for code, name in SUBJECTS:
            subject = Subject.objects.filter(code=code).first()
            if subject is None:
                subject = academics_services.create_subject(
                    name=name, code=code, actor=admin
                )
            subjects[code] = subject

        classes, tuition = {}, {}
        for class_name, level_order, fee in CLASSES:
            school_class = SchoolClass.objects.filter(
                academic_year=year, name=class_name
            ).first()
            if school_class is None:
                school_class = academics_services.create_class(
                    academic_year=year,
                    name=class_name,
                    level_order=level_order,
                    actor=admin,
                )
            classes[class_name] = school_class
            tuition[class_name] = fee

        self.world.update(
            {
                "term": current,
                "terms": terms,
                "subjects": subjects,
                "classes": classes,
                "tuition": tuition,
            }
        )

    def seed_teaching_structure(self) -> None:
        admin = self.world["admin"]
        year = self.world["year"]
        subjects = self.world["subjects"]
        staff = self.world["staff"]

        for school_class in self.world["classes"].values():
            for code, subject in subjects.items():
                if not ClassSubject.objects.filter(
                    school_class=school_class, subject=subject, stream__isnull=True
                ).exists():
                    academics_services.map_class_subject(
                        school_class=school_class, subject=subject, actor=admin
                    )

                owner = staff[SUBJECT_OWNER[code]]
                profile = TeacherProfile.objects.get(user=owner)
                if not TeacherAssignment.objects.filter(
                    school_class=school_class,
                    subject=subject,
                    academic_year=year,
                    stream__isnull=True,
                ).exists():
                    teachers_services.assign_subject_to_teacher(
                        teacher=profile,
                        school_class=school_class,
                        subject=subject,
                        academic_year=year,
                        actor=admin,
                    )

        for class_name, teacher_username in CLASS_OWNER.items():
            school_class = self.world["classes"][class_name]
            teacher = staff[teacher_username]
            if school_class.class_teacher_id != teacher.pk:
                teachers_services.set_class_teacher(
                    school_class=school_class, teacher_user=teacher, actor=admin
                )
                self.notes["class teachers set"] = (
                    self.notes.get("class teachers set", 0) + 1
                )

    def seed_students_and_guardians(self) -> None:
        admin = self.world["admin"]
        year = self.world["year"]
        initials = self._school_initials()
        self.notes["students per class"] = self.students_per_class

        # Two families have a child in both of the junior classes so the parent
        # child-switcher has something to switch between.
        first_class, second_class = CLASSES[0][0], CLASSES[1][0]
        siblings = (self.students_per_class - 2, self.students_per_class - 1)
        sibling_links = {(second_class, slot): (first_class, slot) for slot in siblings}

        families: dict[int, dict] = {}
        slot_of: dict[tuple[str, int], int] = {}
        for class_name, _level, _fee in CLASSES:
            for index in range(self.students_per_class):
                link = sibling_links.get((class_name, index))
                if link is not None:
                    slot_of[(class_name, index)] = slot_of[link]
                    continue
                slot_of[(class_name, index)] = len(families)
                families[len(families)] = self._make_family(len(families))

        parents = {fid: self.ensure_parent(f) for fid, f in families.items()}
        self.world["parents"] = parents

        students_by_class: dict[str, list[Student]] = {}
        number = 0
        for class_name, level_order, _fee in CLASSES:
            school_class = self.world["classes"][class_name]
            roster = []
            for index in range(self.students_per_class):
                number += 1
                family_id = slot_of[(class_name, index)]
                roster.append(
                    self.ensure_student(
                        number=number,
                        initials=initials,
                        year_start=year.start_date.year,
                        school_class=school_class,
                        family=families[family_id],
                        parent=parents[family_id],
                        level_order=level_order,
                        actor=admin,
                    )
                )
            students_by_class[class_name] = roster

        self.world["students_by_class"] = students_by_class
        self.world["students"] = [
            student for roster in students_by_class.values() for student in roster
        ]

    def _school_initials(self) -> str:
        words = [
            word for word in self.world["settings"].name.split() if word[:1].isalpha()
        ]
        return "".join(word[0].upper() for word in words[:3]) or "SMS"

    def _stable_value(self, key: str) -> float:
        """
        A draw that depends only on ``key``, not on the draws before it.

        The seeded flags (who has paid, how able a student is) must land on the
        same rows every run — the shared ``self.rng`` sequence shifts when a
        re-run skips work, which would quietly create extra rows.
        """
        return random.Random(f"{SEED_RNG}:{key}").random()

    def _phone_number(self) -> str:
        return (
            f"+233 2{self.rng.randint(0, 4)} {self.rng.randint(1000000, 9999999):07d}"
        )

    def _make_family(self, family_id: int) -> dict:
        # Re-seed from the family id so the same guardian comes out of every
        # run, whatever the shared sequence looked like beforehand.
        self.rng = random.Random(f"{SEED_RNG}:family:{family_id}")
        is_father = self.rng.random() < 0.5
        return {
            "id": family_id,
            "surname": SURNAMES[family_id % len(SURNAMES)],
            "first_name": self.rng.choice(
                MALE_NAMES if is_father else FEMALE_NAMES
            ),
            "relationship": (
                StudentGuardian.Relationship.FATHER
                if is_father
                else StudentGuardian.Relationship.MOTHER
            ),
            "phone": self._phone_number(),
            "occupation": self.rng.choice(OCCUPATIONS),
            "area": self.rng.choice(AREAS),
        }

    def ensure_parent(self, family: dict) -> ParentProfile:
        username = f"par_{family['id'] + 1:03d}"
        email = f"{username}@{DEMO_EMAIL_DOMAIN}"
        self.assert_demo_username_available(username, email)

        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "first_name": family["first_name"],
                "last_name": family["surname"],
                "phone": family["phone"],
                "role": User.Role.PARENT,
            },
        )
        profile, _ = ParentProfile.objects.get_or_create(
            user=user,
            defaults={
                "occupation": family["occupation"],
                "address": f"{family['area']}, Accra",
            },
        )
        if created:
            self.apply_demo_login(user, User.Role.PARENT)
        return profile

    def ensure_student(
        self,
        *,
        number: int,
        initials: str,
        year_start: int,
        school_class: SchoolClass,
        family: dict,
        parent: ParentProfile,
        level_order: int,
        actor: User,
    ) -> Student:
        username = f"stu_{number:03d}"
        admission = f"{initials}/{str(year_start)[2:]}/{number:03d}"
        existing = Student.objects.filter(admission_number=admission).first()
        if existing:
            return existing

        self.assert_demo_username_available(username, f"{username}@{DEMO_EMAIL_DOMAIN}")

        self.rng = random.Random(f"{SEED_RNG}:student:{number}")
        gender = self.rng.choice([Student.Gender.MALE, Student.Gender.FEMALE])
        first_name = self.rng.choice(MALE_NAMES if gender == "M" else FEMALE_NAMES)
        birth_year = year_start - (5 + level_order)
        date_of_birth = dt.date(
            birth_year, self.rng.randint(1, 12), self.rng.randint(1, 28)
        )

        student = students_services.create_student_with_guardian(
            admission_number=admission,
            first_name=first_name,
            last_name=family["surname"],
            email=f"{username}@{DEMO_EMAIL_DOMAIN}",
            gender=gender,
            date_of_birth=date_of_birth,
            current_class=school_class,
            address=parent.address,
            existing_parent=parent,
            guardian_relationship=family["relationship"],
            actor=actor,
            send_credentials=False,
        )

        user = student.user
        # The service names students after their admission number; the demo
        # needs the predictable stu_001… form used by locustfile.py.
        user.username = username
        user.set_password(DEMO_PASSWORDS[User.Role.STUDENT])
        user.must_change_password = False
        user.save(
            update_fields=[
                "username",
                "password",
                "must_change_password",
                "updated_at",
            ]
        )
        return student

    def seed_grading(self) -> None:
        year = self.world["year"]
        admin = self.world["admin"]
        classes = list(self.world["classes"].values())
        if not GradingConfig.objects.filter(academic_year=year).exists():
            results_services.save_grading_config(
                academic_year=year,
                pass_mark=Decimal(50),
                actor=admin,
            )
        scale = GradeScale.objects.filter(academic_year=year).first()
        if scale is None:
            results_services.save_grade_scale(
                academic_year=year,
                name="Greenfield Standard Scale",
                bands=[
                    {
                        "min_score": Decimal(minimum),
                        "max_score": Decimal(maximum),
                        "grade": grade,
                        "remark": remark,
                        "points": Decimal(points),
                    }
                    for minimum, maximum, grade, remark, points in GRADE_BANDS
                ],
                classes=classes,
                actor=admin,
            )
        else:
            # The year already had a scale before this run — a school that set its
            # own bands, or a second seed. Attach the demo classes to it and grade
            # their marks through it; save_grade_scale would overwrite the bands.
            unlinked = [c for c in classes if c.grade_scale_id != scale.pk]
            if unlinked:
                SchoolClass.objects.filter(pk__in=[c.pk for c in unlinked]).update(
                    grade_scale=scale
                )
                results_services.regrade_entries_for_classes(
                    classes=classes, actor=admin, scale=scale
                )
        # Both paths link classes with a queryset update, which does not touch the
        # in-memory copies held in self.world; refresh so score entry resolves
        # grades through the scale rather than the empty fallback.
        for school_class in classes:
            school_class.refresh_from_db(fields=["grade_scale"])

    def seed_results(self) -> None:
        term = self.world["term"]
        subjects = self.world["subjects"]
        staff = self.world["staff"]
        abilities = {
            student.pk: 0.40 + 0.57 * self._stable_value(f"ability:{student.pk}")
            for student in self.world["students"]
        }

        for class_name, roster in self.world["students_by_class"].items():
            school_class = self.world["classes"][class_name]
            for position, (code, subject) in enumerate(subjects.items()):
                teacher = staff[SUBJECT_OWNER[code]]
                profile = TeacherProfile.objects.get(user=teacher)

                sheet = ResultSheet.objects.filter(
                    school_class=school_class,
                    subject=subject,
                    term=term,
                    stream__isnull=True,
                ).first()
                if sheet is None:
                    sheet = results_services.create_result_sheet(
                        school_class=school_class,
                        subject=subject,
                        term=term,
                        teacher=profile,
                        actor=teacher,
                    )
                elif sheet.teacher_id != profile.pk:
                    sheet.teacher = profile
                    sheet.save(update_fields=["teacher", "updated_at"])

                # Confirmed and published sheets are locked by design, so a
                # re-run leaves their scores exactly as they stand.
                if not sheet.is_locked:
                    target = SHEET_TARGETS[position]
                    entered = (
                        roster
                        if target != ResultSheet.Status.DRAFT
                        else roster[:-3]
                    )
                    for student in entered:
                        self.record_scores(
                            sheet, student, abilities[student.pk], teacher
                        )
                    self.advance_sheet(sheet, target, teacher)

    def record_scores(
        self,
        sheet: ResultSheet,
        student: Student,
        ability: float,
        actor: User,
    ) -> None:
        draw = self._stable_value(f"score:{sheet.pk}:{student.pk}")
        mark = (ability + (draw - 0.5) * 0.18) * 100
        results_services.upsert_score_entry(
            sheet=sheet,
            student=student,
            raw_score=f"{min(96.0, max(5.0, mark)):.1f}",
            actor=actor,
        )

    def advance_sheet(self, sheet: ResultSheet, target: str, actor: User) -> None:
        order = [
            ResultSheet.Status.DRAFT,
            ResultSheet.Status.SUBMITTED,
            ResultSheet.Status.CONFIRMED,
            ResultSheet.Status.PUBLISHED,
        ]
        admin = self.world["admin"]
        while order.index(sheet.status) < order.index(target):
            nxt = order[order.index(sheet.status) + 1]
            if nxt == ResultSheet.Status.SUBMITTED:
                results_services.submit_sheet(sheet=sheet, actor=actor)
            elif nxt == ResultSheet.Status.CONFIRMED:
                results_services.confirm_sheet(sheet=sheet, actor=admin)
            else:
                results_services.publish_sheet(sheet=sheet, actor=admin)
            sheet.refresh_from_db()

    def seed_finance(self) -> None:
        finance_services.seed_accounts()
        admin = self.world["admin"]
        officer = self.world["staff"]["finance"]
        term = self.world["term"]

        for class_name, school_class in self.world["classes"].items():
            base = self.world["tuition"][class_name]
            if FeeStructure.objects.filter(
                school_class=school_class, term=term, is_active=True
            ).exists():
                continue
            finance_services.create_fee_structure(
                school_class=school_class,
                term=term,
                items=[
                    {"label": "Tuition", "amount": base, "ordering": 1},
                    {
                        "label": "Meals",
                        "amount": (base * Decimal("0.55")).quantize(Decimal("0.01")),
                        "ordering": 2,
                    },
                    {
                        "label": "Transport",
                        "amount": (base * Decimal("0.40")).quantize(Decimal("0.01")),
                        "ordering": 3,
                    },
                    {"label": "PTA levy", "amount": Decimal("20.00"), "ordering": 4},
                ],
                note="Seeded demo fee structure.",
                actor=officer,
            )

        # post_invoices() writes an audit row even when it turns out nobody is
        # due a bill, so skip it once the term is fully invoiced — otherwise a
        # re-run leaves a stray log entry behind.
        unbilled = Student.objects.filter(
            status__in=["active", "repeating"],
            current_class__academic_year=term.academic_year,
        ).exclude(invoices__term=term).exists()
        if unbilled:
            finance_services.post_invoices(term=term, actor=officer)

        collected = 0
        invoices = Invoice.objects.filter(term=term).select_related("student__user")
        for invoice in invoices:
            username = invoice.student.user.username
            share = self._stable_value(f"fees:{username}")
            if share < 0.30:
                continue  # roughly three in ten wards are still owing
            amount = invoice.total_amount
            if share < 0.70:
                amount = (invoice.total_amount * Decimal("0.45")).quantize(
                    Decimal("0.01")
                )
            reference = f"DEMO-{username.upper()}"
            if Payment.objects.filter(reference=reference).exists():
                continue
            # The first handful land today so the "collected today" KPI card
            # on the admin home has a live number behind it; the rest spread
            # across the fortnight before it.
            days_ago = 0 if collected < 6 else 1 + int(
                13 * self._stable_value(f"paid-on:{username}")
            )
            finance_services.record_payment(
                student=invoice.student,
                amount=amount,
                method=PAYMENT_METHODS[collected % len(PAYMENT_METHODS)],
                reference=reference,
                payment_date=self.today - dt.timedelta(days=days_ago),
                notes="Seeded demo payment.",
                recorded_by=officer,
            )
            collected += 1

        scholarship_student = self.world["students"][0]
        reason = "Academic merit scholarship"
        if not Adjustment.objects.filter(
            student=scholarship_student, reason=reason
        ).exists():
            finance_services.apply_adjustment(
                student=scholarship_student,
                invoice=Invoice.objects.filter(
                    student=scholarship_student, term=term
                ).first(),
                adjustment_type=Adjustment.AdjustmentType.SCHOLARSHIP,
                amount=Decimal("150.00"),
                reason=reason,
                actor=admin,
            )

        expenses_account = Account.objects.get(code=GENERAL_EXPENSES_CODE)
        for description, amount, days_ago in EXPENSES:
            if Expense.objects.filter(description=description).exists():
                continue
            expense = finance_services.record_expense(
                account=expenses_account,
                amount=amount,
                expense_date=self.today - dt.timedelta(days=days_ago),
                description=description,
                approval_note="Approved by the school administrator.",
                actor=officer,
            )
            if expense.status == Expense.Status.PENDING:
                finance_services.post_expense(expense=expense, actor=admin)

    def seed_homework(self) -> None:
        staff = self.world["staff"]
        term = self.world["term"]
        student_of = self.world["students_by_class"]

        for school_class in self.world["classes"].values():
            two_subjects = list(self.world["subjects"].items())[:2]
            roster = student_of[school_class.name]
            for offset, (code, subject) in enumerate(two_subjects):
                title = f"{subject.name}: worksheet {offset + 1}"
                homework = Homework.objects.filter(
                    title=title, school_class=school_class, subject=subject
                ).first()
                if homework is None:
                    homework = Homework.objects.create(
                        title=title,
                        description=(
                            "Complete exercises 1 to 10 in the workbook and hand it "
                            "in at the start of the next lesson."
                        ),
                        subject=subject,
                        school_class=school_class,
                        term=term,
                        set_by=staff[SUBJECT_OWNER[code]],
                        # One item already overdue, one still upcoming.
                        due_date=self.today + dt.timedelta(days=2 if offset else -1),
                        status=Homework.Status.PUBLISHED,
                    )
                self.seed_homework_submissions(homework, roster)

    def seed_homework_submissions(
        self, homework: Homework, roster: list[Student]
    ) -> None:
        """
        Roughly one pupil in four hands each piece in.

        The overdue worksheet is handed in after its due date, so its rows carry
        ``is_late`` — that is what the teacher's review page counts. No files are
        attached: the note alone is enough to exercise the page, and a seed that
        wrote uploads would have to clean them out of the media root again.
        """
        for index, student in enumerate(roster):
            if index % 4:
                continue
            if HomeworkSubmission.objects.filter(
                homework=homework, student=student
            ).exists():
                continue
            homework_services.submit_homework(
                homework=homework,
                student=student,
                note="Exercises 1 to 10 are done; working is in the exercise book.",
                actor=student.user,
            )

    def seed_communications(self) -> None:
        admin = self.world["admin"]
        students = self.world["students"]

        announcements = [
            {
                "title": "Reopening date and uniform checklist",
                "body": (
                    "The school reopens on the first Monday of the term. Please "
                    "ensure every item on the uniform list is named before your "
                    "ward reports."
                ),
                "audience": Announcement.Audience.EVERYONE,
                "target_roles": None,
                "target_class": None,
            },
            {
                "title": "Staff meeting: mid-term review",
                "body": (
                    "All teaching staff are expected in the staff room after "
                    "school on Friday. Agenda: lesson coverage, broadsheet "
                    "sign-off and the homework load policy."
                ),
                "audience": Announcement.Audience.BY_ROLE,
                "target_roles": ["admin", "director", "teacher", "finance"],
                "target_class": None,
            },
            {
                "title": "JHS 2 end-of-term excursion",
                "body": (
                    "The JHS 2 trip to the botanical gardens is confirmed. "
                    "Consent forms must be returned by Friday and each ward "
                    "should bring lunch money."
                ),
                "audience": Announcement.Audience.BY_CLASS,
                "target_roles": None,
                "target_class": self.world["classes"]["JHS 2"],
            },
        ]
        for spec in announcements:
            if Announcement.objects.filter(title=spec["title"]).exists():
                continue
            comms_services.create_announcement(
                title=spec["title"],
                body=spec["body"],
                audience=spec["audience"],
                target_roles=spec["target_roles"],
                target_class_ids=(
                    [spec["target_class"].pk] if spec["target_class"] else None
                ),
                created_by=admin,
            )

        # A complaint from a parent, an inquiry from a student, and a piece of
        # feedback — one per inbox status so the triage screens are populated.
        threads = [
            {
                "type": MessageThread.MessageType.COMPLAINT,
                "subject": "Noise in the JHS dormitory after lights out",
                "body": (
                    "My ward says the senior boys keep their lights on well past "
                    "the stated time and he cannot sleep. Could this be looked "
                    "into?"
                ),
                "sender": self._parent_of(students[0]),
                "on_behalf_of": students[0],
                "reply": (
                    "Thank you for flagging this. The duty master has been "
                    "instructed to do a 22:00 round and we will report back."
                ),
                "status": MessageThread.Status.IN_PROGRESS,
            },
            {
                "type": MessageThread.MessageType.INQUIRY,
                "subject": "Missing Computing score on my result slip",
                "body": (
                    "My slip shows scores for five subjects but Computing is "
                    "blank. Please confirm whether it will be released later."
                ),
                "sender": students[1].user,
                "on_behalf_of": None,
                "reply": None,
                "status": MessageThread.Status.OPEN,
            },
            {
                "type": MessageThread.MessageType.SATISFACTION,
                "subject": "Appreciation for the JHS 2 team",
                "body": (
                    "I want to record my thanks for the extra lessons the JHS 2 "
                    "teachers put in this term. My ward's science grade moved "
                    "from D to B and it is down to them."
                ),
                "sender": self._parent_of(students[-1]),
                "on_behalf_of": students[-1],
                "reply": "Noted with thanks — this will be shared with the team.",
                "status": MessageThread.Status.RESOLVED,
            },
        ]
        for spec in threads:
            thread = MessageThread.objects.filter(
                sender=spec["sender"], subject=spec["subject"]
            ).first()
            if thread is None:
                thread = comms_services.create_message_thread(
                    sender=spec["sender"],
                    message_type=spec["type"],
                    subject=spec["subject"],
                    body=spec["body"],
                    on_behalf_of=spec["on_behalf_of"],
                )
            if spec["reply"] and not thread.messages.filter(is_opening=False).exists():
                comms_services.reply_to_thread(
                    thread=thread, author=admin, body=spec["reply"]
                )
            if spec["status"] != MessageThread.Status.OPEN and (
                thread.assigned_to_id is None
            ):
                comms_services.assign_thread(thread=thread, assignee=admin, actor=admin)
            if thread.status != spec["status"]:
                comms_services.update_thread_status(
                    thread=thread, status=spec["status"]
                )

    def _parent_of(self, student: Student) -> User:
        link = student.guardian_links.select_related("parent__user").first()
        return link.parent.user if link else student.user

    # ─── Output ──────────────────────────────────────────────────────────

    def _snapshot(self) -> dict[str, int]:
        return {label: model.objects.count() for label, model in TRACKED}

    def summarise(self, deltas: dict[str, int]) -> None:
        heading = (
            "Dry run complete - every write was rolled back."
            if self.dry_run
            else "Demo data ready."
        )
        self.stdout.write(self.style.SUCCESS(heading))
        self.stdout.write("")
        for label, _model in TRACKED:
            created = deltas.get(label, 0)
            marker = "+" if created else " "
            self.stdout.write(f"  {marker} {label:<22} {created:>5}")
        for label, count in sorted(self.notes.items()):
            if count:
                self.stdout.write(f"    {label:<22} {count:>5}")

        self.stdout.write("")
        self.stdout.write("Demo logins (username / password / role):")
        rows = [
            ("admin", User.Role.ADMIN, "School administrator"),
            ("director", User.Role.DIRECTOR, "Director"),
            ("finance", User.Role.FINANCE, "Finance officer"),
            ("tch_001", User.Role.TEACHER, "Teacher - class teacher, Basic 5"),
            ("tch_002", User.Role.TEACHER, "Teacher - class teacher, Basic 6"),
            ("tch_003", User.Role.TEACHER, "Teacher - class teacher, JHS 1"),
            ("tch_004", User.Role.TEACHER, "Teacher"),
            ("stu_001", User.Role.STUDENT, "Student, Basic 5"),
            ("par_001", User.Role.PARENT, "Parent of stu_001"),
        ]
        for username, role, label in rows:
            password = DEMO_PASSWORDS[role]
            if username in self.adopted:
                self.stdout.write(
                    f"  {username:<10} {'(existing)':<16} {label} "
                    "- login kept, password unchanged"
                )
                continue
            self.stdout.write(f"  {username:<10} {password:<16} {label}")
        self.stdout.write("")
        self.stdout.write(
            f"  Also seeded: stu_001-stu_{len(self.world['students']):03d} and "
            f"par_001-par_{len(self.world['parents']):03d}."
        )
