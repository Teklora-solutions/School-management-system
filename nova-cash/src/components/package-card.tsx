import { ArrowRight, ReceiptText } from "lucide-react";
import {
  formatUgx,
  lowestMonthly,
  type LoanPackage,
} from "@/lib/loans";

export function PackageCard({
  item,
  onApply,
}: {
  item: LoanPackage;
  onApply: (item: LoanPackage) => void;
}) {
  return (
    <article className="group relative flex flex-col overflow-hidden rounded-card border border-line/80 bg-white p-5 shadow-card transition duration-200 hover:-translate-y-0.5 hover:border-brand/25 hover:shadow-float sm:p-6">
      {item.popular ? (
        <span className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-brand via-brand to-accent" />
      ) : null}

      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-muted">
            Loan amount
          </p>
          <h3 className="mt-1 flex items-baseline gap-1.5 text-ink">
            <span className="text-xs font-bold text-muted">UGX</span>
            <span className="text-[1.9rem] font-extrabold leading-none tracking-[-0.03em] sm:text-[2.1rem]">
              {item.amount.toLocaleString("en-US")}
            </span>
          </h3>
        </div>
        {item.popular ? (
          <span className="shrink-0 rounded-full bg-accent px-2.5 py-1 text-[9px] font-extrabold uppercase tracking-[0.14em] text-ink">
            Popular
          </span>
        ) : null}
      </div>

      <div className="mt-3.5 inline-flex w-fit items-center gap-1.5 rounded-full bg-mist px-2.5 py-1 text-[11px] font-medium text-ink-soft">
        <ReceiptText className="h-3.5 w-3.5 text-brand" aria-hidden="true" />
        Processing:
        <strong className="font-bold">{formatUgx(item.fee)}</strong>
      </div>

      <div className="mt-auto flex items-end justify-between gap-3 border-t border-line/80 pt-4 sm:pt-5">
        <div>
          <p className="text-[11px] font-medium text-muted">From</p>
          <p className="mt-0.5 text-lg font-extrabold tracking-[-0.02em] text-ink sm:text-xl">
            {formatUgx(lowestMonthly(item.amount))}
            <span className="text-[11px] font-semibold text-muted">/mo</span>
          </p>
          <span className="mt-2 inline-flex rounded-full bg-success/10 px-2.5 py-1 text-[10px] font-bold text-success">
            3–12 mo terms
          </span>
        </div>

        <button
          type="button"
          onClick={() => onApply(item)}
          aria-label={`Apply for ${formatUgx(item.amount)}`}
          className="inline-flex h-10 shrink-0 items-center gap-1.5 rounded-full bg-accent px-4 text-xs font-bold text-ink transition hover:bg-accent-strong active:scale-[0.98] sm:text-sm"
        >
          Apply now
          <ArrowRight className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
    </article>
  );
}
