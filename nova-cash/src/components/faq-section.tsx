"use client";

import { useState } from "react";
import { ChevronDown, ReceiptText, ShieldCheck, Smartphone } from "lucide-react";
import { cn } from "@/lib/cn";

const FEATURES = [
  {
    title: "Mobile money first",
    copy: "Applications accept MTN MoMo and Airtel Money numbers, with M-Pesa for live fee collection.",
    icon: Smartphone,
  },
  {
    title: "Fees from UGX 9,371",
    copy: "Tiered processing fees are printed on every plan before you apply — no surprises.",
    icon: ReceiptText,
  },
  {
    title: "Private by default",
    copy: "Your National ID number is used only to validate this session. It is never saved in your browser.",
    icon: ShieldCheck,
  },
];

const QUESTIONS = [
  {
    question: "How fast is the decision and payout?",
    answer:
      "The eligibility preview takes a few seconds. Sandbox payments confirm automatically after about six seconds, while live mobile money prompts depend on your operator and how quickly you approve the prompt. Disbursement timing is confirmed in your offer before you pay.",
  },
  {
    question: "What do I need to apply?",
    answer:
      "Your full name, a mobile money number, your National ID number, and the wallet that should receive the funds. You must be 18 or older and the mobile money account must be registered in your name.",
  },
  {
    question: "How does the one-time processing fee work?",
    answer:
      "Every plan lists a processing fee. It is collected separately from the loan, before disbursement, and is not deducted from the principal. You will see the exact amount on the review screen and on the mobile money prompt before you enter your PIN.",
  },
  {
    question: "What terms and interest rates are available?",
    answer:
      "Choose 3, 6, 9 or 12 months. Interest is flat: 5%, 10%, 15% or 20% of the principal for those terms. Your total repayment is the principal plus flat interest — there is no compounding.",
  },
  {
    question: "How do I make my monthly repayments?",
    answer:
      "Use the reference shown in My Loans. On MTN MoMo or Airtel Money, open Payments → Goods & Services and enter the reference together with the merchant details provided by support.",
  },
  {
    question: "Can I repay early without penalties?",
    answer:
      "Yes. Contact support with your reference to settle early. Because interest is flat for the chosen term, confirm any adjustment with support before making an early payment.",
  },
];

export function FaqSection() {
  const [openIndex, setOpenIndex] = useState(0);

  return (
    <section id="faq" className="mx-auto w-full max-w-5xl scroll-mt-24 px-4 pb-16 pt-4 sm:px-6 sm:pb-20">
      <div className="text-center">
        <span className="text-[10px] font-bold uppercase tracking-[0.22em] text-brand">
          Clear terms, no surprises
        </span>
        <h2 className="mt-2 text-balance text-3xl font-extrabold tracking-[-0.035em] text-ink sm:text-4xl">
          Frequently asked questions
        </h2>
        <p className="mx-auto mt-3 max-w-xl text-sm leading-relaxed text-muted">
          Everything you need to know before applying for a NovaCash loan.
        </p>
      </div>

      <div className="mt-8 grid gap-3 sm:grid-cols-3">
        {FEATURES.map((feature) => (
          <div
            key={feature.title}
            className="rounded-card border border-line/80 bg-white/80 p-4 shadow-sm"
          >
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-brand-soft text-brand">
              <feature.icon className="h-4 w-4" aria-hidden="true" />
            </span>
            <h3 className="mt-3 text-sm font-extrabold text-ink">{feature.title}</h3>
            <p className="mt-1 text-[11px] leading-relaxed text-muted">{feature.copy}</p>
          </div>
        ))}
      </div>

      <div className="mt-6 overflow-hidden rounded-panel border border-line/80 bg-white shadow-card">
        {QUESTIONS.map((item, index) => {
          const open = openIndex === index;
          return (
            <div key={item.question} className="border-b border-line/70 last:border-b-0">
              <h3>
                <button
                  type="button"
                  onClick={() => setOpenIndex(open ? -1 : index)}
                  aria-expanded={open}
                  aria-controls={`faq-answer-${index}`}
                  className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left text-sm font-semibold text-ink transition hover:bg-mist/60 sm:px-6"
                >
                  {item.question}
                  <ChevronDown
                    className={cn(
                      "h-4 w-4 shrink-0 text-brand transition-transform duration-200",
                      open && "rotate-180",
                    )}
                    aria-hidden="true"
                  />
                </button>
              </h3>
              <div
                id={`faq-answer-${index}`}
                role="region"
                hidden={!open}
                className="px-5 pb-5 sm:px-6"
              >
                <p className="max-w-2xl text-xs leading-relaxed text-muted sm:text-[13px]">
                  {item.answer}
                </p>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
