"use client";

import { useState } from "react";
import { ArrowRight, Calculator, X } from "lucide-react";
import {
  AMOUNT_STEP_UGX,
  CALCULATOR_TERMS,
  FLAT_RATES,
  MAX_LOAN_UGX,
  MIN_LOAN_UGX,
  feeForAmount,
  formatCompact,
  formatUgx,
  loanQuote,
  type TermMonths,
} from "@/lib/loans";
import { cn } from "@/lib/cn";

const PRESETS = [650_000, 1_100_000, 2_150_000, 3_650_000, 5_000_000];

function clampAmount(value: number): number {
  const stepped = Math.round(value / AMOUNT_STEP_UGX) * AMOUNT_STEP_UGX;
  return Math.min(MAX_LOAN_UGX, Math.max(MIN_LOAN_UGX, stepped));
}

export function CalculatorPanel({
  onApply,
  onClose,
}: {
  onApply: (amount: number) => void;
  onClose: () => void;
}) {
  const [amount, setAmount] = useState(1_100_000);
  const [months, setMonths] = useState<TermMonths>(6);

  const quote = loanQuote(amount, months);
  const fee = feeForAmount(amount);
  const interest = quote.total - amount;
  const progress = ((amount - MIN_LOAN_UGX) / (MAX_LOAN_UGX - MIN_LOAN_UGX)) * 100;

  return (
    <section
      id="calculator"
      className="mx-auto w-full max-w-5xl scroll-mt-24 px-4 pt-2 sm:px-6"
      aria-label="Loan calculator"
    >
      <div className="animate-fade-up overflow-hidden rounded-panel border border-line/80 bg-white shadow-card">
        <div className="flex items-start justify-between gap-4 border-b border-line/80 px-5 py-4 sm:px-7 sm:py-5">
          <div className="flex items-center gap-3">
            <span className="inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-brand-soft text-brand">
              <Calculator className="h-5 w-5" aria-hidden="true" />
            </span>
            <div>
              <h3 className="text-base font-extrabold tracking-tight text-ink sm:text-lg">
                Build your loan
              </h3>
              <p className="text-[11px] text-muted sm:text-xs">
                Move the slider and compare the full cost.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close calculator"
            className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-line bg-white text-muted transition hover:bg-mist hover:text-ink"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="grid gap-6 p-5 sm:p-7 lg:grid-cols-[1.1fr_0.9fr] lg:gap-8">
          <div>
            <div className="flex items-end justify-between gap-3">
              <div>
                <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-muted">
                  Loan amount
                </p>
                <p className="mt-1 text-3xl font-extrabold tracking-[-0.035em] text-ink sm:text-4xl">
                  {formatUgx(amount)}
                </p>
              </div>
              <span className="rounded-full bg-mist px-2.5 py-1 text-[10px] font-semibold text-muted">
                {formatCompact(MIN_LOAN_UGX)}–{formatCompact(MAX_LOAN_UGX)}
              </span>
            </div>

            <input
              type="range"
              min={MIN_LOAN_UGX}
              max={MAX_LOAN_UGX}
              step={AMOUNT_STEP_UGX}
              value={amount}
              onChange={(event) => setAmount(clampAmount(Number(event.target.value)))}
              aria-label="Loan amount in Ugandan shillings"
              aria-valuetext={formatUgx(amount)}
              className="range-brand mt-5"
              style={{
                background: `linear-gradient(to right, var(--color-brand) 0%, var(--color-brand) ${progress}%, #e6e4f2 ${progress}%, #e6e4f2 100%)`,
              }}
            />
            <div className="mt-1.5 flex justify-between text-[10px] font-medium text-muted">
              <span>{formatUgx(MIN_LOAN_UGX)}</span>
              <span>{formatUgx(MAX_LOAN_UGX)}</span>
            </div>

            <div className="mt-4 flex flex-wrap gap-1.5">
              {PRESETS.map((preset) => (
                <button
                  key={preset}
                  type="button"
                  onClick={() => setAmount(preset)}
                  aria-pressed={amount === preset}
                  className={cn(
                    "rounded-full border px-2.5 py-1.5 text-[11px] font-semibold transition",
                    amount === preset
                      ? "border-brand bg-brand-soft text-brand-deep"
                      : "border-line bg-white text-muted hover:border-brand/30 hover:text-brand",
                  )}
                >
                  {formatCompact(preset)}
                </button>
              ))}
            </div>

            <div className="mt-6">
              <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-muted">
                Repayment term
              </p>
              <div className="mt-2.5 grid grid-cols-3 gap-2">
                {CALCULATOR_TERMS.map((term) => {
                  const active = term === months;
                  return (
                    <button
                      key={term}
                      type="button"
                      onClick={() => setMonths(term)}
                      aria-pressed={active}
                      className={cn(
                        "rounded-2xl border px-2 py-2.5 text-center transition",
                        active
                          ? "border-ink bg-ink text-white shadow-sm"
                          : "border-line bg-white text-ink-soft hover:border-brand/30",
                      )}
                    >
                      <span className="block text-sm font-bold">{term} months</span>
                      <span className={cn("mt-0.5 block text-[10px]", active ? "text-accent" : "text-muted")}>
                        {Math.round(FLAT_RATES[term] * 100)}% flat
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          <div className="flex flex-col rounded-[1.6rem] bg-gradient-to-br from-ink via-ink-soft to-brand-deep p-5 text-white sm:p-6">
            <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-accent">
              Your estimate
            </p>
            <p className="mt-3 text-[11px] font-medium text-white/60">Monthly repayment</p>
            <p className="mt-0.5 text-[2rem] font-extrabold leading-tight tracking-[-0.03em]">
              {formatUgx(quote.monthly)}
              <span className="text-xs font-medium text-white/50"> /mo</span>
            </p>

            <div className="mt-4 space-y-2 border-t border-white/10 pt-4 text-[11px] sm:text-xs">
              <div className="flex items-center justify-between gap-3">
                <span className="text-white/60">Principal</span>
                <span className="font-semibold">{formatUgx(amount)}</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-white/60">
                  Flat interest ({Math.round(quote.rate * 100)}%)
                </span>
                <span className="font-semibold">{formatUgx(interest)}</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-white/60">Total repayment</span>
                <span className="font-bold text-accent">{formatUgx(quote.total)}</span>
              </div>
              <div className="flex items-center justify-between gap-3 border-t border-white/10 pt-2">
                <span className="text-white/60">One-time processing fee</span>
                <span className="font-bold">{formatUgx(fee)}</span>
              </div>
            </div>

            <p className="mt-3 text-[10px] leading-relaxed text-white/45">
              The processing fee is paid upfront and is not deducted from your principal.
              Estimates only — your final offer is shown before payment.
            </p>

            <button
              type="button"
              onClick={() => onApply(amount)}
              className="mt-auto inline-flex h-11 w-full items-center justify-center gap-2 rounded-full bg-accent px-4 pt-0 text-xs font-bold text-ink transition hover:bg-accent-strong active:scale-[0.99] sm:text-sm"
            >
              Apply for {formatUgx(amount)}
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
