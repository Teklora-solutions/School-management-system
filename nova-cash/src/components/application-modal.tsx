"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  Copy,
  FlaskConical,
  Loader2,
  LockKeyhole,
  ReceiptText,
  RotateCcw,
  ShieldCheck,
  Smartphone,
  X,
} from "lucide-react";
import { ModalShell } from "./modal-shell";
import { LogoMark } from "./brand";
import { cn } from "@/lib/cn";
import {
  APPLICATION_TERMS,
  addMonthsAsDays,
  detectNetwork,
  feeForAmount,
  formatUgx,
  loanQuote,
  termLabel,
  validateApplicant,
  validateDisbursementPhone,
  type PaymentMode,
  type SavedLoan,
  type TermMonths,
} from "@/lib/loans";

type Stage =
  | "details"
  | "checking"
  | "qualified"
  | "disbursement_phone"
  | "send_stk"
  | "verifying"
  | "success"
  | "failed";

interface PaymentSession {
  transactionId: string;
  reference: string;
  feeUgx: number;
  feeKes: number;
  mode: PaymentMode;
  phone: string;
}

interface PushResponse {
  success?: boolean;
  mode?: PaymentMode;
  reference?: string;
  transactionId?: string;
  feeUgx?: number;
  feeKes?: number;
  phone?: string;
  error?: string;
}

interface StatusResponse {
  success?: boolean;
  status?: "pending" | "completed" | "failed";
  receipt?: string | null;
  resultDesc?: string | null;
  error?: string;
}

type FieldErrors = {
  fullName?: string;
  phone?: string;
  nin?: string;
  disbursement?: string;
};

const STEPS = ["Details", "Offer", "Payout", "Payment", "Receipt"];

const STEP_INDEX: Record<Stage, number> = {
  details: 0,
  checking: 0,
  qualified: 1,
  disbursement_phone: 2,
  send_stk: 3,
  verifying: 3,
  success: 4,
  failed: 3,
};

const CHECK_MESSAGES = [
  "Confirming your contact details",
  "Calculating repayment options",
  "Preparing your offer",
];

const inputClass =
  "h-11 w-full rounded-xl border border-line bg-mist/60 px-3.5 text-sm font-medium text-ink placeholder:font-normal placeholder:text-muted/70 transition focus:border-brand focus:bg-white focus:outline-none focus:ring-2 focus:ring-brand/15";

function Field({
  id,
  label,
  hint,
  error,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div>
      <label
        htmlFor={id}
        className="mb-1.5 flex items-baseline justify-between gap-2 text-[11px] font-bold uppercase tracking-[0.08em] text-ink-soft"
      >
        <span>{label}</span>
        {hint ? (
          <span className="text-[10px] font-medium normal-case tracking-normal text-muted">
            {hint}
          </span>
        ) : null}
      </label>
      {children}
      {error ? (
        <p className="mt-1.5 flex items-center gap-1 text-[11px] font-medium text-danger">
          <AlertCircle className="h-3 w-3 shrink-0" aria-hidden="true" />
          {error}
        </p>
      ) : null}
    </div>
  );
}

function DetailRow({
  label,
  value,
  emphasis = false,
}: {
  label: string;
  value: ReactNode;
  emphasis?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-1.5">
      <span className="text-[11px] text-muted sm:text-xs">{label}</span>
      <span
        className={cn(
          "text-right text-[11px] font-semibold text-ink sm:text-xs",
          emphasis && "font-extrabold text-brand-deep",
        )}
      >
        {value}
      </span>
    </div>
  );
}

