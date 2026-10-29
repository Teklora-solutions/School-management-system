"""Script to generate stub views.py and urls.py for remaining apps."""

import pathlib

base = pathlib.Path(__file__).resolve().parent.parent / "backend" / "apps"

view_content = """\
\"\"\"apps/{app}/views.py  — stub, full implementation in Phase 2+\"\"\"
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView


class ComingSoonView(LoginRequiredMixin, TemplateView):
    template_name = "partials/_coming_soon.html"
"""

url_header = """\
\"\"\"apps/{app}/urls.py — stub routes, full implementation in Phase 2+\"\"\"
from django.urls import path
from .views import ComingSoonView

app_name = "{app_name}"

urlpatterns = [
"""

stubs = {
    "academics": (
        "academics",
        [
            ("year_list", ""),
        ],
    ),
    "students": (
        "students",
        [
            ("list", ""),
            ("bulk_import", "import/"),
            ("promote", "promote/"),
        ],
    ),
    "teachers": (
        "teachers",
        [
            ("list", ""),
            ("my_classes", "my-classes/"),
        ],
    ),
    "results": (
        "results",
        [
            ("list", ""),
            ("entry", "entry/"),
            ("student_results", "my-results/"),
        ],
    ),
    "finance": (
        "finance",
        [
            ("overview", ""),
            ("ledger", "ledger/"),
            ("payment_list", "payments/"),
            ("fee_structure_list", "fee-structures/"),
            ("reports", "reports/"),
            ("student_statement", "my-statement/"),
        ],
    ),
    "homework": (
        "homework",
        [
            ("list", ""),
        ],
    ),
    "attendance": (
        "attendance",
        [
            ("list", ""),
        ],
    ),
    "communications": (
        "communications",
        [
            ("list", ""),
            ("complaint_create", "complaint/new/"),
        ],
    ),
}

for app, (app_name, routes) in stubs.items():
    app_dir = base / app
    app_dir.mkdir(exist_ok=True)

    vpath = app_dir / "views.py"
    if not vpath.exists():
        vpath.write_text(view_content.format(app=app), encoding="utf-8")
        print(f"  created {app}/views.py")
    else:
        print(f"  exists  {app}/views.py")

    upath = app_dir / "urls.py"
    if not upath.exists():
        lines = [url_header.format(app=app, app_name=app_name)]
        for name, route in routes:
            lines.append(
                f'    path("{route}", ComingSoonView.as_view(), name="{name}"),\n'
            )
        lines.append("]\n")
        upath.write_text("".join(lines), encoding="utf-8")
        print(f"  created {app}/urls.py")
    else:
        print(f"  exists  {app}/urls.py")

print("done")
