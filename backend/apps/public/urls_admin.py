"""
apps/public/urls_admin.py
-------------------------
The Website section of the Admin Portal, mounted at ``/website/``.

Covers gallery, enquiries, and all dynamic website content sections:
hero slides, feature cards, testimonials, team members, timeline events,
events, FAQs, admission steps, and key dates.
"""

from django.urls import path

from . import views_admin

app_name = "public_admin"

urlpatterns = [
    # ── Overview ──────────────────────────────────────────────────────────
    path("", views_admin.WebsiteOverviewView.as_view(), name="overview"),

    # ── Gallery ───────────────────────────────────────────────────────────
    path("gallery/", views_admin.GalleryListView.as_view(), name="gallery_list"),
    path("gallery/add/", views_admin.GalleryPhotoCreateView.as_view(), name="gallery_add"),
    path("gallery/<int:pk>/edit/", views_admin.GalleryPhotoEditView.as_view(), name="gallery_edit"),
    path("gallery/<int:pk>/publish/", views_admin.GalleryPhotoPublishView.as_view(), name="gallery_publish"),
    path("gallery/<int:pk>/delete/", views_admin.GalleryPhotoDeleteView.as_view(), name="gallery_delete"),

    # ── Enquiries ─────────────────────────────────────────────────────────
    path("enquiries/", views_admin.EnquiryListView.as_view(), name="enquiry_list"),
    path("enquiries/<int:pk>/", views_admin.EnquiryDetailView.as_view(), name="enquiry_detail"),
    path("enquiries/<int:pk>/handled/", views_admin.EnquiryHandledView.as_view(), name="enquiry_handled"),
    path("enquiries/<int:pk>/delete/", views_admin.EnquiryDeleteView.as_view(), name="enquiry_delete"),

    # ── Hero Slides ───────────────────────────────────────────────────────
    path("hero/", views_admin.HeroSlideListView.as_view(), name="hero_list"),
    path("hero/add/", views_admin.HeroSlideCreateView.as_view(), name="hero_add"),
    path("hero/<int:pk>/edit/", views_admin.HeroSlideEditView.as_view(), name="hero_edit"),
    path("hero/<int:pk>/toggle/", views_admin.HeroSlideToggleView.as_view(), name="hero_toggle"),
    path("hero/<int:pk>/delete/", views_admin.HeroSlideDeleteView.as_view(), name="hero_delete"),

    # ── Feature Cards ─────────────────────────────────────────────────────
    path("features/", views_admin.FeatureCardListView.as_view(), name="feature_list"),
    path("features/add/", views_admin.FeatureCardCreateView.as_view(), name="feature_add"),
    path("features/<int:pk>/edit/", views_admin.FeatureCardEditView.as_view(), name="feature_edit"),
    path("features/<int:pk>/toggle/", views_admin.FeatureCardToggleView.as_view(), name="feature_toggle"),
    path("features/<int:pk>/delete/", views_admin.FeatureCardDeleteView.as_view(), name="feature_delete"),

    # ── Testimonials ──────────────────────────────────────────────────────
    path("testimonials/", views_admin.TestimonialListView.as_view(), name="testimonial_list"),
    path("testimonials/add/", views_admin.TestimonialCreateView.as_view(), name="testimonial_add"),
    path("testimonials/<int:pk>/edit/", views_admin.TestimonialEditView.as_view(), name="testimonial_edit"),
    path("testimonials/<int:pk>/toggle/", views_admin.TestimonialToggleView.as_view(), name="testimonial_toggle"),
    path("testimonials/<int:pk>/delete/", views_admin.TestimonialDeleteView.as_view(), name="testimonial_delete"),

    # ── Team Members ──────────────────────────────────────────────────────
    path("team/", views_admin.TeamMemberListView.as_view(), name="team_list"),
    path("team/add/", views_admin.TeamMemberCreateView.as_view(), name="team_add"),
    path("team/<int:pk>/edit/", views_admin.TeamMemberEditView.as_view(), name="team_edit"),
    path("team/<int:pk>/toggle/", views_admin.TeamMemberToggleView.as_view(), name="team_toggle"),
    path("team/<int:pk>/delete/", views_admin.TeamMemberDeleteView.as_view(), name="team_delete"),

    # ── Timeline Events ───────────────────────────────────────────────────
    path("timeline/", views_admin.TimelineEventListView.as_view(), name="timeline_list"),
    path("timeline/add/", views_admin.TimelineEventCreateView.as_view(), name="timeline_add"),
    path("timeline/<int:pk>/edit/", views_admin.TimelineEventEditView.as_view(), name="timeline_edit"),
    path("timeline/<int:pk>/toggle/", views_admin.TimelineEventToggleView.as_view(), name="timeline_toggle"),
    path("timeline/<int:pk>/delete/", views_admin.TimelineEventDeleteView.as_view(), name="timeline_delete"),

    # ── Upcoming Events ───────────────────────────────────────────────────
    path("events/", views_admin.EventItemListView.as_view(), name="event_list"),
    path("events/add/", views_admin.EventItemCreateView.as_view(), name="event_add"),
    path("events/<int:pk>/edit/", views_admin.EventItemEditView.as_view(), name="event_edit"),
    path("events/<int:pk>/toggle/", views_admin.EventItemToggleView.as_view(), name="event_toggle"),
    path("events/<int:pk>/delete/", views_admin.EventItemDeleteView.as_view(), name="event_delete"),

    # ── FAQs ──────────────────────────────────────────────────────────────
    path("faqs/", views_admin.FAQListView.as_view(), name="faq_list"),
    path("faqs/add/", views_admin.FAQCreateView.as_view(), name="faq_add"),
    path("faqs/<int:pk>/edit/", views_admin.FAQEditView.as_view(), name="faq_edit"),
    path("faqs/<int:pk>/toggle/", views_admin.FAQToggleView.as_view(), name="faq_toggle"),
    path("faqs/<int:pk>/delete/", views_admin.FAQDeleteView.as_view(), name="faq_delete"),

    # ── Admission Steps ───────────────────────────────────────────────────
    path("admissions/", views_admin.AdmissionStepListView.as_view(), name="admission_list"),
    path("admissions/add/", views_admin.AdmissionStepCreateView.as_view(), name="admission_add"),
    path("admissions/<int:pk>/edit/", views_admin.AdmissionStepEditView.as_view(), name="admission_edit"),
    path("admissions/<int:pk>/toggle/", views_admin.AdmissionStepToggleView.as_view(), name="admission_toggle"),
    path("admissions/<int:pk>/delete/", views_admin.AdmissionStepDeleteView.as_view(), name="admission_delete"),

    # ── Key Dates ─────────────────────────────────────────────────────────
    path("keydates/", views_admin.KeyDateListView.as_view(), name="keydate_list"),
    path("keydates/add/", views_admin.KeyDateCreateView.as_view(), name="keydate_add"),
    path("keydates/<int:pk>/edit/", views_admin.KeyDateEditView.as_view(), name="keydate_edit"),
    path("keydates/<int:pk>/toggle/", views_admin.KeyDateToggleView.as_view(), name="keydate_toggle"),
    path("keydates/<int:pk>/delete/", views_admin.KeyDateDeleteView.as_view(), name="keydate_delete"),
]
