"""
apps/public/models.py
---------------------
Tables for the school's public website content.

The dynamic content models (HeroSlide, FeatureCard, Testimonial, etc.) let the
admin edit every section of the public website from the admin portal rather than
touching templates. Each model follows the same pattern: ordered, blankable where
sensible, and with an ``is_active`` flag so items can be drafted without appearing
on the live site.
"""

from __future__ import annotations

from django.db import models

from apps.core.models import BaseModel


class GalleryPhoto(BaseModel):
    """One photograph on the public gallery, published or held back."""

    title = models.CharField(max_length=200)
    caption = models.CharField(
        max_length=300,
        blank=True,
        help_text="One line shown under the photo. Leave blank for no caption.",
    )
    image = models.ImageField(upload_to="gallery/")
    display_order = models.PositiveSmallIntegerField(
        default=0,
        db_index=True,
        help_text="Lowest first. Photos sharing an order fall back to newest first.",
    )
    is_published = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "public_gallery_photo"
        ordering = ["display_order", "-created_at"]
        verbose_name = "gallery photo"
        verbose_name_plural = "gallery photos"

    def __str__(self) -> str:
        return self.title


class ContactMessage(BaseModel):
    """
    An enquiry left on the public Contact page.

    ``handled`` is the office's own mark, not a claim about the sender: nothing
    here records *what* was done or who did it, and the row is still the whole
    record of the enquiry. The flag exists so the enquiry list can be filtered
    down to the ones nobody has answered yet; who set it is in the audit log.
    """

    name = models.CharField(max_length=200)
    email = models.EmailField(max_length=254)
    phone = models.CharField(max_length=32, blank=True)
    message = models.TextField(max_length=2000)
    handled = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Tick once the office has replied. Unticked rows are the open ones.",
    )
    handled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set automatically when the mark is ticked, cleared when it is unticked.",
    )

    class Meta:
        db_table = "public_contact_message"
        ordering = ["-created_at"]
        verbose_name = "website enquiry"
        verbose_name_plural = "website enquiries"

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"


# ─────────────────────────────────────────────────────────────────────────────
# Home page — hero carousel slides
# ─────────────────────────────────────────────────────────────────────────────

class HeroSlide(BaseModel):
    """One slide in the home-page hero carousel."""

    title = models.CharField(max_length=200, help_text="Slide heading shown in the caption.")
    caption = models.CharField(max_length=300, blank=True, help_text="One-line sub-caption below the heading.")
    image = models.ImageField(
        upload_to="website/hero/",
        help_text="Recommended size: 900 × 560 px.",
    )
    alt_text = models.CharField(max_length=200, blank=True, help_text="Accessibility description of the image.")
    display_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "public_hero_slide"
        ordering = ["display_order", "id"]
        verbose_name = "hero slide"
        verbose_name_plural = "hero slides"

    def __str__(self) -> str:
        return self.title


# ─────────────────────────────────────────────────────────────────────────────
# Home page — "Why Choose Us" feature cards
# ─────────────────────────────────────────────────────────────────────────────

class FeatureCard(BaseModel):
    """One card in the home page "Why Choose Us" grid."""

    COLOUR_CHOICES = [
        ("gold", "Gold"),
        ("teal", "Teal"),
        ("indigo", "Indigo"),
        ("rose", "Rose"),
    ]

    icon = models.CharField(max_length=10, blank=True, help_text="Emoji icon, e.g. 📚")
    colour = models.CharField(max_length=20, choices=COLOUR_CHOICES, default="gold")
    number_label = models.CharField(
        max_length=4, blank=True, help_text='Display number, e.g. "01".',
    )
    title = models.CharField(max_length=200)
    text = models.TextField(help_text="Card body text.")
    tag = models.CharField(max_length=120, blank=True, help_text="Small checkmark tag line, e.g. ✓ Avg. 18 students per class.")
    display_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "public_feature_card"
        ordering = ["display_order", "id"]
        verbose_name = "feature card"
        verbose_name_plural = "feature cards"

    def __str__(self) -> str:
        return self.title


# ─────────────────────────────────────────────────────────────────────────────
# Home page — parent testimonials
# ─────────────────────────────────────────────────────────────────────────────

