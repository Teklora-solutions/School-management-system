import { ArrowUpRight, FlaskConical, Mail, MapPin, ShieldCheck } from "lucide-react";
import type { PaymentMode } from "@/lib/loans";
import { Logo } from "./brand";

export function SiteFooter({
  onOpenLoans,
  onOpenCalculator,
  paymentMode,
}: {
  onOpenLoans: () => void;
  onOpenCalculator: () => void;
  paymentMode: PaymentMode;
}) {
  return (
    <footer className="mt-auto bg-ink text-white">
      <div className="mx-auto w-full max-w-6xl px-4 py-12 sm:px-6 sm:py-14">
        <div className="grid gap-10 md:grid-cols-[1.5fr_1fr_1fr]">
          <div>
            <Logo tone="light" tagline />
            <p className="mt-4 max-w-sm text-xs leading-relaxed text-white/55 sm:text-[13px]">
              NovaCash is a demonstration instant-loan experience for Uganda. Compare
              plans, preview repayments and walk through the mobile money flow end to end.
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              <span className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-2.5 py-1.5 text-[10px] font-semibold text-white/70">
                <ShieldCheck className="h-3 w-3 text-accent" aria-hidden="true" />
                Fees disclosed upfront
              </span>
              {paymentMode === "demo" ? (
                <span className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-2.5 py-1.5 text-[10px] font-semibold text-white/70">
                  <FlaskConical className="h-3 w-3 text-accent" aria-hidden="true" />
                  Sandbox checkout
                </span>
              ) : null}
            </div>
          </div>

          <div>
            <h4 className="text-[10px] font-bold uppercase tracking-[0.2em] text-accent">
              Services
            </h4>
            <ul className="mt-4 space-y-2.5 text-xs text-white/65">
              <li>
                <a href="#packages" className="transition hover:text-white">
                  Loan packages (UGX 650K – 5M)
                </a>
              </li>
              <li>
                <button type="button" onClick={onOpenCalculator} className="text-left transition hover:text-white">
                  Interactive loan calculator
                </button>
              </li>
              <li>
                <button type="button" onClick={onOpenLoans} className="text-left transition hover:text-white">
                  My loans
                </button>
              </li>
              <li>
                <a
                  href="https://global.optimapaybridge.co.ke/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 transition hover:text-white"
                >
                  OptimaPay payment gateway
                  <ArrowUpRight className="h-3 w-3" aria-hidden="true" />
                </a>
              </li>
            </ul>
          </div>

          <div>
            <h4 className="text-[10px] font-bold uppercase tracking-[0.2em] text-accent">
              Contact
            </h4>
            <ul className="mt-4 space-y-3 text-xs text-white/65">
              <li className="flex items-start gap-2">
                <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" aria-hidden="true" />
                Kampala, Uganda
              </li>
              <li className="flex items-start gap-2">
                <Mail className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" aria-hidden="true" />
                <a href="mailto:support@novacash.example" className="break-all transition hover:text-white">
                  support@novacash.example
                </a>
              </li>
              <li className="text-white/45">Demo support · Mon–Sun, 8:00–20:00 EAT</li>
            </ul>
          </div>
        </div>

        <div className="mt-10 rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-[10px] leading-relaxed text-white/45 sm:text-[11px]">
          <p>
            <strong className="font-bold text-white/70">Demo notice:</strong> package
            figures, eligibility results and payment confirmations in sandbox mode are
            illustrative. No credit bureau is contacted and no real money moves. When live
            mode is enabled, the verification fee is collected by OptimaPay before
            disbursement. Confirm licensing and consumer-credit requirements in your market
            before offering real loans. Borrow responsibly.
          </p>
        </div>

        <div className="mt-6 flex flex-col items-start justify-between gap-2 border-t border-white/10 pt-5 text-[10px] text-white/40 sm:flex-row sm:items-center sm:text-[11px]">
          <span>© 2026 NovaCash · Instant Loans Uganda · All rights reserved.</span>
          <span>Payments processed by OptimaPay · M-Pesa STK Push</span>
        </div>
      </div>
    </footer>
  );
}