function ProgressSteps({ stage }: { stage: Stage }) {
  const active = STEP_INDEX[stage];

  return (
    <div className="grid grid-cols-5 gap-1 px-5 pb-2 pt-3 sm:px-7">
      {STEPS.map((label, index) => {
        const done = index < active || stage === "success";
        const current = index === active && stage !== "success";
        return (
          <div key={label} className="flex flex-col items-center gap-1">
            <span
              className={cn(
                "flex h-6 w-6 items-center justify-center rounded-full border text-[10px] font-bold transition",
                done && "border-brand bg-brand text-white",
                current && "border-brand bg-brand-soft text-brand-deep",
                !done && !current && "border-line bg-white text-muted",
              )}
              aria-hidden="true"
            >
              {done ? <Check className="h-3 w-3" /> : index + 1}
            </span>
            <span
              className={cn(
                "text-[9px] font-semibold sm:text-[10px]",
                current ? "text-brand-deep" : "text-muted",
              )}
            >
              {label}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function ApplicationModal({
  open,
  amount,
  packageId,
  paymentMode,
  onClose,
  onApproved,
}: {
  open: boolean;
  amount: number;
  packageId?: string;
  paymentMode: PaymentMode;
  onClose: () => void;
  onApproved: (loan: SavedLoan) => void;
}) {
  const [stage, setStage] = useState<Stage>("details");
  const [fullName, setFullName] = useState("");
  const [phone, setPhone] = useState("");
  const [nin, setNin] = useState("");
  const [disbursementPhone, setDisbursementPhone] = useState("");
  const [months, setMonths] = useState<TermMonths>(6);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [checkStep, setCheckStep] = useState(0);
  const [payment, setPayment] = useState<PaymentSession | null>(null);
  const [sending, setSending] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [failureReason, setFailureReason] = useState("");
  const [verifySeconds, setVerifySeconds] = useState(45);
  const [receipt, setReceipt] = useState<SavedLoan | null>(null);
  const [copied, setCopied] = useState(false);
  const finalizedRef = useRef(false);

  const principal = amount;
  const quote = loanQuote(principal, months);
  const fee = feeForAmount(principal);
  const firstName = fullName.trim().split(/\s+/)[0] || "there";
  const network = detectNetwork(disbursementPhone);

  useEffect(() => {
    if (stage !== "checking") return;
    const stepOne = window.setTimeout(() => setCheckStep(1), 1400);
    const stepTwo = window.setTimeout(() => setCheckStep(2), 3000);
    const complete = window.setTimeout(() => setStage("qualified"), 4500);
    return () => {
      window.clearTimeout(stepOne);
      window.clearTimeout(stepTwo);
      window.clearTimeout(complete);
    };
  }, [stage]);

  useEffect(() => {
    const dialog = document.querySelector<HTMLElement>(
      '[role="dialog"][aria-labelledby="application-title"]',
    );
    if (!dialog) return;
    const heading = dialog.querySelector<HTMLElement>("#application-title");
    const frame = window.requestAnimationFrame(() => {
      dialog.scrollTo({ top: 0 });
      heading?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [stage]);

  const finalizePayment = useCallback(
    (receiptId: string) => {
      if (!payment || finalizedRef.current || !receiptId.trim()) return;
      finalizedRef.current = true;
      const now = new Date().toISOString();
      const loan: SavedLoan = {
        id: payment.transactionId,
        reference: payment.reference,
        fullName: fullName.trim(),
        phone: payment.phone,
        network: detectNetwork(disbursementPhone),
        principal,
        fee: payment.feeUgx,
        months,
        monthly: quote.monthly,
        total: quote.total,
        status: payment.mode === "demo" ? "sandbox" : "queued",
        mode: payment.mode,
        receipt: receiptId,
        createdAt: now,
        dueDate: addMonthsAsDays(now, months),
      };
      setReceipt(loan);
      setStage("success");
      onApproved(loan);
    },
    [payment, fullName, disbursementPhone, principal, months, quote.monthly, quote.total, onApproved],
  );

  useEffect(() => {
    if (stage !== "verifying" || !payment) return;

    let cancelled = false;
    let pollTimer: number | undefined;
    const deadline = Date.now() + 45_000;

    const countdown = window.setInterval(() => {
      if (!cancelled) {
        setVerifySeconds(Math.max(0, Math.ceil((deadline - Date.now()) / 1000)));
      }
    }, 500);

    const poll = async () => {
      try {
        const response = await fetch(
          `/api/payments/status?transactionId=${encodeURIComponent(payment.transactionId)}`,
          { cache: "no-store" },
        );
        const data = (await response.json()) as StatusResponse;
        if (cancelled) return;

        if (data.success && data.status === "completed") {
          finalizePayment(data.receipt ?? payment.transactionId);
          return;
        }
        if (data.success && data.status === "failed") {
          setFailureReason(data.resultDesc ?? "The request was cancelled on the handset.");
          setStage("failed");
          return;
        }
      } catch {
        // Transient network errors are retried until the countdown expires.
      }

      if (cancelled) return;
      if (Date.now() >= deadline) {
        setFailureReason("The mobile money prompt expired before it was approved.");
        setStage("failed");
        return;
      }
      pollTimer = window.setTimeout(poll, 2500);
    };

    pollTimer = window.setTimeout(poll, 1200);

    return () => {
      cancelled = true;
      window.clearInterval(countdown);
      if (pollTimer) window.clearTimeout(pollTimer);
    };
  }, [stage, payment, finalizePayment]);

  function handleDetails(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextErrors = validateApplicant(fullName, phone, nin);
    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors);
      return;
    }
    setErrors({});
    if (!disbursementPhone.trim()) setDisbursementPhone(phone.trim());
    setCheckStep(0);
    setStage("checking");
  }

  function handlePayout(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const error = validateDisbursementPhone(disbursementPhone, paymentMode);
    if (error) {
      setErrors({ disbursement: error });
      return;
    }
    setErrors({});
    setSubmitError("");
    setStage("send_stk");
  }

  async function sendStk() {
    setSending(true);
    setSubmitError("");
    try {
      const response = await fetch("/api/payments/stkpush", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          amount: principal,
          packageId,
          fullName: fullName.trim(),
          phone: disbursementPhone,
          months,
        }),
      });
      const data = (await response.json()) as PushResponse;

      if (
        !response.ok ||
        !data.success ||
        !data.transactionId ||
        !data.reference ||
        typeof data.feeUgx !== "number"
      ) {
        setSubmitError(data.error ?? "The payment could not be started. Please try again.");
        return;
      }

      setPayment({
        transactionId: data.transactionId,
        reference: data.reference,
        feeUgx: data.feeUgx,
        feeKes: data.feeKes ?? 0,
        mode: data.mode ?? paymentMode,
        phone: data.phone ?? disbursementPhone,
      });
      finalizedRef.current = false;
      setVerifySeconds(45);
      setFailureReason("");
      setStage("verifying");
    } catch {
      setSubmitError("Could not reach the server. Check your connection and try again.");
    } finally {
      setSending(false);
    }
  }

  async function copyReference() {
    const value = receipt?.reference ?? payment?.reference;
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  const stepActive = STEP_INDEX[stage];
  const locked = stage === "verifying";

  return (
    <ModalShell
      open={open}
      onClose={onClose}
      labelledBy="application-title"
      locked={locked}
      className="sm:max-w-[34rem]"
    >
      <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-line/80 bg-white/95 px-5 py-3.5 backdrop-blur sm:px-7">
        <div className="flex min-w-0 items-center gap-2.5">
          <LogoMark className="h-9 w-9" />
          <div className="min-w-0">
            <p className="text-[9px] font-bold uppercase tracking-[0.18em] text-muted">
              Loan application
            </p>
            <p className="truncate text-sm font-extrabold text-ink">
              {formatUgx(principal)} · {termLabel(months)}
            </p>
          </div>
        </div>
        {locked ? (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-brand-soft px-2.5 py-1 text-[10px] font-bold text-brand-deep">
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
            In progress
          </span>
        ) : (
          <button
            type="button"
            onClick={onClose}
            aria-label="Close application"
            className="inline-flex h-8 w-8 items-center justify-center rounded-full text-muted transition hover:bg-mist hover:text-ink"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      <ProgressSteps stage={stage} />

      <div className="px-5 pb-6 pt-2 sm:px-7 sm:pb-7">
        {stage === "details" ? (
          <form onSubmit={handleDetails} className="animate-fade-up space-y-4" noValidate>
            <div>
              <h2 id="application-title" tabIndex={-1} className="outline-none text-xl font-extrabold tracking-[-0.02em] text-ink">
                Your details
              </h2>
              <p className="mt-1 text-xs leading-relaxed text-muted">
                Takes about a minute. Nothing is saved unless a payment is confirmed.
              </p>
            </div>

            <Field id="full-name" label="Full name" hint="As on your ID" error={errors.fullName}>
              <input
                id="full-name"
                data-autofocus
                type="text"
                autoComplete="name"
                placeholder="e.g. Amina Nakato"
                value={fullName}
                onChange={(event) => setFullName(event.target.value)}
                className={inputClass}
              />
            </Field>

            <Field id="applicant-phone" label="Mobile money number" error={errors.phone}>
              <input
                id="applicant-phone"
                type="tel"
                inputMode="tel"
                autoComplete="tel"
                placeholder="0772 123 456"
                value={phone}
                onChange={(event) => setPhone(event.target.value)}
                className={inputClass}
              />
            </Field>

            <Field
              id="nin"
              label="National ID number"
              hint="Kept in this session only"
              error={errors.nin}
            >
              <input
                id="nin"
                type="text"
                autoComplete="off"
                placeholder="Enter your NIN"
                value={nin}
                onChange={(event) => setNin(event.target.value)}
                className={inputClass}
              />
            </Field>

            <div className="flex items-start gap-2 rounded-xl border border-line/80 bg-mist/70 px-3 py-2.5 text-[11px] leading-relaxed text-muted">
              <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand" aria-hidden="true" />
              This preview does not contact a credit bureau. Figures are illustrative and
              your full cost is shown before any payment.
            </div>

            <button
              type="submit"
              className="inline-flex h-12 w-full items-center justify-center gap-2 rounded-full bg-ink text-sm font-bold text-white transition hover:bg-ink-soft active:scale-[0.99]"
            >
              Check my eligibility
              <ArrowRight className="h-4 w-4 text-accent" aria-hidden="true" />
            </button>
          </form>
        ) : null}

        {stage === "checking" ? (
          <div className="animate-fade-up py-6 text-center">
            <div className="relative mx-auto flex h-20 w-20 items-center justify-center">
              <div className="absolute inset-0 animate-ping rounded-full bg-brand/15" />
              <div className="absolute inset-2 rounded-full border-2 border-brand/25" />
              <div className="relative z-10 flex h-14 w-14 items-center justify-center rounded-full bg-ink text-accent">
                <Loader2 className="h-6 w-6 animate-spin" aria-hidden="true" />
              </div>
            </div>
            <h2 id="application-title" tabIndex={-1} className="outline-none mt-4 text-xl font-extrabold text-ink">
              Reviewing your details
            </h2>
            <p className="mx-auto mt-1 max-w-xs text-xs leading-relaxed text-muted">
              Preparing an illustrative offer. This preview does not contact a credit bureau.
            </p>

            <div className="mx-auto mt-5 max-w-sm space-y-2.5 rounded-2xl border border-line/80 bg-mist/50 p-4 text-left">
              {CHECK_MESSAGES.map((message, index) => {
                const done = index < checkStep || stage !== "checking";
                const active = index === checkStep;
                return (
                  <div key={message} className="flex items-center gap-2.5 text-[11px] font-medium">
                    <span
                      className={cn(
                        "flex h-5 w-5 items-center justify-center rounded-full border",
                        done
                          ? "border-success bg-success text-white"
                          : active
                            ? "border-brand bg-white text-brand"
                            : "border-line bg-white text-muted",
                      )}
                    >
                      {done ? (
                        <Check className="h-3 w-3" />
                      ) : active ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <span className="h-1.5 w-1.5 rounded-full bg-current" />
                      )}
                    </span>
                    <span className={done ? "text-ink" : "text-muted"}>{message}</span>
                  </div>
                );
              })}
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-line">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-brand to-accent transition-all duration-500"
                  style={{ width: `${((checkStep + 1) / CHECK_MESSAGES.length) * 100}%` }}
                />
              </div>
            </div>
          </div>
        ) : null}

        {stage === "qualified" ? (
          <div className="animate-fade-up space-y-4">
            <div className="text-center">
              <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-success/10 text-success">
                <CheckCircle2 className="h-7 w-7" aria-hidden="true" />
              </span>
              <h2 id="application-title" tabIndex={-1} className="outline-none mt-2 text-xl font-extrabold tracking-[-0.02em] text-ink">
                Congratulations, {firstName}!
              </h2>
              <p className="mt-1 text-xs text-muted">
                You are pre-approved for{" "}
                <strong className="font-bold text-ink">{formatUgx(principal)}</strong>
              </p>
            </div>

            <div className="rounded-2xl border border-line/80 bg-mist/50 p-4">
              <p className="text-[11px] font-bold text-ink">Choose your repayment term</p>
              <div className="mt-2.5 grid grid-cols-4 gap-1.5">
                {APPLICATION_TERMS.map((term) => {
                  const selected = term === months;
                  return (
                    <button
                      key={term}
                      type="button"
                      onClick={() => setMonths(term)}
                      aria-pressed={selected}
                      className={cn(
                        "rounded-xl border px-1 py-2 text-center transition",
                        selected
                          ? "border-ink bg-ink text-white shadow-sm"
                          : "border-line bg-white text-ink-soft hover:border-brand/30",
                      )}
                    >
                      <span className="block text-[11px] font-bold">{term} mos</span>
                      <span
                        className={cn(
                          "mt-0.5 block text-[9px]",
                          selected ? "text-accent" : "text-muted",
                        )}
                      >
                        {Math.round(loanQuote(principal, term).rate * 100)}% flat
                      </span>
                    </button>
                  );
                })}
              </div>

              <div className="mt-3.5 space-y-0.5 border-t border-line pt-3">
                <DetailRow label="Monthly installment" value={`${formatUgx(quote.monthly)} / mo`} emphasis />
                <DetailRow label="Total repayment" value={formatUgx(quote.total)} />
                <DetailRow label="One-time processing fee" value={formatUgx(fee)} />
              </div>
            </div>

            <p className="text-[10px] leading-relaxed text-muted">
              Interest is flat for the selected term. The processing fee is paid separately
              before disbursement and is not part of the total repayment above.
            </p>

            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => setStage("details")}
                className="inline-flex h-11 items-center gap-1.5 rounded-full px-3 text-xs font-semibold text-muted transition hover:bg-mist hover:text-ink"
              >
                <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
                Back
              </button>
              <button
                type="button"
                onClick={() => {
                  if (!disbursementPhone.trim()) setDisbursementPhone(phone.trim());
                  setStage("disbursement_phone");
                }}
                className="inline-flex h-12 flex-1 items-center justify-center gap-2 rounded-full bg-ink text-sm font-bold text-white transition hover:bg-ink-soft active:scale-[0.99]"
              >
                Accept offer & continue
                <ArrowRight className="h-4 w-4 text-accent" aria-hidden="true" />
              </button>
            </div>
          </div>
        ) : null}

        {stage === "disbursement_phone" ? (
          <form onSubmit={handlePayout} className="animate-fade-up space-y-4" noValidate>
            <div>
              <h2 id="application-title" tabIndex={-1} className="outline-none text-xl font-extrabold tracking-[-0.02em] text-ink">
                Where should the funds go?
              </h2>
              <p className="mt-1 text-xs leading-relaxed text-muted">
                Enter the mobile money wallet that will receive the loan. We use the same
                number for the payment prompt.
              </p>
            </div>

            <Field
              id="payout-phone"
              label="Mobile money receiver number"
              error={errors.disbursement}
            >
              <div className="relative">
                <Smartphone
                  className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-brand"
                  aria-hidden="true"
                />
                <input
                  id="payout-phone"
                  data-autofocus
                  type="tel"
                  inputMode="tel"
                  autoComplete="tel"
                  placeholder="0772 123 456"
                  value={disbursementPhone}
                  onChange={(event) => setDisbursementPhone(event.target.value)}
                  className={cn(inputClass, "pl-10")}
                />
              </div>
            </Field>

            <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted">
              <span>
                Network:{" "}
                <strong className="font-bold text-ink">
                  {network === "Other" ? "Detecting…" : network}
                </strong>
              </span>
              <button
                type="button"
                onClick={() => setDisbursementPhone(phone.trim())}
                className="font-semibold text-brand hover:underline"
              >
                Use application number
              </button>
            </div>

            {paymentMode === "live" ? (
              <div className="flex items-start gap-2 rounded-xl border border-brand/15 bg-brand-soft px-3 py-2.5 text-[11px] leading-relaxed text-brand-deep">
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                Live verification fees are collected through M-Pesa in Kenya. Use a number
                starting with +254 for a real prompt.
              </div>
            ) : (
              <div className="flex items-start gap-2 rounded-xl border border-line/80 bg-mist/70 px-3 py-2.5 text-[11px] leading-relaxed text-muted">
                <FlaskConical className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand" aria-hidden="true" />
                Sandbox mode accepts Ugandan numbers. No real prompt is sent.
              </div>
            )}

            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => setStage("qualified")}
                className="inline-flex h-11 items-center gap-1.5 rounded-full px-3 text-xs font-semibold text-muted transition hover:bg-mist hover:text-ink"
              >
                <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
                Back
              </button>
              <button
                type="submit"
                className="inline-flex h-12 flex-1 items-center justify-center gap-2 rounded-full bg-ink text-sm font-bold text-white transition hover:bg-ink-soft active:scale-[0.99]"
              >
                Review & authorize
                <ArrowRight className="h-4 w-4 text-accent" aria-hidden="true" />
              </button>
            </div>
          </form>
        ) : null}

        {stage === "send_stk" ? (
          <div className="animate-fade-up space-y-4">
            <div>
              <h2 id="application-title" tabIndex={-1} className="outline-none text-xl font-extrabold tracking-[-0.02em] text-ink">
                Review and pay
              </h2>
              <p className="mt-1 text-xs leading-relaxed text-muted">
                Confirm the details below before sending the payment prompt.
              </p>
            </div>

            <div className="rounded-2xl border border-line/80 bg-mist/50 p-4">
              <DetailRow label="Approved principal" value={formatUgx(principal)} emphasis />
              <DetailRow
                label="Receiving wallet"
                value={`${disbursementPhone} (${network === "Other" ? "mobile money" : network})`}
              />
              <DetailRow
                label="Repayment term"
                value={`${termLabel(months)} · ${formatUgx(quote.monthly)}/mo`}
              />
              <div className="my-1 border-t border-line" />
              <DetailRow label="One-time processing fee" value={formatUgx(fee)} emphasis />
            </div>

            <div className="flex items-start gap-2.5 rounded-2xl border border-brand/15 bg-brand-soft px-3.5 py-3 text-[11px] leading-relaxed text-brand-deep">
              <ReceiptText className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              <span>
                <strong className="block font-bold">This fee is collected before disbursement.</strong>
                It is separate from the loan, is not deducted from the principal, and the
                amount above is exactly what the prompt will request.
              </span>
            </div>

            <div className="flex items-start gap-2.5 rounded-2xl border border-line/80 bg-white px-3.5 py-3 text-[11px] leading-relaxed text-muted">
              <Smartphone className="mt-0.5 h-4 w-4 shrink-0 text-brand" aria-hidden="true" />
              {paymentMode === "demo" ? (
                <span>
                  <strong className="block font-bold text-ink">Sandbox checkout</strong>
                  No real prompt is sent and no money moves. The payment will complete
                  automatically in about six seconds.
                </span>
              ) : (
                <span>
                  <strong className="block font-bold text-ink">M-Pesa prompt</strong>
                  A secure prompt will appear on your phone. Enter your M-Pesa PIN to
                  authorize the fee.
                </span>
              )}
            </div>

            {submitError ? (
              <div className="flex items-start gap-2 rounded-xl border border-danger/20 bg-danger/5 px-3 py-2.5 text-[11px] font-medium text-danger">
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                {submitError}
              </div>
            ) : null}

            <button
              type="button"
              onClick={sendStk}
              disabled={sending}
              className="inline-flex h-12 w-full items-center justify-center gap-2 rounded-full bg-gradient-to-r from-brand to-brand-deep text-sm font-bold text-white transition hover:brightness-110 active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {sending ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  Sending prompt…
                </>
              ) : (
                <>
                  {paymentMode === "demo" ? "Send sandbox payment" : "Send M-Pesa prompt"}
                  <ArrowRight className="h-4 w-4 text-accent" aria-hidden="true" />
                </>
              )}
            </button>

            <div className="flex items-center justify-between gap-2">
              <button
                type="button"
                onClick={() => setStage("disbursement_phone")}
                className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-muted transition hover:text-ink"
              >
                <ArrowLeft className="h-3 w-3" aria-hidden="true" />
                Edit payout details
              </button>
              <span className="inline-flex items-center gap-1.5 text-[10px] text-muted">
                <LockKeyhole className="h-3 w-3 text-success" aria-hidden="true" />
                Gateway secrets stay server-side
              </span>
            </div>
          </div>
        ) : null}

        {stage === "verifying" && payment ? (
          <div className="animate-fade-up space-y-4 py-2 text-center">
            <div className="relative mx-auto flex h-20 w-20 items-center justify-center">
              <div className="absolute inset-0 animate-ping rounded-full bg-brand/15" />
              <div className="absolute inset-2 animate-pulse rounded-full border-2 border-brand/30" />
              <div className="relative z-10 flex h-14 w-14 items-center justify-center rounded-full bg-ink text-accent">
                <Smartphone className="h-6 w-6 animate-bounce" aria-hidden="true" />
              </div>
            </div>

            <div>
              <h2 id="application-title" tabIndex={-1} className="outline-none text-xl font-extrabold tracking-[-0.02em] text-ink">
                {payment.mode === "demo" ? "Confirming sandbox payment" : "Prompt sent to your phone"}
              </h2>
              <p className="mx-auto mt-1 max-w-xs text-xs leading-relaxed text-muted">
                {payment.mode === "demo"
                  ? "No PIN is needed. This sandbox payment confirms on its own."
                  : "Check your handset and enter your mobile money PIN to authorize the fee."}
              </p>
            </div>

            <div className="mx-auto max-w-sm rounded-2xl border border-line/80 bg-mist/50 p-4 text-left">
              <DetailRow label="Target phone" value={payment.phone} />
              <DetailRow label="Network" value={network === "Other" ? "Mobile money" : network} />
              <DetailRow label="Verification fee" value={formatUgx(payment.feeUgx)} emphasis />
              <DetailRow label="Reference" value={payment.reference} />
            </div>

            <div className="mx-auto flex max-w-sm items-center justify-center gap-2 rounded-full border border-success/20 bg-success/5 px-4 py-2.5 text-[11px] font-semibold text-success">
              <span className="h-2 w-2 animate-pulse rounded-full bg-success" aria-hidden="true" />
              Awaiting confirmation · {verifySeconds}s
            </div>

            <div className="mx-auto h-1.5 max-w-sm overflow-hidden rounded-full bg-line">
              <div
                className="h-full rounded-full bg-gradient-to-r from-brand to-accent transition-all duration-500"
                style={{ width: `${Math.max(4, ((45 - verifySeconds) / 45) * 100)}%` }}
              />
            </div>

            <p className="text-[10px] text-muted">
              Keep this window open while the network confirms your transaction.
            </p>
          </div>
        ) : null}

        {stage === "success" && receipt ? (
          <div className="animate-fade-up space-y-4 py-1 text-center">
            <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-success/10 text-success">
              <CheckCircle2 className="h-9 w-9" aria-hidden="true" />
            </span>
            <div>
              <h2 id="application-title" tabIndex={-1} className="outline-none text-xl font-extrabold tracking-[-0.02em] text-ink">
                {receipt.mode === "demo" ? "Sandbox payment complete" : "Payment received"}
              </h2>
              <p className="mx-auto mt-1 max-w-sm text-xs leading-relaxed text-muted">
                {receipt.mode === "demo"
                  ? "No real money moved. This record is stored only in your browser so you can test the full flow."
                  : "Your verification fee was confirmed. The loan is queued for disbursement to your mobile money wallet."}
              </p>
            </div>

            <div className="rounded-2xl border border-line/80 bg-mist/50 p-4 text-left">
              <div className="flex items-center justify-between border-b border-line py-1.5">
                <span className="text-[11px] text-muted">Status</span>
                <span className="rounded-full bg-success/10 px-2.5 py-0.5 text-[10px] font-bold text-success">
                  {receipt.mode === "demo" ? "Sandbox · simulated" : "Fee paid · queued"}
                </span>
              </div>
              <DetailRow label="Receipt ID" value={receipt.receipt ?? receipt.reference} />
              <DetailRow label="Approved loan" value={formatUgx(receipt.principal)} emphasis />
              <DetailRow label="Disbursed to" value={`${receipt.phone} (${receipt.network})`} />
              <DetailRow label="Repayment" value={`${receipt.months} months · ${formatUgx(receipt.monthly)}/mo`} />
              <DetailRow label="First repayment due" value={new Date(receipt.dueDate).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })} />
            </div>

            <div className="flex gap-2">
              <button
                type="button"
                onClick={copyReference}
                className="inline-flex h-11 flex-1 items-center justify-center gap-1.5 rounded-full border border-line bg-white text-xs font-semibold text-ink transition hover:bg-mist"
              >
                {copied ? (
                  <>
                    <Check className="h-3.5 w-3.5 text-success" aria-hidden="true" />
                    Copied
                  </>
                ) : (
                  <>
                    <Copy className="h-3.5 w-3.5" aria-hidden="true" />
                    Copy reference
                  </>
                )}
              </button>
              <button
                type="button"
                onClick={onClose}
                className="inline-flex h-11 flex-1 items-center justify-center rounded-full bg-ink text-xs font-bold text-white transition hover:bg-ink-soft"
              >
                Done
              </button>
            </div>

            <p className="text-[10px] text-muted">
              Find this record anytime under <strong className="font-semibold text-ink">My loans</strong>.
            </p>
          </div>
        ) : null}

        {stage === "failed" ? (
          <div className="animate-fade-up space-y-4 py-1 text-center">
            <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-danger/10 text-danger">
              <AlertCircle className="h-8 w-8" aria-hidden="true" />
            </span>
            <div>
              <h2 id="application-title" tabIndex={-1} className="outline-none text-xl font-extrabold tracking-[-0.02em] text-ink">
                Payment not confirmed
              </h2>
              <p className="mx-auto mt-1 max-w-sm text-xs leading-relaxed text-muted">
                {failureReason || "The payment session timed out or was cancelled on your phone."}
              </p>
            </div>

            <div className="rounded-2xl border border-line/80 bg-mist/50 p-4 text-left">
              <DetailRow label="Attempted number" value={payment?.phone ?? disbursementPhone} />
              {payment ? <DetailRow label="Reference" value={payment.reference} /> : null}
              <DetailRow label="Fee" value={formatUgx(payment?.feeUgx ?? fee)} />
              <DetailRow label="Status" value="Cancelled / timed out" />
            </div>

            {submitError ? (
              <div className="flex items-start gap-2 rounded-xl border border-danger/20 bg-danger/5 px-3 py-2.5 text-left text-[11px] font-medium text-danger">
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                {submitError}
              </div>
            ) : null}

            <div className="flex flex-col gap-2 sm:flex-row">
              <button
                type="button"
                onClick={sendStk}
                disabled={sending}
                className="inline-flex h-11 flex-1 items-center justify-center gap-1.5 rounded-full bg-ink text-xs font-bold text-white transition hover:bg-ink-soft disabled:opacity-60"
              >
                {sending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                ) : (
                  <RotateCcw className="h-3.5 w-3.5 text-accent" aria-hidden="true" />
                )}
                Try payment again
              </button>
              <button
                type="button"
                onClick={() => {
                  setSubmitError("");
                  setStage("disbursement_phone");
                }}
                className="inline-flex h-11 flex-1 items-center justify-center rounded-full border border-line bg-white text-xs font-semibold text-ink-soft transition hover:bg-mist"
              >
                Change number
              </button>
            </div>

            <p className="text-[10px] leading-relaxed text-muted">
              If you cancelled the prompt, no fee should be charged. Confirm with your
              mobile money operator if you are unsure.
            </p>
          </div>
        ) : null}

        <div className="sr-only" aria-live="polite">
          Step {stepActive + 1} of {STEPS.length}: {STEPS[stepActive]}
        </div>
      </div>
    </ModalShell>
  );
}
