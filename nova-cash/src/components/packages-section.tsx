"use client";

import { useState } from "react";
import { Info } from "lucide-react";
import {
  FILTERS,
  filterPackages,
  formatUgx,
  type LoanPackage,
  type PackageFilter,
} from "@/lib/loans";
import { PackageCard } from "./package-card";
import { cn } from "@/lib/cn";

export function PackagesSection({
  onApply,
}: {
  onApply: (item: LoanPackage) => void;
}) {
  const [filter, setFilter] = useState<PackageFilter>("all");
  const visible = filterPackages(filter);
  const lowestFee = 9_371;

  return (
    <section id="packages" className="mx-auto w-full max-w-6xl scroll-mt-24 px-4 py-12 sm:px-6 sm:py-16">
      <div className="text-center">
        <h2 className="text-balance text-3xl font-extrabold tracking-[-0.035em] text-ink sm:text-4xl">
          Choose your loan amount
        </h2>
        <p className="mx-auto mt-3 max-w-xl text-sm leading-relaxed text-muted">
          Every plan shows its one-time processing fee upfront · fees from{" "}
          <strong className="font-bold text-ink">{formatUgx(lowestFee)}</strong>
        </p>
      </div>

      <div className="mt-6 flex flex-wrap items-center justify-center gap-2" role="group" aria-label="Filter loan plans">
        {FILTERS.map((option) => {
          const active = option.id === filter;
          return (
            <button
              key={option.id}
              type="button"
              onClick={() => setFilter(option.id)}
              aria-pressed={active}
              className={cn(
                "inline-flex items-center gap-2 rounded-full border px-4 py-2 text-xs font-semibold transition",
                active
                  ? "border-ink bg-ink text-white shadow-sm"
                  : "border-line bg-white text-ink-soft hover:border-brand/30 hover:text-brand",
              )}
            >
              {option.label}
              <span
                className={cn(
                  "rounded-full px-1.5 py-0.5 text-[10px] font-bold",
                  active ? "bg-white/15 text-accent" : "bg-mist text-muted",
                )}
              >
                {option.caption}
              </span>
            </button>
          );
        })}
      </div>

      <div key={filter} className="mt-6 grid animate-fade-up gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {visible.map((item) => (
          <PackageCard key={item.id} item={item} onApply={onApply} />
        ))}
      </div>

      <div className="mt-6 flex items-start gap-2.5 rounded-2xl border border-line/80 bg-white/70 px-4 py-3.5 text-[11px] leading-relaxed text-muted sm:text-xs">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-brand" aria-hidden="true" />
        <p>
          Monthly figures above use the shortest 3-month term. Interest is flat — 5%, 10%,
          15% or 20% of the principal for 3, 6, 9 or 12 months. The one-time processing fee
          is listed on every plan and is collected separately before disbursement.
        </p>
      </div>
    </section>
  );
}
