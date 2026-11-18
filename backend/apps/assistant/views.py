"""
apps/assistant/views.py
-----------------------
Admin configuration screens, director-visible alert screens and the admin test
console. There is still no user-facing chat screen; the assistant is off by
default and the public/signed-in switches do nothing until those screens exist.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.core.audit_log import log_action
from apps.core.mixins import AdminRequiredMixin, RoleRequiredMixin

from . import alerts, consent, crypto, gateway, selectors, services
from .forms import (
    AlertCloseForm,
    AssistantSettingsForm,
    GuardrailRuleForm,
    ProviderForm,
    TestConsoleForm,
)
from .guardrails.rules import MANDATORY_RULES
from .models import AssistantJobFailure, CustomGuardrailRule, ProviderConfig, SafeguardingAlert
from .providers.base import ProviderError

log = logging.getLogger(__name__)


class AssistantSettingsView(AdminRequiredMixin, UpdateView):
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
        alerts.invalidate_page_state()
        messages.success(self.request, "Assistant settings saved.")
        return HttpResponseRedirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["crypto_configured"] = crypto.is_configured()
        ctx["provider_count"] = ProviderConfig.objects.count()
        ctx["consent_draft_en"] = consent.DRAFT_CONSENT_EN
        ctx["consent_draft_sw"] = consent.DRAFT_CONSENT_SW
        ctx["consent_status"] = consent.DRAFT_STATUS
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


class ProviderDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        provider = get_object_or_404(ProviderConfig, pk=pk)
        try:
            services.delete_provider(provider=provider, actor=request.user, request=request)
        except services.ProviderInUse as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Provider deleted.")
        return redirect("assistant:providers")


class ProviderTestView(AdminRequiredMixin, View):
    def post(self, request, pk):
        provider = get_object_or_404(ProviderConfig, pk=pk)
        if not provider.is_active:
            messages.error(request, f"“{provider.name}” is switched off. Turn it on before testing it.")
            return redirect("assistant:providers")
        try:
            reply = services.test_provider(provider=provider, actor=request.user, request=request)
        except (gateway.AllProvidersUnavailable, ProviderError) as exc:
            safe = (
                getattr(exc, "detail", "")
                or getattr(exc, "safe_message", None)
                or str(exc)
            )
            log.warning("Assistant provider test failed for %s: %s", provider.pk, safe)
            messages.error(request, safe)
            log_action(
                actor=request.user,
                action="assistant.provider_test_failed",
                content_type="assistant.ProviderConfig",
                object_id=provider.pk,
                change_summary={"ok": False, "provider": provider.name, "reason": safe},
                request=request,
            )
        except Exception:
            log.exception("Assistant provider test failed unexpectedly for %s", provider.pk)
            messages.error(
                request,
                "The test failed unexpectedly. Nothing about the key is written to the "
                "log; ask your developer to check the server log.",
            )
        else:
            messages.success(
                request,
                f"Gateway replied: “{reply.text}” (model {reply.model}, "
                f"{reply.tokens_in} in / {reply.tokens_out} out tokens).",
            )
        return redirect("assistant:providers")


class GuardrailListView(AdminRequiredMixin, ListView):
    template_name = "features/assistant/guardrails.html"
    context_object_name = "custom_rules"

    def get_queryset(self):
        return selectors.custom_rules_all()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["mandatory_rules"] = MANDATORY_RULES
        ctx["mandatory_count"] = len(MANDATORY_RULES)
        return ctx


class GuardrailCreateView(AdminRequiredMixin, CreateView):
    template_name = "features/assistant/guardrail_form.html"
    form_class = GuardrailRuleForm
    success_url = reverse_lazy("assistant:guardrails")

    def form_valid(self, form):
        self.object = form.save()
        from apps.core.audit_log import log_action

        log_action(
            actor=self.request.user,
            action="assistant.guardrail_rule_added",
            content_type="assistant.CustomGuardrailRule",
            object_id=self.object.pk,
            change_summary={"name": self.object.name, "category": self.object.category},
            request=self.request,
        )
        messages.success(self.request, "Rule added. Mandatory rules are unchanged.")
        return HttpResponseRedirect(self.get_success_url())


class GuardrailToggleView(AdminRequiredMixin, View):
    """Only admin-added rules can be toggled. Mandatory rules have no rows."""

    def post(self, request, pk):
        rule = get_object_or_404(CustomGuardrailRule, pk=pk)
        rule.is_active = not rule.is_active
        rule.save(update_fields=["is_active", "updated_at"])
        from apps.core.audit_log import log_action

        log_action(
            actor=request.user,
            action="assistant.guardrail_rule_toggled",
            content_type="assistant.CustomGuardrailRule",
            object_id=rule.pk,
            change_summary={"name": rule.name, "is_active": rule.is_active},
            request=request,
        )
        messages.success(request, f"Rule “{rule.name}” {'switched on' if rule.is_active else 'switched off'}.")
        return redirect("assistant:guardrails")


class AlertListView(RoleRequiredMixin, ListView):
    allowed_roles = ["admin", "director"]
    template_name = "features/assistant/alerts.html"
    context_object_name = "alerts"

    def get_queryset(self):
        return selectors.all_alerts()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["open_count"] = selectors.open_alerts().count()
        return ctx


class AlertAcknowledgeView(RoleRequiredMixin, View):
    allowed_roles = ["admin", "director"]

    def post(self, request, pk):
        alert = get_object_or_404(SafeguardingAlert, pk=pk)
        alerts.acknowledge(alert, actor=request.user, request=request)
        messages.success(request, "Alert acknowledged.")
        return redirect("assistant:alerts")


class AlertCloseView(RoleRequiredMixin, View):
    allowed_roles = ["admin", "director"]
    template_name = "features/assistant/alert_close.html"

    def get(self, request, pk):
        from django.shortcuts import render

        alert = get_object_or_404(SafeguardingAlert, pk=pk)
        return render(
            request,
            self.template_name,
            {
                "alert": alert,
                "form": AlertCloseForm(initial={"outcome": alert.outcome}),
                "retention_months": selectors.assistant_settings().alert_retention_months,
            },
        )

    def post(self, request, pk):
        from django.shortcuts import render

        alert = get_object_or_404(SafeguardingAlert, pk=pk)
        form = AlertCloseForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "alert": alert,
                    "form": form,
                    "retention_months": selectors.assistant_settings().alert_retention_months,
                },
            )
        alerts.close_alert(
            alert, outcome=form.cleaned_data["outcome"], actor=request.user, request=request
        )
        messages.success(request, "Alert closed and the outcome recorded.")
        return redirect("assistant:alerts")


class TestConsoleView(AdminRequiredMixin, View):
    template_name = "features/assistant/console.html"

    def get(self, request):
        return self._render(request, TestConsoleForm())

    def post(self, request):
        form = TestConsoleForm(request.POST)
        result = None
        if form.is_valid():
            result = services.run_console(
                question=form.cleaned_data["question"],
                provider_id=getattr(form.cleaned_data.get("provider"), "pk", None),
                actor=request.user,
            )
        return self._render(request, form, result)

    def _render(self, request, form, result=None):
        from django.shortcuts import render

        settings_row = selectors.assistant_settings()
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "result": result,
                "settings_row": settings_row,
                "safeguarding_contact_set": settings_row.has_safeguarding_contact,
                "console_daily_limit": settings_row.console_daily_limit,
                "console_monthly_budget_ksh": settings_row.console_monthly_budget_ksh,
            },
        )


class JobFailureListView(AdminRequiredMixin, ListView):
    template_name = "features/assistant/jobs.html"
    context_object_name = "failures"

    def get_queryset(self):
        return selectors.recent_failures()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["resolved"] = AssistantJobFailure.objects.filter(
            resolved_at__isnull=False
        )[:20]
        return ctx


class JobFailureResolveView(AdminRequiredMixin, View):
    def post(self, request, pk):
        failure = get_object_or_404(AssistantJobFailure, pk=pk)
        from django.utils import timezone

        failure.resolved_at = timezone.now()
        failure.save(update_fields=["resolved_at", "updated_at"])
        messages.success(request, "Failure marked as resolved.")
        return redirect("assistant:jobs")
