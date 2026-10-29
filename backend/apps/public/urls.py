"""apps/public/urls.py"""

from django.urls import path

from . import views

app_name = "public"

# path("") maps directly to the public home page so "/" is the default
# homepage — no redirect for anyone. Authenticated users who navigate to "/"
# directly are redirected to their dashboard by DashboardRedirectView in
# accounts, but unauthenticated visitors land here first.
urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),          # "/" → home
    path("home/", views.HomeView.as_view(), name="home_alt"), # backward-compat alias
    path("about/", views.AboutView.as_view(), name="about"),
    path("programme/", views.AcademicsView.as_view(), name="academics"),
    path("gallery/", views.GalleryView.as_view(), name="gallery"),
    path("contact/", views.ContactView.as_view(), name="contact"),
]
