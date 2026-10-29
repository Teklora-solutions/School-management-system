"""
scripts/gen_dashboard_templates.py
Generates the 5 template files for each of the 6 role dashboard shells.
Run from the project root: python scripts/gen_dashboard_templates.py
"""

import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
TMPL = PROJECT_ROOT / "frontend" / "templates" / "dashboards"

ROLES = {
    "admin": {
        "label": "Admin",
        "icon": "bi-shield-fill",
        "color_var": "--accent",  # indigo
        "welcome": "School Administration Panel",
        "subtitle": "Manage the entire school from one place.",
    },
    "director": {
        "label": "Director",
        "icon": "bi-binoculars-fill",
        "color_var": "--accent",  # sky
        "welcome": "Director's Dashboard",
        "subtitle": "School-wide analytics and oversight.",
    },
    "teacher": {
        "label": "Teacher",
        "icon": "bi-person-workspace",
        "color_var": "--accent",  # emerald
        "welcome": "Teacher Dashboard",
        "subtitle": "Manage results, homework, and your classes.",
    },
    "student": {
        "label": "Student",
        "icon": "bi-backpack-fill",
        "color_var": "--accent",  # amber
        "welcome": "Student Portal",
        "subtitle": "View your results, homework, and fee statement.",
    },
    "finance": {
        "label": "Finance Officer",
        "icon": "bi-wallet2",
        "color_var": "--accent",  # violet
        "welcome": "Finance Dashboard",
        "subtitle": "Manage payments, ledger, and fee structures.",
    },
    "parent": {
        "label": "Parent / Guardian",
        "icon": "bi-house-heart-fill",
        "color_var": "--accent",  # pink
        "welcome": "Parent Portal",
        "subtitle": "Stay connected with your child's progress.",
    },
}


# ── Templates ─────────────────────────────────────────────────────────────────


def make_navbar(role: str, info: dict) -> str:
    return """{% extends "base.html" %}
{% comment %} This file is intentionally empty — navbar is in base.html {% endcomment %}
"""


def make_sidebar(role: str, info: dict) -> str:
    return """{% load static %}
<aside class="app-sidebar" id="app-sidebar" role="navigation" aria-label="Sidebar">
  {% include "partials/_sidebar_nav.html" %}
</aside>
"""


def make_footer(role: str, info: dict) -> str:
    return """{% load static %}
<footer class="app-footer" role="contentinfo">
  <p>&copy; {% now "Y" %} <strong>{{ SCHOOL_NAME }}</strong>. All rights reserved.</p>
</footer>
"""


def make_base(role: str, info: dict) -> str:
    return f"""{{% extends "base.html" %}}
{{% comment %}}
  dashboards/{role}/base.html
  Role shell for {info['label']}.
  Feature pages extend this via the base_template context variable.
{{% endcomment %}}

{{% block sidebar %}}
  {{% include "dashboards/{role}/_sidebar.html" %}}
{{% endblock %}}

{{% block footer %}}
  {{% include "dashboards/{role}/_footer.html" %}}
{{% endblock %}}
"""


def make_home(role: str, info: dict) -> str:
    return f"""{{% extends "dashboards/{role}/base.html" %}}

{{% block title %}}{info['label']} Dashboard | {{{{ SCHOOL_NAME }}}}{{% endblock %}}

{{% block content %}}
<div class="page-header">
  <h1 class="page-title">
    <i class="bi {info['icon']} me-2" style="color:var(--accent-light);" aria-hidden="true"></i>
    {info['welcome']}
  </h1>
  <p class="page-subtitle">{info['subtitle']}</p>
</div>

<div class="row g-3">
  <div class="col-sm-6 col-xl-3">
    <div class="stat-card">
      <div class="stat-icon"><i class="bi bi-people-fill" aria-hidden="true"></i></div>
      <div class="stat-body">
        <div class="stat-value">–</div>
        <div class="stat-label">Students</div>
      </div>
    </div>
  </div>
  <div class="col-sm-6 col-xl-3">
    <div class="stat-card">
      <div class="stat-icon"><i class="bi bi-person-badge-fill" aria-hidden="true"></i></div>
      <div class="stat-body">
        <div class="stat-value">–</div>
        <div class="stat-label">Teachers</div>
      </div>
    </div>
  </div>
  <div class="col-sm-6 col-xl-3">
    <div class="stat-card">
      <div class="stat-icon"><i class="bi bi-cash-coin" aria-hidden="true"></i></div>
      <div class="stat-body">
        <div class="stat-value">–</div>
        <div class="stat-label">Fees Collected</div>
      </div>
    </div>
  </div>
  <div class="col-sm-6 col-xl-3">
    <div class="stat-card">
      <div class="stat-icon"><i class="bi bi-graph-up-arrow" aria-hidden="true"></i></div>
      <div class="stat-body">
        <div class="stat-value">–</div>
        <div class="stat-label">Avg. Score</div>
      </div>
    </div>
  </div>
</div>

<div class="row g-3 mt-1">
  <div class="col-12">
    <div class="card">
      <div class="card-header">
        <h2 class="card-title">
          <i class="bi bi-lightning-fill me-2" style="color:var(--accent-light);" aria-hidden="true"></i>
          Quick Actions
        </h2>
      </div>
      <div class="card-body">
        <div class="d-flex flex-wrap gap-2">
          <a href="{{{{ request.user.get_dashboard_url }}}}" class="btn btn-outline-primary btn-sm">
            <i class="bi bi-arrow-clockwise me-1"></i>Refresh
          </a>
          <a href="{{% url 'accounts:profile' %}}" class="btn btn-outline-primary btn-sm">
            <i class="bi bi-person me-1"></i>My Profile
          </a>
        </div>
      </div>
    </div>
  </div>
</div>

<div class="row g-3 mt-1">
  <div class="col-12">
    <div class="card">
      <div class="card-header">
        <h2 class="card-title">
          <i class="bi bi-megaphone-fill me-2" style="color:var(--accent-light);" aria-hidden="true"></i>
          Recent Announcements
        </h2>
      </div>
      <div class="card-body">
        <p class="text-muted-sm">No announcements yet. Announcements will appear here in Phase 5.</p>
      </div>
    </div>
  </div>
</div>
{{% endblock content %}}
"""


def write(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8")
        print(f"  created {path.relative_to(PROJECT_ROOT)}")
    else:
        print(f"  exists  {path.relative_to(PROJECT_ROOT)}")


for role, info in ROLES.items():
    d = TMPL / role
    write(d / "_navbar.html", make_navbar(role, info))
    write(d / "_sidebar.html", make_sidebar(role, info))
    write(d / "_footer.html", make_footer(role, info))
    write(d / "base.html", make_base(role, info))
    write(d / "home.html", make_home(role, info))

print("done")
