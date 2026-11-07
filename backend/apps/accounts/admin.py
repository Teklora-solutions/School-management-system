"""
apps/accounts/admin.py
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import AdminProfile, DirectorProfile, FinanceProfile, ParentProfile, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "email", "full_name", "role", "is_active", "created_at")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("username", "email", "first_name", "last_name")
    ordering = ("last_name", "first_name")
    fieldsets = BaseUserAdmin.fieldsets + (
        (
            "School Info",
            {"fields": ("role", "phone", "avatar", "must_change_password")},
        ),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("School Info", {"fields": ("email", "role")}),
    )

    # The admin site is not a way to erase people: history and audit logs name
    # these rows. The portal's guarded delete page is the only sanctioned door.
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AdminProfile)
class AdminProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "staff_id")
    search_fields = ("user__email", "user__username", "staff_id")


@admin.register(DirectorProfile)
class DirectorProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "staff_id")
    search_fields = ("user__email", "staff_id")


@admin.register(FinanceProfile)
class FinanceProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "staff_id")
    search_fields = ("user__email", "staff_id")


@admin.register(ParentProfile)
class ParentProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "occupation")
    search_fields = ("user__email", "user__first_name", "user__last_name")