class Testimonial(BaseModel):
    """One parent/student testimonial quote."""

    quote = models.TextField(help_text="The testimonial text (use straight quotes, not curly).")
    author_name = models.CharField(max_length=200)
    author_role = models.CharField(max_length=200, blank=True, help_text='e.g. "Parent, Grade 4".')
    avatar = models.ImageField(
        upload_to="website/testimonials/",
        null=True,
        blank=True,
        help_text="Optional headshot. Recommended: 100 × 100 px, square.",
    )
    display_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "public_testimonial"
        ordering = ["display_order", "id"]
        verbose_name = "testimonial"
        verbose_name_plural = "testimonials"

    def __str__(self) -> str:
        return f"{self.author_name}: {self.quote[:60]}"


# ─────────────────────────────────────────────────────────────────────────────
# About page — leadership / staff cards
# ─────────────────────────────────────────────────────────────────────────────

class TeamMember(BaseModel):
    """One person in the About page leadership grid."""

    name = models.CharField(max_length=200)
    role = models.CharField(max_length=200, help_text='e.g. "Principal" or "Head of Primary".')
    bio = models.TextField(blank=True, help_text="Short one- or two-sentence biography.")
    photo = models.ImageField(
        upload_to="website/team/",
        null=True,
        blank=True,
        help_text="Recommended: 200 × 200 px, square.",
    )
    display_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "public_team_member"
        ordering = ["display_order", "id"]
        verbose_name = "team member"
        verbose_name_plural = "team members"

    def __str__(self) -> str:
        return f"{self.name} — {self.role}"


# ─────────────────────────────────────────────────────────────────────────────
# About page — history timeline
# ─────────────────────────────────────────────────────────────────────────────

class TimelineEvent(BaseModel):
    """One milestone entry in the About page history timeline."""

    year_label = models.CharField(max_length=20, help_text='Label shown on the dot, e.g. "1985" or "Today".')
    title = models.CharField(max_length=200)
    text = models.TextField()
    display_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "public_timeline_event"
        ordering = ["display_order", "id"]
        verbose_name = "timeline event"
        verbose_name_plural = "timeline events"

    def __str__(self) -> str:
        return f"{self.year_label}: {self.title}"


# ─────────────────────────────────────────────────────────────────────────────
# School Life / Gallery page — upcoming events
# ─────────────────────────────────────────────────────────────────────────────

class EventItem(BaseModel):
    """An upcoming school event shown on the School Life page."""

    day = models.CharField(max_length=4, help_text='Day number, e.g. "15".')
    month = models.CharField(max_length=10, help_text='3-letter month abbreviation, e.g. "Nov".')
    title = models.CharField(max_length=200)
    description = models.CharField(max_length=300, blank=True, help_text="Time and short note, e.g. '9:00 AM — Main Hall'.")
    display_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "public_event_item"
        ordering = ["display_order", "id"]
        verbose_name = "event item"
        verbose_name_plural = "event items"

    def __str__(self) -> str:
        return f"{self.day} {self.month} — {self.title}"


# ─────────────────────────────────────────────────────────────────────────────
# Contact / Admissions page — FAQ accordion
# ─────────────────────────────────────────────────────────────────────────────

class FAQ(BaseModel):
    """One question and answer in the admissions/contact FAQ accordion."""

    question = models.CharField(max_length=300)
    answer = models.TextField()
    display_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "public_faq"
        ordering = ["display_order", "id"]
        verbose_name = "FAQ"
        verbose_name_plural = "FAQs"

    def __str__(self) -> str:
        return self.question


# ─────────────────────────────────────────────────────────────────────────────
# Contact / Admissions page — admissions process steps
# ─────────────────────────────────────────────────────────────────────────────

class AdmissionStep(BaseModel):
    """One numbered step in the admissions process."""

    step_number = models.PositiveSmallIntegerField(help_text="Display number shown on the step badge.")
    title = models.CharField(max_length=200)
    text = models.TextField()
    display_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "public_admission_step"
        ordering = ["display_order", "step_number"]
        verbose_name = "admission step"
        verbose_name_plural = "admission steps"

    def __str__(self) -> str:
        return f"Step {self.step_number}: {self.title}"


# ─────────────────────────────────────────────────────────────────────────────
# Contact / Admissions page — key dates
# ─────────────────────────────────────────────────────────────────────────────

class KeyDate(BaseModel):
    """One key admissions date shown on the Contact/Admissions page."""

    day = models.CharField(max_length=4, help_text='Day number, e.g. "15".')
    month = models.CharField(max_length=10, help_text='3-letter month, e.g. "Jan".')
    title = models.CharField(max_length=200)
    description = models.CharField(max_length=300, blank=True)
    display_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "public_key_date"
        ordering = ["display_order", "id"]
        verbose_name = "key date"
        verbose_name_plural = "key dates"

    def __str__(self) -> str:
        return f"{self.day} {self.month} — {self.title}"
