"use client";

import { useCallback, useEffect, useState } from "react";
import type { LoanPackage, PaymentMode, SavedLoan } from "@/lib/loans";
import { clearLoans, loadLoans, saveLoan, LOANS_UPDATED_EVENT } from "@/lib/storage";
import { SiteHeader } from "./site-header";
import { Hero } from "./hero";
import { CalculatorPanel } from "./calculator-panel";
import { PackagesSection } from "./packages-section";
import { CalculatorBanner } from "./calculator-banner";
import { FaqSection } from "./faq-section";
import { SiteFooter } from "./site-footer";
import { MyLoansDrawer } from "./my-loans-drawer";
import { ApplicationModal } from "./application-modal";

interface ApplicationDraft {
  amount: number;
  packageId?: string;
}

export function NovaCashApp({ paymentMode }: { paymentMode: PaymentMode }) {
  const [loans, setLoans] = useState<SavedLoan[]>([]);
  const [loansOpen, setLoansOpen] = useState(false);
  const [calculatorOpen, setCalculatorOpen] = useState(false);
  const [application, setApplication] = useState<ApplicationDraft | null>(null);

  useEffect(() => {
    const refresh = () => setLoans(loadLoans());
    const initial = window.setTimeout(refresh, 0);
    window.addEventListener(LOANS_UPDATED_EVENT, refresh);
    return () => {
      window.clearTimeout(initial);
      window.removeEventListener(LOANS_UPDATED_EVENT, refresh);
    };
  }, []);

  useEffect(() => {
    if (!calculatorOpen) return;
    const timer = window.setTimeout(() => {
      document.getElementById("calculator")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 80);
    return () => window.clearTimeout(timer);
  }, [calculatorOpen]);

  const scrollToPackages = useCallback(() => {
    document.getElementById("packages")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  const openCalculator = useCallback(() => setCalculatorOpen(true), []);

  const selectPackage = useCallback((item: LoanPackage) => {
    setApplication({ amount: item.amount, packageId: item.id });
  }, []);

  const applyCustomAmount = useCallback((amount: number) => {
    setCalculatorOpen(false);
    setApplication({ amount });
  }, []);

  const handleApproved = useCallback((loan: SavedLoan) => {
    setLoans(saveLoan(loan));
  }, []);

  const handleClearLoans = useCallback(() => {
    clearLoans();
    setLoans([]);
  }, []);

  const applyNew = useCallback(() => {
    setLoansOpen(false);
    window.setTimeout(scrollToPackages, 60);
  }, [scrollToPackages]);

  return (
    <div className="flex flex-1 flex-col">
      <SiteHeader
        onApply={scrollToPackages}
        onOpenLoans={() => setLoansOpen(true)}
        onOpenCalculator={openCalculator}
        loanCount={loans.length}
      />

      <main className="flex-1">
        <Hero onApply={scrollToPackages} paymentMode={paymentMode} />

        {calculatorOpen ? (
          <CalculatorPanel
            onApply={applyCustomAmount}
            onClose={() => setCalculatorOpen(false)}
          />
        ) : null}

        <PackagesSection onApply={selectPackage} />

        {!calculatorOpen ? <CalculatorBanner onLaunch={openCalculator} /> : null}

        <FaqSection />
      </main>

      <SiteFooter
        onOpenLoans={() => setLoansOpen(true)}
        onOpenCalculator={openCalculator}
        paymentMode={paymentMode}
      />

      {loansOpen ? (
        <MyLoansDrawer
          open
          loans={loans}
          onClose={() => setLoansOpen(false)}
          onApplyNew={applyNew}
          onClearLoans={handleClearLoans}
        />
      ) : null}

      {application ? (
        <ApplicationModal
          open
          amount={application.amount}
          packageId={application.packageId}
          paymentMode={paymentMode}
          onClose={() => setApplication(null)}
          onApproved={handleApproved}
        />
      ) : null}
    </div>
  );
}
