"""
config/urls.py
--------------
Root URL configuration. Each app registers its own urls under a namespace.
"""

from apps.accounts.views import PortalChooserView, StaffBlockedPasswordResetView
from apps.core.health import LivenessView
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    # Django admin (keep under a non-obvious path in production)
    path("django-admin/", admin.site.urls),
    # ── Custom password reset (overrides django.contrib.auth.urls) ──────
    # Staff roles (admin, director, teacher, finance) are blocked from
    # self-service reset; admin-only reset is at
    # /accounts/staff/<pk>/reset-password/.
    # This must come BEFORE the contrib auth.urls include so Django
    # resolves our view first for /accounts/password_reset/.
    path(
        "accounts/password_reset/",
        StaffBlockedPasswordResetView.as_view(),
        name="password_reset",
    ),
    # Django's default login route must not fall through to contrib (the project
    # has no registration/login.html), so point it at the same portal chooser as
    # /login/ — one answer wherever a redirect or a bookmark arrives.
    path(
        "accounts/login/",
        PortalChooserView.as_view(),
        name="login_default",
    ),
    # Django auth: provides password_reset/done/, reset/<uidb64>/<token>/,
    # reset/done/, password_change/, password_change/done/, login/, logout/.
    # The password_reset entry point above is overridden by our custom view;
    # the remaining URLs (confirm, done) use the built-in views.
    path("accounts/", include("django.contrib.auth.urls")),
    # Health / readiness probes (no auth required — for load-balancer / k8s)
    path("healthz/", LivenessView.as_view(), name="liveness"),
    path("readyz/", include("apps.core.urls")),
    # Audit log viewer (admin only)
    path("audit/", include("apps.core.urls_audit", namespace="core")),
    # School-wide configuration (admin only)
    path("school/", include("apps.core.urls_school", namespace="core_school")),
    # Activity feed and daily report (admin only)
    path("activity/", include("apps.core.urls_activity", namespace="core_activity")),
    # App routes
    # The school's own website: open to anyone.
    # public.urls owns the root path so "/" serves the home page directly -
    # no redirect for unauthenticated visitors.
    path("", include("apps.public.urls", namespace="public")),
    # ...and the school's own screens for editing it, behind their own prefix so
    # a visitor's /gallery/ and an admin's /website/gallery/ never compete.
    path("website/", include("apps.public.urls_admin", namespace="public_admin")),
    path("", include("apps.accounts.urls", namespace="accounts")),
    path("academics/", include("apps.academics.urls", namespace="academics")),
    path("students/", include("apps.students.urls", namespace="students")),
    path("teachers/", include("apps.teachers.urls", namespace="teachers")),
    path("results/", include("apps.results.urls", namespace="results")),
    path("finance/", include("apps.finance.urls", namespace="finance")),
    path("homework/", include("apps.homework.urls", namespace="homework")),
    path("attendance/", include("apps.attendance.urls", namespace="attendance")),
    path(
        "communications/",
        include("apps.communications.urls", namespace="communications"),
    ),
    path("dashboards/", include("apps.dashboards.urls", namespace="dashboards")),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Debug toolbar
if settings.DEBUG:
    try:
        import debug_toolbar

        urlpatterns = [path("__debug__/", include(debug_toolbar.urls))] + urlpatterns
    except ImportError:
        pass
