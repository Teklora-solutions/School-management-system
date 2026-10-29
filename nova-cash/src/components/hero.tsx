import {
  ArrowRight,
  FlaskConical,
  ReceiptText,
  ShieldCheck,
  Smartphone,
  Zap,
} from "lucide-react";
import type { PaymentMode } from "@/lib/loans";
import { PACKAGES, formatUgx } from "@/lib/loans";

const chips = [
  { label: "Fee shown upfront", icon: ReceiptText },
  { label: "Instant decision", icon: Zap },
  { label: "No collateral", icon: ShieldCheck },
];

export function Hero({
  onApply,
  paymentMode,
}: {
  onApply: () => void;
  paymentMode: PaymentMode;
}) {
  const lowestFee = PACKAGES[0]?.fee ?? 9_371;

  return (
    <section id="top" className="mx-auto w-full max-w-6xl px-4 pt-6 sm:px-6 sm:pt-10">
      <div className="relative overflow-hidden rounded-panel border border-white/70 bg-white px-5 py-10 text-center shadow-card sm:px-10 sm:py-14">
        <div className="pointer-events-none absolute inset-0">
          <div className="grid-fade absolute inset-0 opacity-60" />
          <div className="absolute -left-24 -top-24 h-64 w-64 rounded-full bg-brand/10 blur-3xl" />
          <div className="absolute -bottom-28 -right-16 h-72 w-72 rounded-full bg-accent/25 blur-3xl" />
        </div>

        <div className="relative mx-auto flex max-w-2xl flex-col items-center">
          <span className="inline-flex items-center gap-2 rounded-full border border-brand/15 bg-brand-soft px-3.5 py-1.5 text-[11px] font-bold uppercase tracking-[0.2em] text-brand-deep">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand/60" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-brand" />
            </span>
            Instant decisions
          </span>

          <h1 className="mt-5 text-balance text-[2.6rem] font-extrabold leading-[1.02] tracking-[-0.04em] text-ink sm:text-6xl">
            Get your{" "}
            <span className="relative mx-1 inline-block px-1">
              <span className="absolute inset-x-0 bottom-[0.06em] top-[0.08em] -rotate-1 rounded-2xl bg-accent" />
              <span className="relative">loan</span>
            </span>
          </h1>

          <p className="mt-5 max-w-xl text-balance text-sm leading-relaxed text-muted sm:text-base">
            Pick an amount, see the full cost before you commit, and receive funds in your
            mobile money wallet. Transparent fees, flexible terms, no collateral.
          </p>

          <button
            type="button"
            onClick={onApply}
            className="group mt-7 inline-flex h-14 w-full max-w-md items-center justify-center gap-2.5 rounded-full bg-gradient-to-r from-brand via-brand to-brand-deep px-7 text-sm font-bold text-white shadow-[0_18px_32px_-18px_rgba(79,70,229,0.85)] transition hover:brightness-110 active:scale-[0.99] sm:w-auto sm:text-base"
          >
            Apply for a loan now
            <ArrowRight className="h-5 w-5 text-accent transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
          </button>

          <div className="mt-5 flex w-full flex-wrap items-center justify-center gap-2">
            {chips.map((chip) => (
              <span
                key={chip.label}
                className="inline-flex items-center gap-1.5 rounded-full border border-line bg-mist/70 px-3 py-1.5 text-[11px] font-semibold text-ink-soft sm:text-xs"
              >
                <chip.icon className="h-3.5 w-3.5 text-brand" aria-hidden="true" />
                {chip.label}
              </span>
            ))}
          </div>

          <div className="mt-7 flex flex-wrap items-center justify-center gap-x-4 gap-y-2 border-t border-line/80 pt-5 text-[11px] font-medium text-muted sm:text-xs">
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-[#f5c518]" aria-hidden="true" />
              MTN MoMo
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-[#e0242b]" aria-hidden="true" />
              Airtel Money
            </span>
            <span className="inline-flex items-center gap-1.5">
              <Smartphone className="h-3.5 w-3.5 text-brand" aria-hidden="true" />
              {paymentMode === "demo" ? "Sandbox payments" : "M-Pesa via OptimaPay"}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <ReceiptText className="h-3.5 w-3.5 text-brand" aria-hidden="true" />
              Verification fee from {formatUgx(lowestFee)}
            </span>
          </div>

          {paymentMode === "demo" ? (
            <p className="mt-4 inline-flex items-center gap-1.5 rounded-full bg-ink px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-accent">
              <FlaskConical className="h-3 w-3" aria-hidden="true" />
              Sandbox mode · no real money moves
            </p>
          ) : null}
        </div>
      </div>
    </section>
  );
}
