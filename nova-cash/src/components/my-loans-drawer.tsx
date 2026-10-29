"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  ArrowRight,
  Check,
  Copy,
  FlaskConical,
  Info,
  Trash2,
  Wallet,
  X,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { formatDate, formatUgx, type SavedLoan } from "@/lib/loans";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function MyLoansDrawer({
  open,
  loans,
  onClose,
  onApplyNew,
  onClearLoans,
}: {
  open: boolean;
  loans: SavedLoan[];
  onClose: () => void;
  onApplyNew: () => void;
  onClearLoans: () => void;
}) {
  const panelRef = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  const [copiedReference, setCopiedReference] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);

  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const panel = panelRef.current;
    const focusables = () =>
      Array.from(panel?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []).filter(
        (element) => element.offsetParent !== null,
      );

    const timer = window.setTimeout(() => {
      (panel?.querySelector<HTMLElement>("[data-autofocus]") ?? panel)?.focus();
    }, 20);

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusables();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      previousFocus?.focus?.();
    };
  }, [open]);

  useEffect(() => {
    if (!copiedReference) return;
    const timer = window.setTimeout(() => setCopiedReference(null), 2000);
    return () => window.clearTimeout(timer);
  }, [copiedReference]);

  async function copyReference(reference: string) {
    try {
      await navigator.clipboard.writeText(reference);
      setCopiedReference(reference);
    } catch {
      setCopiedReference(null);
    }
  }

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex justify-end bg-ink/65 backdrop-blur-sm"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <aside
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="loans-title"
        tabIndex={-1}
        className="flex h-full w-full max-w-md animate-slide-in flex-col bg-white shadow-float outline-none"
      >
        <div className="flex items-center justify-between gap-3 border-b border-line/80 px-5 py-4">
          <div className="flex items-center gap-2.5">
            <span className="inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-ink text-accent">
              <Wallet className="h-5 w-5" aria-hidden="true" />
            </span>
            <div>
              <h2 id="loans-title" className="text-lg font-extrabold tracking-tight text-ink">
                My loans
              </h2>
              <p className="text-[11px] text-muted">Confirmed payments · saved locally</p>
            </div>
          </div>
          <button
            type="button"
            data-autofocus
            onClick={onClose}
            aria-label="Close my loans"
            className="inline-flex h-9 w-9 items-center justify-center rounded-full text-muted transition hover:bg-mist hover:text-ink"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="scroll-thin flex-1 overflow-y-auto px-5 py-5">
          {loans.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center px-4 py-12 text-center">
              <span className="flex h-16 w-16 items-center justify-center rounded-full bg-mist text-muted">
                <Wallet className="h-8 w-8" aria-hidden="true" />
              </span>
              <h3 className="mt-3 text-base font-extrabold text-ink">No applications yet</h3>
              <p className="mt-1 max-w-xs text-xs leading-relaxed text-muted">
                A loan is saved here only after every step is complete, including the
                mobile money payment prompt.
              </p>
              <button
                type="button"
                onClick={onApplyNew}
                className="mt-5 inline-flex h-11 items-center gap-2 rounded-full bg-ink px-5 text-xs font-bold text-white transition hover:bg-ink-soft"
              >
                Browse loan plans
                <ArrowRight className="h-4 w-4 text-accent" aria-hidden="true" />
              </button>
            </div>
          ) : (
            <div className="space-y-4">
              {loans.map((loan) => (
                <article
                  key={loan.id}
                  className="rounded-2xl border border-line/90 bg-mist/40 p-4 shadow-sm"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span
                      className={cn(
                        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-bold",
                        loan.mode === "demo"
                          ? "bg-brand-soft text-brand-deep"
                          : "bg-success/10 text-success",
                      )}
                    >
                      {loan.mode === "demo" ? (
                        <FlaskConical className="h-3 w-3" aria-hidden="true" />
                      ) : (
                        <Check className="h-3 w-3" aria-hidden="true" />
                      )}
                      {loan.mode === "demo" ? "Sandbox · simulated" : "Fee paid · queued"}
                    </span>

                    <button
                      type="button"
                      onClick={() => copyReference(loan.reference)}
                      title="Copy reference"
                      className="inline-flex items-center gap-1 rounded-lg border border-line bg-white px-2 py-1 font-mono text-[10px] font-bold text-ink transition hover:bg-mist"
                    >
                      {loan.reference}
                      {copiedReference === loan.reference ? (
                        <Check className="h-3 w-3 text-success" aria-hidden="true" />
                      ) : (
                        <Copy className="h-3 w-3 text-muted" aria-hidden="true" />
                      )}
                    </button>
                  </div>

                  <div className="mt-3 flex items-baseline justify-between gap-3">
                    <span className="text-xs text-muted">Principal</span>
                    <span className="text-xl font-extrabold tracking-[-0.02em] text-ink">
                      {formatUgx(loan.principal)}
                    </span>
                  </div>

                  <div className="mt-3 space-y-1.5 border-t border-line/80 pt-3 text-[11px] text-muted">
                    <div className="flex justify-between gap-3">
                      <span>Credited to</span>
                      <span className="text-right font-semibold text-ink">
                        {loan.phone} ({loan.network})
                      </span>
                    </div>
                    <div className="flex justify-between gap-3">
                      <span>Repayment plan</span>
                      <span className="font-semibold text-ink">
                        {loan.months} months · {formatUgx(loan.monthly)}/mo
                      </span>
                    </div>
                    <div className="flex justify-between gap-3">
                      <span>Total due</span>
                      <span className="font-bold text-ink">{formatUgx(loan.total)}</span>
                    </div>
                    <div className="flex justify-between gap-3">
                      <span>Processing fee</span>
                      <span className="font-semibold text-ink">{formatUgx(loan.fee)}</span>
                    </div>
                    <div className="flex justify-between gap-3">
                      <span>First due date</span>
                      <span className="font-semibold text-success">{formatDate(loan.dueDate)}</span>
                    </div>
                  </div>

                  <div className="mt-3 flex items-start gap-2 rounded-xl bg-white px-3 py-2.5 text-[10px] leading-relaxed text-muted">
                    <Info className="mt-0.5 h-3 w-3 shrink-0 text-brand" aria-hidden="true" />
                    <span>
                      To repay, open your mobile money menu → Payments → Goods & Services and
                      enter reference <strong className="font-semibold text-ink">{loan.reference}</strong>.
                    </span>
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>

        <div className="border-t border-line/80 bg-mist/60 px-5 py-4">
          {loans.length > 0 ? (
            <div className="mb-3 flex items-center justify-between gap-3">
              <span className="text-[11px] text-muted">
                {loans.length} confirmed loan{loans.length > 1 ? "s" : ""} stored locally
              </span>
              {confirmClear ? (
                <span className="inline-flex items-center gap-2 text-[11px]">
                  <button
                    type="button"
                    onClick={() => {
                      onClearLoans();
                      setConfirmClear(false);
                    }}
                    className="font-bold text-danger hover:underline"
                  >
                    Confirm clear
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmClear(false)}
                    className="font-medium text-muted hover:text-ink"
                  >
                    Cancel
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  onClick={() => setConfirmClear(true)}
                  className="inline-flex items-center gap-1 text-[11px] font-semibold text-danger transition hover:underline"
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                  Clear history
                </button>
              )}
            </div>
          ) : null}
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-11 w-full items-center justify-center rounded-full bg-ink text-xs font-bold text-white transition hover:bg-ink-soft"
          >
            Close
          </button>
        </div>
      </aside>
    </div>,
    document.body,
  );
}
