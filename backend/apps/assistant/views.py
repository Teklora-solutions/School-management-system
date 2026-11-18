"""
apps/assistant/views.py
-----------------------
Admin-only screens: the assistant settings page, the provider list, the
provider form, and a connection test. There is no user-facing chat in this
build yet; the assistant is off by default and the fake provider contacts
nobody.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.core.mixins import AdminRequiredMixin

from . import crypto, selectors, services
from .forms import AssistantSettingsForm, ProviderForm
from .models import ProviderConfig
from .providers import ProviderUnavailable


class AssistantSettingsView(AdminRequiredMixin, UpdateView):
    """Admin: the switch, the rules, the budget and the default provider."""

    template_name = "features/assistant/settings.html"
    form_class = AssistantSettingsForm
    success_url = reverse_lazy("assistant:settings")

    def get_object(self, queryset=None):
        return selectors.assistant_settings()

    def form_valid(self, form):
        services.update_assistant_settings(
            instance=form.instance,
            cleaned_data=form.cleaned_data,
            actor=self.request.user,
            request=self.request,
        )
        messages.success(self.request, "Assistant settings saved.")
        return HttpResponseRedirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["crypto_configured"] = crypto.is_configured()
        ctx["provider_count"] = ProviderConfig.objects.count()
        return ctx


class ProviderListView(AdminRequiredMixin, ListView):
    template_name = "features/assistant/provider_list.html"
    context_object_name = "providers"

    def get_queryset(self):
        return selectors.provider_list()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        settings_row = selectors.assistant_settings()
        ctx["default_provider_id"] = settings_row.default_provider_id
        ctx["crypto_configured"] = crypto.is_configured()
        return ctx


class ProviderCreateView(AdminRequiredMixin, CreateView):
    template_name = "features/assistant/provider_form.html"
    form_class = ProviderForm

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["crypto_configured"] = crypto.is_configured()
        return ctx

    def form_valid(self, form):
        try:
            provider = services.create_provider(
                cleaned_data=form.cleaned_data,
                actor=self.request.user,
                request=self.request,
            )
        except crypto.EncryptionUnavailable as exc:
            form.add_error("api_key", str(exc))
            return self.form_invalid(form)
        messages.success(self.request, f"Provider “{provider.name}” added.")
        return redirect("assistant:providers")


class ProviderUpdateView(AdminRequiredMixin, UpdateView):
    template_name = "features/assistant/provider_form.html"
    form_class = ProviderForm

    def get_queryset(self):
        return ProviderConfig.objects.all()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["crypto_configured"] = crypto.is_configured()
        return ctx

    def form_valid(self, form):
        try:
            provider = services.update_provider(
                instance=form.instance,
                cleaned_data=form.cleaned_data,
                actor=self.request.user,
                request=self.request,
            )
        except crypto.EncryptionUnavailable as exc:
            form.add_error("api_key", str(exc))
            return self.form_invalid(form)
        messages.success(self.request, f"Provider “{provider.name}” saved.")
        return redirect("assistant:providers")


class ProviderTestView(AdminRequiredMixin, View):
    """POST only: send one canned prompt through the gateway."""

    def post(self, request, pk):
        provider = get_object_or_404(ProviderConfig, pk=pk)
        if not provider.is_active:
            messages.error(
                request,
                f"“{provider.name}” is switched off. Turn it on before testing it.",
            )
            return redirect("assistant:providers")
        try:
            reply = services.test_provider(
                provider=provider, actor=request.user, request=request
            )
        except ProviderUnavailable as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f"Gateway replied: “{reply.text}” (model {reply.model}).",
            )
        return redirect("assistant:providers")
