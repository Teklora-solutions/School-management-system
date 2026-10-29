"""
apps/accounts/urls.py
"""

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.PortalChooserView.as_view(), name="login"),
    path("login/<slug:role>/", views.RoleLoginView.as_view(), name="role_login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    # Must keep its own path: "/" belongs to the public site (config/urls.py
    # includes apps.public first), so a root-mounted stub would resolve to the
    # website and "go to my dashboard" would silently serve the home page.
    path("dashboard/", views.DashboardRedirectView.as_view(), name="dashboard_redirect"),
    path(
        "change-password/",
        views.ForcePasswordChangeView.as_view(),
        name="force_password_change",
    ),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("my-children/", views.ChildrenView.as_view(), name="children"),
    path("my-children/select/", views.SelectChildView.as_view(), name="select_child"),
    # Staff management
    path("staff/", views.StaffUserListView.as_view(), name="staff_list"),
    path("staff/add/", views.StaffUserCreateView.as_view(), name="staff_create"),
    path("staff/<int:pk>/", views.StaffUserDetailView.as_view(), name="staff_detail"),
    path(
        "staff/<int:pk>/deactivate/",
        views.StaffUserDeactivateView.as_view(),
        name="staff_deactivate",
    ),
    path(
        "staff/<int:pk>/reactivate/",
        views.StaffUserReactivateView.as_view(),
        name="staff_reactivate",
    ),
    path(
        "staff/<int:pk>/reset-password/",
        views.StaffResetPasswordView.as_view(),
        name="staff_reset_password",
    ),
]
