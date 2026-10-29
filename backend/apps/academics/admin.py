from django.contrib import admin

from .models import AcademicYear, ClassSubject, SchoolClass, Stream, Subject, Term


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ("name", "start_date", "end_date", "is_current", "created_at")
    list_filter = ("is_current",)
    search_fields = ("name",)
    ordering = ("-start_date",)


@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = ("name", "academic_year", "start_date", "end_date", "is_current")
    list_filter = ("is_current", "academic_year", "name")
    search_fields = ("academic_year__name",)
    autocomplete_fields = ("academic_year",)


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("code", "name")


@admin.register(SchoolClass)
class SchoolClassAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "academic_year",
        "level_order",
        "class_teacher",
        "created_at",
    )
    list_filter = ("academic_year",)
    search_fields = ("name", "academic_year__name")
    autocomplete_fields = ("academic_year", "class_teacher")


@admin.register(Stream)
class StreamAdmin(admin.ModelAdmin):
    list_display = ("name", "school_class", "created_at")
    list_filter = ("school_class__academic_year",)
    search_fields = ("name", "school_class__name")
    autocomplete_fields = ("school_class",)


@admin.register(ClassSubject)
class ClassSubjectAdmin(admin.ModelAdmin):
    list_display = ("school_class", "subject", "stream", "created_at")
    list_filter = ("school_class__academic_year", "subject")
    search_fields = ("school_class__name", "subject__code", "subject__name")
    autocomplete_fields = ("school_class", "subject", "stream")
