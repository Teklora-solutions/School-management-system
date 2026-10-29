"""
locustfile.py
-------------
Load test for the School Management System.

Scenarios:
  1. LoginUser      — POST /login/, follow redirect to dashboard (all roles)
  2. StudentResults — GET /results/ (student role)
  3. FeeStatement   — GET /finance/statement/ (student/parent role)
  4. DirectorHome   — GET /dashboards/director/ (director role, cache warm/cold)
  5. StaffList      — GET /students/ with pagination (admin role)

Run:
  locust -f locustfile.py --host http://localhost:8000

For CI headless run:
  locust -f locustfile.py --host http://localhost:8000 \
         --users 50 --spawn-rate 5 --run-time 60s --headless \
         --csv docs/performance_baseline

Requirements:
  pip install locust>=2.29
"""

from __future__ import annotations

import random

from locust import HttpUser, between, task
from locust.exception import StopUser

# ---------------------------------------------------------------------------
# Credentials — override via environment variables or edit here.
# These must match accounts created by scripts/seed_demo_data.py
# ---------------------------------------------------------------------------

ADMIN_CREDS = {"username": "admin", "password": "AdminPass123!"}
STUDENT_CREDS = {"username": "stu_001", "password": "StudentPass1!"}
PARENT_CREDS = {"username": "par_001", "password": "ParentPass1!"}
DIRECTOR_CREDS = {"username": "director", "password": "DirPass123!"}
FINANCE_CREDS = {"username": "finance", "password": "FinPass123!"}


def _login(client, credentials: dict) -> bool:
    """POST login form and return True if login succeeded."""
    # Fetch CSRF token first
    resp = client.get("/login/", name="/login/ [GET]")
    if resp.status_code != 200:
        return False
    csrftoken = resp.cookies.get("csrftoken", "")

    resp = client.post(
        "/login/",
        data={
            "username": credentials["username"],
            "password": credentials["password"],
            "csrfmiddlewaretoken": csrftoken,
        },
        headers={"Referer": client.base_url + "/login/"},
        allow_redirects=True,
        name="/login/ [POST]",
    )
    # A successful login redirects to a dashboard; check we didn't stay on /login/
    return "/login/" not in resp.url


# ---------------------------------------------------------------------------
# User classes
# ---------------------------------------------------------------------------


class StudentUser(HttpUser):
    """
    Simulates a student checking their results and fee statement.
    Weight = 40 (most users are students).
    """

    wait_time = between(1, 4)
    weight = 40

    def on_start(self):
        if not _login(self.client, STUDENT_CREDS):
            raise StopUser()

    @task(3)
    def view_results(self):
        self.client.get("/results/", name="/results/")

    @task(2)
    def view_fee_statement(self):
        self.client.get("/finance/statement/", name="/finance/statement/")

    @task(1)
    def view_announcements(self):
        self.client.get("/communications/", name="/communications/ [list]")

    @task(1)
    def view_dashboard(self):
        self.client.get("/dashboards/student/", name="/dashboards/student/")


class ParentUser(HttpUser):
    """
    Simulates a parent checking their child's results.
    Weight = 25.
    """

    wait_time = between(2, 5)
    weight = 25

    def on_start(self):
        if not _login(self.client, PARENT_CREDS):
            raise StopUser()

    @task(3)
    def view_results(self):
        self.client.get("/results/", name="/results/ [parent]")

    @task(2)
    def view_fee_statement(self):
        self.client.get("/finance/statement/", name="/finance/statement/ [parent]")

    @task(1)
    def view_dashboard(self):
        self.client.get("/dashboards/parent/", name="/dashboards/parent/")


class AdminUser(HttpUser):
    """
    Simulates an admin managing students.
    Weight = 10.
    """

    wait_time = between(2, 6)
    weight = 10

    def on_start(self):
        if not _login(self.client, ADMIN_CREDS):
            raise StopUser()

    @task(3)
    def list_students(self):
        page = random.randint(1, 5)
        self.client.get(f"/students/?page={page}", name="/students/ [list]")

    @task(2)
    def list_staff(self):
        self.client.get("/accounts/staff/", name="/accounts/staff/ [list]")

    @task(1)
    def academics_list(self):
        self.client.get("/academics/", name="/academics/ [list]")

    @task(1)
    def dashboard(self):
        self.client.get("/dashboards/admin/", name="/dashboards/admin/")


class DirectorUser(HttpUser):
    """
    Simulates a director reviewing the executive dashboard.
    Weight = 5 (few directors).
    """

    wait_time = between(3, 8)
    weight = 5

    def on_start(self):
        if not _login(self.client, DIRECTOR_CREDS):
            raise StopUser()

    @task(4)
    def director_home(self):
        # Tests both cold-cache (first hit) and warm-cache (subsequent hits)
        self.client.get("/dashboards/director/", name="/dashboards/director/")

    @task(2)
    def class_performance(self):
        self.client.get(
            "/dashboards/director/performance/",
            name="/dashboards/director/performance/",
        )

    @task(2)
    def fee_analysis(self):
        self.client.get("/dashboards/director/fees/", name="/dashboards/director/fees/")

    @task(1)
    def staff_directory(self):
        self.client.get(
            "/dashboards/director/staff/", name="/dashboards/director/staff/"
        )

    @task(1)
    def books_overview(self):
        self.client.get(
            "/dashboards/director/books/", name="/dashboards/director/books/"
        )


class FinanceUser(HttpUser):
    """
    Simulates a finance officer reviewing payments.
    Weight = 5.
    """

    wait_time = between(2, 5)
    weight = 5

    def on_start(self):
        if not _login(self.client, FINANCE_CREDS):
            raise StopUser()

    @task(3)
    def payment_list(self):
        page = random.randint(1, 3)
        self.client.get(
            f"/finance/payments/?page={page}", name="/finance/payments/ [list]"
        )

    @task(2)
    def finance_overview(self):
        self.client.get("/finance/", name="/finance/overview/")

    @task(1)
    def ledger(self):
        self.client.get("/finance/ledger/", name="/finance/ledger/")
