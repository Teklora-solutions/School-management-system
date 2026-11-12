"""
apps/accounts/views.py
-----------------------
Thin views — validation + call service → render.
Business logic lives in services.py.
"""

from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import PasswordResetView
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from apps.core.mixins import AdminRequiredMixin

from . import selectors, services
from .forms import (
    ForcePasswordChangeForm,
    LoginForm,
    StaffSetPasswordForm,
    StaffUserCreateForm,
    StaffUserFilterForm,
    StaffUserUpdateForm,
)
from .models import User
from .portals import PORTALS

# Roles that are NOT allowed to self-reset their password.
# Only the admin can reset passwords for these roles via the staff management page.
STAFF_ROLES = frozenset(
    {
        User.Role.ADMIN,
        User.Role.DIRECTOR,
        User.Role.TEACHER,
        User.Role.FINANCE,
    }
)


def _safe_next(request, include_post: bool = False) -> str:
    """
    The ``next`` query parameter, but only if it points back at this site.

    Every gated page hands us ``/login/?next=/finance/…``, and the chooser has
    to carry that through to the role page it links to — so the check belongs in
    one place rather than at each redirect. ``include_post`` extends that to a
    form's hidden ``next`` field, which is how the staff edit page returns an
    admin to the filtered list they came from.
    """
    raw = ""
    if include_post:
        raw = request.POST.get("next") or ""
    raw = raw or request.GET.get("next") or ""
    if not raw:
        return ""
    if url_has_allowed_host_and_scheme(
        url=raw,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return raw
    return ""


def _add_service_errors(form, exc: ValidationError) -> None:
    """Put a service-layer ``ValidationError`` onto the matching form fields."""
    if hasattr(exc, "message_dict"):
        for field, errs in exc.message_dict.items():
            for err in errs:
                form.add_error(field if field in form.fields else None, err)
    else:
        form.add_error(None, exc)


class PortalChooserView(View):
    """
    ``/login/`` — the page every ``LoginRequiredMixin`` redirect lands on.

    It holds no form: it sends each audience to its own sign-in screen, which is
    what lets ``LOGIN_URL`` stay a single value while the portals multiply.
    """

    template_name = "auth/portal_chooser.html"

    def get(self, request):
        if request.user.is_authenticated:
            return redirect("accounts:dashboard_redirect")
        return render(
            request,
            self.template_name,
            {"portals": PORTALS.values(), "next": _safe_next(request)},
        )


class RoleLoginView(View):
    """
    ``/login/<role>/`` — one portal's sign-in form, pinned to that role.

    Correct credentials for a *different* portal are refused without opening a
    session, so a teacher cannot accidentally end up browsing from the student
    page. A superuser with no role set is let through any portal, matching the
    bypass in ``RoleRequiredMixin``.
    """

    template_name = "auth/login.html"

    def get_portal(self):
        portal = PORTALS.get(self.kwargs.get("role", ""))
        if portal is None:
            raise Http404("No such portal.")
        return portal

    def _form(self, request, portal, data=None):
        """The shared form, with this portal's credential wording applied."""
        form = LoginForm(request, data=data)
        form.fields["username"].widget.attrs["placeholder"] = portal.placeholder
        return form

    def _ctx(self, request, portal, form, next_url):
        return {
            "form": form,
            "portal": portal,
            "next": next_url,
            "other_portals": [p for p in PORTALS.values() if p.slug != portal.slug],
        }

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("accounts:dashboard_redirect")
        portal = self.get_portal()
        return render(
            request,
            self.template_name,
            self._ctx(request, portal, self._form(request, portal), _safe_next(request)),
        )

    def post(self, request, *args, **kwargs):
        portal = self.get_portal()
        next_url = _safe_next(request)
        form = self._form(request, portal, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            role = getattr(user, "role", "")
            if role != portal.role and not (user.is_superuser and not role):
                form.add_error(
                    None,
                    f"That is a {role or 'staff'} account. "
                    f"Use the {role.title() or 'Administration'} sign-in page instead.",
                )
            else:
                login(request, user)
                # Anyone holding an admin-set or admin-reset password chooses
                # their own the first time they sign in. A forgotten password
                # is still an admin reset; the self-service email stays closed
                # to staff.
                if user.must_change_password:
                    return redirect("accounts:force_password_change")
                return redirect(next_url or user.get_dashboard_url())
        return render(
            request, self.template_name, self._ctx(request, portal, form, next_url)
        )


class LogoutView(LoginRequiredMixin, View):
    def post(self, request):
        logout(request)
        messages.success(request, "You have been logged out.")
        return redirect("accounts:login")


# ---------------------------------------------------------------------------
# Custom password-reset view — blocks staff roles
# ---------------------------------------------------------------------------


class StaffBlockedPasswordResetView(PasswordResetView):
    """
    Override Django's built-in PasswordResetView to reject email addresses
    that belong to staff accounts (admin, director, teacher, finance).

    Staff passwords can only be reset by an admin through the staff
    management page (/accounts/staff/<pk>/reset-password/).

    Students and parents keep the normal self-service flow.
    """

    def form_valid(self, form):
        email = form.cleaned_data["email"].strip().lower()

        # Check whether the email belongs to a staff account.
        # We deliberately use the same vague response Django does when an email
        # is not found — this avoids leaking whether an account exists.
        try:
            user = User.objects.get(email__iexact=email, is_active=True)
            if user.role in STAFF_ROLES:
                # Redirect to the "done" page without sending any email.
                # The user sees the same neutral confirmation message so this
                # cannot be used to enumerate staff accounts.
                return redirect(self.get_success_url())
        except User.DoesNotExist:
            # Non-existent email — let Django handle it (sends nothing, shows done page).
            pass

        # Email belongs to a student or parent — proceed normally.
        return super().form_valid(form)


class DashboardRedirectView(View):
    """
    ``/dashboard/`` — the one address every "Dashboard" link points at.

    Signed-in users are sent to their role's own home; everyone else gets the
    public home page (``/``). It lives off the site root on purpose: the public
    site owns ``/``, so a root-mounted stub would never be reached.
    """

    def get(self, request):
        if request.user.is_authenticated:
            return redirect(request.user.get_dashboard_url())
        return redirect("public:home")


class ForcePasswordChangeView(LoginRequiredMixin, View):
    """
    Shown once when user.must_change_password is True.

    Admin-set and admin-reset passwords are temporary for every role,
    including staff: the first sign-in must replace it. The self-service
    *reset* email stays closed to staff, so a forgotten password is an
    admin reset, not an email link.
    """

    template_name = "auth/force_password_change.html"

    def get(self, request):
        if not request.user.must_change_password:
            return redirect(request.user.get_dashboard_url())
        form = ForcePasswordChangeForm(user=request.user)
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = ForcePasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            user = form.save()
            user.must_change_password = False
            user.save(update_fields=["must_change_password"])
            update_session_auth_hash(request, user)
            messages.success(request, "Password updated successfully. Welcome!")
            return redirect(user.get_dashboard_url())
        return render(request, self.template_name, {"form": form})


class ProfileView(LoginRequiredMixin, TemplateView):
    template_name = "features/accounts/profile.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["user"] = self.request.user
        # Role-specific profile data
        if self.request.user.role == "student" and hasattr(
            self.request.user, "student_profile"
        ):
            ctx["student"] = self.request.user.student_profile
        if self.request.user.role == "parent" and hasattr(
            self.request.user, "parent_profile"
        ):
            ctx["parent_profile"] = self.request.user.parent_profile
        return ctx


class ChildrenView(LoginRequiredMixin, TemplateView):
    """Parent: list linked children with child-switcher functionality."""

    template_name = "features/accounts/children.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if self.request.user.role == "parent" and hasattr(
            self.request.user, "parent_profile"
        ):
            from apps.students.models import Student

            children = list(
                Student.objects.filter(
                    guardian_links__parent=self.request.user.parent_profile
                )
                .select_related("user", "current_class", "current_stream")
                .prefetch_related("guardian_links")
            )
            ctx["children"] = children
            ctx["selected_child_id"] = self.request.session.get("selected_child_id")
        else:
            ctx["children"] = []
        return ctx


class SelectChildView(LoginRequiredMixin, View):
    """POST: store selected child in session, redirect back."""

    def post(self, request, *args, **kwargs):
        if request.user.role != "parent" or not hasattr(request.user, "parent_profile"):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied("Only parents can switch children.")
        student_id = request.POST.get("student_id")
        if student_id:
            child_ids = list(
                request.user.parent_profile.children_links.values_list(
                    "student_id", flat=True
                )
            )
            if int(student_id) in child_ids:
                request.session["selected_child_id"] = int(student_id)
        next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/"
        return redirect(next_url)


# ─── Staff user management (Admin) ────────────────────────────────────────────


class StaffUserListView(AdminRequiredMixin, ListView):
    template_name = "features/accounts/staff_list.html"
    context_object_name = "staff_users"
    paginate_by = 25

    def get_queryset(self):
        self.filter_form = StaffUserFilterForm(self.request.GET or None)
        search = ""
        role = ""
        is_active = None
        if self.filter_form.is_valid():
            search = self.filter_form.cleaned_data.get("search") or ""
            role = self.filter_form.cleaned_data.get("role") or ""
            active = self.filter_form.cleaned_data.get("is_active")
            if active == "1":
                is_active = True
            elif active == "0":
                is_active = False
        return selectors.staff_user_list(search=search, role=role, is_active=is_active)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter_form"] = getattr(self, "filter_form", StaffUserFilterForm())
        return ctx


class StaffUserCreateView(AdminRequiredMixin, View):
    template_name = "features/accounts/staff_form.html"
    success_url = reverse_lazy("accounts:staff_list")

    def get(self, request):
        return render(request, self.template_name, {"form": StaffUserCreateForm()})

    def post(self, request):
        form = StaffUserCreateForm(request.POST)
        if form.is_valid():
            try:
                user, initial_password = services.create_staff_user(
                    role=form.cleaned_data["role"],
                    first_name=form.cleaned_data["first_name"],
                    last_name=form.cleaned_data["last_name"],
                    email=form.cleaned_data["email"],
                    phone=form.cleaned_data.get("phone") or "",
                    staff_id=form.cleaned_data.get("staff_id") or "",
                    password=form.cleaned_data["password"],
                    must_change_password=form.cleaned_data["must_change_password"],
                    actor=request.user,
                    request=request,
                )
            except ValidationError as exc:
                _add_service_errors(form, exc)
            else:
                messages.success(
                    request,
                    f"Staff account created for {user.full_name}. "
                    f"Login email: {user.email} | "
                    f"Initial password: {initial_password}"
                    + (
                        " (they must change it on first login)."
                        if user.must_change_password
                        else "."
                    ),
                )
                return redirect("accounts:staff_detail", pk=user.pk)
        return render(request, self.template_name, {"form": form})


class StaffUserDetailView(AdminRequiredMixin, DetailView):
    template_name = "features/accounts/staff_detail.html"
    context_object_name = "staff_user"

    def get_object(self, queryset=None):
        return get_object_or_404(
            selectors.staff_user_list(),
            pk=self.kwargs["pk"],
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # staff_id lives on the role's profile row, so the template cannot read
        # it off the user without knowing which row to look at.
        ctx["staff_id"] = getattr(services.staff_profile(ctx["staff_user"]), "staff_id", "")
        return ctx


class StaffUserUpdateView(AdminRequiredMixin, View):
    """
    ``/staff/<pk>/edit/`` — correct a staff member's name, email, phone or staff
    ID. Role and password are not on this form: see ``services.update_staff_user``.
    """

    template_name = "features/accounts/staff_edit.html"

    def _staff_user(self):
        return get_object_or_404(selectors.staff_user_list(), pk=self.kwargs["pk"])

    def _initial(self, user) -> dict:
        profile = services.staff_profile(user)
        return {
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "phone": user.phone,
            "staff_id": getattr(profile, "staff_id", ""),
        }

    def _render(self, request, user, form):
        return render(
            request,
            self.template_name,
            {
                "staff_user": user,
                "form": form,
                "back_url": _safe_next(request) or reverse_lazy("accounts:staff_list"),
            },
        )

    def get(self, request, pk):
        user = self._staff_user()
        return self._render(request, user, StaffUserUpdateForm(initial=self._initial(user)))

    def post(self, request, pk):
        user = self._staff_user()
        form = StaffUserUpdateForm(request.POST)
        if form.is_valid():
            previous_email = user.email
            try:
                services.update_staff_user(
                    user=user,
                    first_name=form.cleaned_data["first_name"],
                    last_name=form.cleaned_data["last_name"],
                    email=form.cleaned_data["email"],
                    phone=form.cleaned_data.get("phone") or "",
                    staff_id=form.cleaned_data.get("staff_id") or "",
                    actor=request.user,
                    request=request,
                )
            except ValidationError as exc:
                _add_service_errors(form, exc)
            else:
                messages.success(request, f"Staff account updated for {user.full_name}.")
                if user.email != previous_email:
                    # The email is the credential, so this is the one edit that
                    # can lock someone out of an account they did not know had
                    # changed.
                    messages.warning(
                        request,
                        f"{user.full_name} now signs in with {user.email}. "
                        f"{previous_email} no longer works.",
                    )
                next_url = _safe_next(request, include_post=True)
                if next_url:
                    return redirect(next_url)
                return redirect("accounts:staff_detail", pk=user.pk)
        return self._render(request, user, form)


class StaffUserDeactivateView(AdminRequiredMixin, View):
    def post(self, request, pk):
        user = get_object_or_404(selectors.staff_user_list(), pk=pk)
        try:
            services.deactivate_user(user=user, actor=request.user, request=request)
            messages.success(request, f"{user.full_name} has been deactivated.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect("accounts:staff_detail", pk=pk)


class StaffUserReactivateView(AdminRequiredMixin, View):
    def post(self, request, pk):
        user = get_object_or_404(selectors.staff_user_list(), pk=pk)
        services.reactivate_user(user=user, actor=request.user, request=request)
        messages.success(request, f"{user.full_name} has been reactivated.")
        return redirect("accounts:staff_detail", pk=pk)


class StaffUserDeleteView(AdminRequiredMixin, View):
    """Show what a staff delete would cost, then perform it or refuse."""

    def _render(self, request, user, forecast):
        return render(
            request,
            "features/accounts/staff_delete_confirm.html",
            {
                "staff_user": user,
                "forecast": forecast,
                # Same rule the service raises with, shown before the button.
                "refusals": services.staff_delete_refusals(user, request.user),
            },
        )

    def get(self, request, pk):
        user = get_object_or_404(selectors.staff_user_list(), pk=pk)
        return self._render(request, user, services.staff_delete_forecast(user))

    def post(self, request, pk):
        user = get_object_or_404(selectors.staff_user_list(), pk=pk)
        try:
            label = services.delete_staff_user(
                user=user, actor=request.user, request=request
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            # Nothing was removed, so the same account is still on screen.
            return self._render(request, user, services.staff_delete_forecast(user))
        messages.success(request, f"{label} and their login were deleted.")
        return redirect("accounts:staff_list")


class StaffResetPasswordView(AdminRequiredMixin, View):
    """
    ``/staff/<pk>/reset-password/`` — the admin types the new password.

    Staff cannot self-reset by email (``StaffBlockedPasswordResetView``), so
    this form is the only way back into a staff account whose password is lost.
    """

    template_name = "features/accounts/staff_reset_password.html"

    def _staff_user(self):
        return get_object_or_404(selectors.staff_user_list(), pk=self.kwargs["pk"])

    def _render(self, request, user, form):
        return render(
            request,
            self.template_name,
            {
                "staff_user": user,
                "form": form,
                "back_url": _safe_next(request, include_post=True)
                or reverse_lazy("accounts:staff_detail", kwargs={"pk": user.pk}),
            },
        )

    def get(self, request, pk):
        return self._render(request, self._staff_user(), StaffSetPasswordForm())

    def post(self, request, pk):
        user = self._staff_user()
        form = StaffSetPasswordForm(request.POST)
        if form.is_valid():
            services.reset_user_password(
                user=user,
                password=form.cleaned_data["password"],
                must_change_password=form.cleaned_data["must_change_password"],
                actor=request.user,
                request=request,
            )
            messages.success(request, f"Password updated for {user.full_name}.")
            next_url = _safe_next(request, include_post=True)
            if next_url:
                return redirect(next_url)
            return redirect("accounts:staff_detail", pk=pk)
        return self._render(request, user, form)
