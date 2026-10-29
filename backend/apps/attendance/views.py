"""apps/attendance/views.py  — stub, full implementation in Phase 2+"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView


class ComingSoonView(LoginRequiredMixin, TemplateView):
    template_name = "partials/_coming_soon.html"
