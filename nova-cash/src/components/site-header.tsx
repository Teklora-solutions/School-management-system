"use client";

import { useState } from "react";
import {
  ArrowUpRight,
  Calculator,
  CircleHelp,
  LayoutGrid,
  Menu,
  Wallet,
  X,
} from "lucide-react";
import { Logo } from "./brand";

const LINKS = [
  { href: "#packages", label: "Loan plans", icon: LayoutGrid },
  { href: "#calculator", label: "Calculator", icon: Calculator },
  { href: "#faq", label: "FAQ", icon: CircleHelp },
];

export function SiteHeader({
  onApply,
  onOpenLoans,
  onOpenCalculator,
  loanCount,
}: {
  onApply: () => void;
  onOpenLoans: () => void;
  onOpenCalculator: () => void;
  loanCount: number;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const closeMenu = () => setMenuOpen(false);

  return (
    <header className="sticky top-0 z-30 border-b border-line/70 bg-mist/85 backdrop-blur-xl">
      <div className="mx-auto flex h-[72px] w-full max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
        <a href="#top" className="shrink-0 rounded-xl" aria-label="NovaCash home">
          <Logo tagline taglineClassName="hidden sm:block" markClassName="h-9 w-9 sm:h-10 sm:w-10" />
        </a>

        <nav className="hidden items-center gap-1 md:flex" aria-label="Main">
          {LINKS.map((link) => (
            <a
              key={link.href}
              href={link.href}
              onClick={link.href === "#calculator" ? (event) => { event.preventDefault(); onOpenCalculator(); } : undefined}
              className="whitespace-nowrap rounded-full px-3 py-2 text-sm font-medium text-muted transition-colors hover:bg-white hover:text-ink lg:px-3.5"
            >
              {link.label}
            </a>
          ))}
        </nav>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onOpenLoans}
            aria-label={`My loans (${loanCount} saved)`}
            className="inline-flex h-10 items-center gap-2 rounded-full border border-line bg-white px-3.5 text-sm font-semibold text-ink shadow-sm transition hover:border-brand/30 hover:text-brand"
          >
            <Wallet className="h-4 w-4" aria-hidden="true" />
            <span className="hidden sm:inline">Loans</span>
            <span className="inline-flex min-w-5 items-center justify-center rounded-full bg-brand-soft px-1.5 text-[11px] font-bold text-brand">
              {loanCount}
            </span>
          </button>

          <button
            type="button"
            onClick={onApply}
            className="hidden h-10 items-center gap-1.5 rounded-full bg-ink px-4 text-sm font-semibold text-white shadow-sm transition hover:bg-ink-soft active:scale-[0.98] sm:inline-flex"
          >
            Apply
            <ArrowUpRight className="h-4 w-4 text-accent" aria-hidden="true" />
          </button>

          <button
            type="button"
            onClick={() => setMenuOpen((open) => !open)}
            aria-expanded={menuOpen}
            aria-controls="mobile-menu"
            aria-label={menuOpen ? "Close menu" : "Open menu"}
            className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-line bg-white text-ink shadow-sm transition hover:border-brand/30 md:hidden"
          >
            {menuOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
        </div>
      </div>

      {menuOpen ? (
        <div id="mobile-menu" className="animate-fade-down border-t border-line/70 bg-white/95 md:hidden">
          <nav className="mx-auto grid w-full max-w-6xl gap-1 px-4 py-4" aria-label="Mobile">
            {LINKS.map((link) => (
              <a
                key={link.href}
                href={link.href}
                onClick={(event) => {
                  if (link.href === "#calculator") {
                    event.preventDefault();
                    onOpenCalculator();
                  }
                  closeMenu();
                }}
                className="flex items-center gap-3 rounded-2xl px-3 py-3 text-sm font-semibold text-ink transition hover:bg-mist"
              >
                <link.icon className="h-4 w-4 text-brand" aria-hidden="true" />
                {link.label}
              </a>
            ))}
            <button
              type="button"
              onClick={() => {
                closeMenu();
                onOpenLoans();
              }}
              className="flex items-center gap-3 rounded-2xl px-3 py-3 text-left text-sm font-semibold text-ink transition hover:bg-mist"
            >
              <Wallet className="h-4 w-4 text-brand" aria-hidden="true" />
              My loans
              <span className="ml-auto inline-flex min-w-5 items-center justify-center rounded-full bg-brand-soft px-1.5 text-[11px] font-bold text-brand">
                {loanCount}
              </span>
            </button>
            <button
              type="button"
              onClick={() => {
                closeMenu();
                onApply();
              }}
              className="mt-1 flex items-center gap-3 rounded-2xl bg-ink px-3 py-3 text-left text-sm font-bold text-white transition hover:bg-ink-soft"
            >
              <ArrowUpRight className="h-4 w-4 text-accent" aria-hidden="true" />
              Apply for a loan
            </button>
          </nav>
        </div>
      ) : null}
    </header>
  );
}
