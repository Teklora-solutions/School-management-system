import { ArrowRight, Calculator } from "lucide-react";

export function CalculatorBanner({ onLaunch }: { onLaunch: () => void }) {
  return (
    <section className="mx-auto w-full max-w-4xl px-4 py-6 sm:px-6">
      <div className="relative overflow-hidden rounded-panel bg-gradient-to-br from-ink via-ink-soft to-brand-deep p-6 text-white shadow-card sm:p-8">
        <div className="pointer-events-none absolute -right-14 -top-16 h-56 w-56 rounded-full bg-brand/30 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-20 left-10 h-48 w-48 rounded-full bg-accent/15 blur-3xl" />

        <div className="relative flex flex-col items-start justify-between gap-5 sm:flex-row sm:items-center">
          <div className="max-w-md">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-white/10 px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.18em] text-accent">
              <Calculator className="h-3 w-3" aria-hidden="true" />
              Custom amount
            </span>
            <h3 className="mt-3 text-balance text-xl font-extrabold tracking-[-0.02em] sm:text-2xl">
              Need a custom loan amount?
            </h3>
            <p className="mt-2 text-xs leading-relaxed text-white/60 sm:text-sm">
              Simulate any amount from {`UGX 650,000`} to {`UGX 5,000,000`} and compare 3, 6
              or 12-month repayment plans before you apply.
            </p>
          </div>

          <button
            type="button"
            onClick={onLaunch}
            className="inline-flex h-11 w-full shrink-0 items-center justify-center gap-2 rounded-full bg-accent px-6 text-sm font-bold text-ink transition hover:bg-accent-strong active:scale-[0.99] sm:w-auto"
          >
            Launch calculator
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </div>
    </section>
  );
}
