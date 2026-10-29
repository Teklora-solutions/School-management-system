import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { connection } from "next/server";
import {
  BadgeCheck,
  Banknote,
  FlaskConical,
  Landmark,
  RefreshCw,
  ShieldAlert,
  TrendingUp,
} from "lucide-react";
import { Logo } from "@/components/brand";
import { getAdminSession } from "@/lib/admin-auth";
import { readLedger, type LedgerEntry } from "@/lib/ledger";
import { formatUgx } from "@/lib/loans";
import { resolvePaymentMode } from "@/lib/optima";
import { computeStats } from "@/lib/stats";
import { logout } from "./login/actions";

export const instant = false;

export const metadata: Metadata = {
  title: "NovaCash — Operations dashboard",
  robots: { index: false, follow: false },
};

const card = "rounded-card border border-line bg-white p-5 shadow-card";

function amount(value: number): string {
  return value.toLocaleString("en-US");
}

function StatusPill({ entry }: { entry: LedgerEntry }) {
  if (entry.mode === "demo") {
    return (
      <span className="inline-flex whitespace-nowrap rounded-full border border-line bg-mist px-2 py-1 text-[10px] font-bold uppercase tracking-wide text-muted">
        Sandbox · {entry.status}
      </span>
    );
  }
  if (entry.status === "completed") {
    return (
      <span className="inline-flex whitespace-nowrap rounded-full bg-success/10 px-2 py-1 text-[10px] font-bold uppercase tracking-wide text-success">
        Collected
      </span>
    );
  }
  if (entry.status === "failed") {
    return (
      <span className="inline-flex whitespace-nowrap rounded-full bg-danger/10 px-2 py-1 text-[10px] font-bold uppercase tracking-wide text-danger">
        Failed
      </span>
    );
  }
  return (
    <span className="inline-flex whitespace-nowrap rounded-full bg-brand-soft px-2 py-1 text-[10px] font-bold uppercase tracking-wide text-brand">
      Pending
    </span>
  );
}

const when = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "Africa/Kampala",
});

export default async function AdminPage() {
  await connection();

  const session = await getAdminSession();
  if (!session) redirect("/admin/login");

  const entries = await readLedger();
  const stats = computeStats(entries);
  const mode = resolvePaymentMode();
  const updated = new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Africa/Kampala",
  }).format(new Date());
  const maxDailyFees = Math.max(1, ...stats.last7.map((day) => day.feesKes));
  const hasDailyFees = stats.last7.some((day) => day.feesKes > 0);
  const finished = stats.confirmed + stats.failed;
  const recent = entries.slice(0, 12);

  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:py-12">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <Logo />
          <span className="rounded-full border border-line bg-white px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.16em] text-muted">
            Admin
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[11px] font-semibold ${
              mode === "live"
                ? "border-success/20 bg-success/10 text-success"
                : "border-line bg-white text-muted"
            }`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${mode === "live" ? "bg-success" : "bg-muted"}`}
              aria-hidden="true"
            />
            {mode === "live" ? "Live collections" : "Sandbox mode"}
          </span>
          <span className="hidden text-[11px] font-semibold text-muted sm:inline">
            {session.username}
          </span>
          <a
            href="/admin"
            className="inline-flex items-center gap-1.5 rounded-full border border-line bg-white px-3 py-1.5 text-[11px] font-semibold text-ink transition hover:border-brand/40 hover:text-brand"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
            Refresh
          </a>
          <Link
            href="/"
            className="inline-flex items-center rounded-full bg-ink px-3 py-1.5 text-[11px] font-semibold text-white transition hover:bg-ink-soft"
          >
            View site
          </Link>
          <form action={logout}>
            <button
              type="submit"
              className="inline-flex items-center rounded-full border border-line bg-white px-3 py-1.5 text-[11px] font-semibold text-muted transition hover:border-danger/30 hover:text-danger"
            >
              Sign out
            </button>
          </form>
        </div>
      </header>

      <div className="mt-9">
        <p className="text-[11px] font-bold uppercase tracking-[0.22em] text-brand">
          Operations dashboard
        </p>
        <h1 className="mt-1 text-2xl font-extrabold tracking-[-0.02em] text-ink sm:text-3xl">
          Loan book &amp; collections
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted">
          Every STK attempt, confirmed payment and fee collected across all visitors.
          Updated {updated} EAT.
        </p>
      </div>

      <section className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label="Key figures">
        <div className={card}>
          <div className="flex items-center justify-between">
            <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-muted">
              Fees collected
            </p>
            <Banknote className="h-4 w-4 text-brand" aria-hidden="true" />
          </div>
          <p className="mt-3 text-2xl font-extrabold tracking-tight text-ink">
            KES {amount(stats.feesCollectedKes)}
          </p>
          <p className="mt-1 text-[11px] text-muted">
            ≈ {formatUgx(stats.feesCollectedUgx)} · sandbox excluded
          </p>
        </div>

        <div className={card}>
          <div className="flex items-center justify-between">
            <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-muted">
              Confirmed loans
            </p>
            <BadgeCheck className="h-4 w-4 text-brand" aria-hidden="true" />
          </div>
          <p className="mt-3 text-2xl font-extrabold tracking-tight text-ink">
            {amount(stats.confirmed)}
          </p>
          <p className="mt-1 text-[11px] text-muted">
            {finished === 0 ? "No finished attempts yet" : `${stats.successRate}% of finished attempts`}{" "}
            · {stats.uniqueCustomers} customers
          </p>
        </div>

        <div className={card}>
          <div className="flex items-center justify-between">
            <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-muted">
              Principal approved
            </p>
            <Landmark className="h-4 w-4 text-brand" aria-hidden="true" />
          </div>
          <p className="mt-3 text-2xl font-extrabold tracking-tight text-ink">
            UGX {amount(stats.principalApprovedUgx)}
          </p>
          <p className="mt-1 text-[11px] text-muted">Only after a confirmed payment</p>
        </div>

        <div className={card}>
          <div className="flex items-center justify-between">
            <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-muted">
              Expected repayments
            </p>
            <TrendingUp className="h-4 w-4 text-brand" aria-hidden="true" />
          </div>
          <p className="mt-3 text-2xl font-extrabold tracking-tight text-ink">
            UGX {amount(stats.expectedRepaymentUgx)}
          </p>
          <p className="mt-1 text-[11px] text-muted">
            Includes {formatUgx(stats.interestUgx)} flat interest
          </p>
        </div>
      </section>

      <section className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4" aria-label="Payment pipeline">
        {[
          { label: "Live attempts", value: stats.liveAttempts },
          { label: "Pending prompts", value: stats.pending },
          { label: "Failed / cancelled", value: stats.failed },
          { label: "Sandbox runs", value: stats.sandboxAttempts },
        ].map((item) => (
          <div key={item.label} className="rounded-2xl border border-line bg-white/70 px-4 py-3">
            <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-muted">
              {item.label}
            </p>
            <p className="mt-1 text-lg font-extrabold text-ink">{amount(item.value)}</p>
          </div>
        ))}
      </section>

      <div className="mt-3 grid gap-3 lg:grid-cols-3">
        <section className={`${card} lg:col-span-2`} aria-label="Fees collected in the last seven days">
          <div className="flex flex-wrap items-end justify-between gap-2">
            <div>
              <h2 className="text-sm font-bold text-ink">Fees collected, last 7 days</h2>
              <p className="mt-0.5 text-[11px] text-muted">Live payments only · KES</p>
            </div>
            <p className="text-[11px] font-semibold text-muted">
              Today · {stats.today.confirmed} loans · KES {amount(stats.today.feesKes)}
            </p>
          </div>

          {hasDailyFees ? (
            <div className="mt-6 flex h-44 items-end gap-2 sm:gap-3">
              {stats.last7.map((day) => {
                const height =
                  day.feesKes === 0
                    ? 4
                    : Math.max(8, Math.round((day.feesKes / maxDailyFees) * 104));
                return (
                  <div
                    key={day.key}
                    className="flex min-w-0 flex-1 flex-col items-center justify-end gap-1.5"
                  >
                    <span className="h-4 text-[10px] font-semibold text-muted">
                      {day.feesKes > 0 ? amount(day.feesKes) : ""}
                    </span>
                    <div
                      className={`w-full rounded-t-lg ${day.feesKes > 0 ? "bg-brand" : "bg-line"}`}
                      style={{ height: `${height}px` }}
                      title={`${day.label}: KES ${amount(day.feesKes)} from ${day.confirmed} confirmed loans`}
                      aria-label={`${day.label}: KES ${amount(day.feesKes)} from ${day.confirmed} confirmed loans`}
                    />
                    <span className="truncate text-[10px] font-semibold text-muted">
                      {day.label}
                    </span>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="mt-6 flex h-44 items-center justify-center rounded-2xl border border-dashed border-line bg-mist/50 px-6 text-center">
              <p className="text-xs leading-relaxed text-muted">
                No live payments in the last 7 days yet. Confirmed fees will chart here as
                soon as a customer completes an M-Pesa prompt.
              </p>
            </div>
          )}
        </section>

        <section className={card} aria-label="Confirmed loans by term">
          <h2 className="text-sm font-bold text-ink">Loan mix</h2>
          <p className="mt-0.5 text-[11px] text-muted">Confirmed loans by repayment term</p>

          <ul className="mt-5 space-y-4">
            {stats.terms.map((term) => {
              const share =
                stats.confirmed === 0 ? 0 : Math.round((term.confirmed / stats.confirmed) * 100);
              return (
                <li key={term.months}>
                  <div className="flex items-baseline justify-between text-[11px]">
                    <span className="font-bold text-ink">{term.label}</span>
                    <span className="text-muted">
                      {term.confirmed} · {share}%
                    </span>
                  </div>
                  <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-brand-soft">
                    <div className="h-full rounded-full bg-brand" style={{ width: `${share}%` }} />
                  </div>
                  <p className="mt-1 text-[10px] text-muted">
                    {formatUgx(term.principalUgx)} principal · {formatUgx(term.expectedUgx)} due
                  </p>
                </li>
              );
            })}
          </ul>

          <div className="mt-5 rounded-2xl border border-line bg-mist/70 p-3">
            <p className="flex items-start gap-2 text-[10px] leading-relaxed text-muted">
              <FlaskConical className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand" aria-hidden="true" />
              {stats.sandboxConfirmed} sandbox payment{stats.sandboxConfirmed === 1 ? "" : "s"}{" "}
              completed. Test money is never counted as revenue.
            </p>
          </div>
        </section>
      </div>

      <section className={`${card} mt-3`} aria-label="Recent applications">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <h2 className="text-sm font-bold text-ink">Recent applications</h2>
            <p className="mt-0.5 text-[11px] text-muted">
              Newest first · {stats.attempts} recorded attempt{stats.attempts === 1 ? "" : "s"}
            </p>
          </div>
          <span className="inline-flex items-center gap-1.5 rounded-full border border-line px-2.5 py-1 text-[10px] font-semibold text-muted">
            <ShieldAlert className="h-3 w-3" aria-hidden="true" />
            Contains customer data
          </span>
        </div>

        {recent.length === 0 ? (
          <div className="mt-5 rounded-2xl border border-dashed border-line bg-mist/60 px-6 py-10 text-center">
            <p className="text-sm font-semibold text-ink">No applications recorded yet</p>
            <p className="mx-auto mt-1.5 max-w-md text-xs leading-relaxed text-muted">
              Attempts appear here as soon as a customer starts an STK push on the public
              site. Card figures stay at zero until a live payment is confirmed.
            </p>
          </div>
        ) : (
          <>
          <p className="mt-3 text-[10px] text-muted sm:hidden">
            Swipe the table sideways to see principal, fee, term and receipt.
          </p>
          <div className="mt-2 overflow-x-auto scroll-thin sm:mt-4">
            <table className="w-full min-w-[56rem] text-left text-xs">
              <thead>
                <tr className="border-b border-line text-[10px] font-bold uppercase tracking-[0.12em] text-muted">
                  <th scope="col" className="py-2 pr-4 font-bold">When</th>
                  <th scope="col" className="py-2 pr-4 font-bold">Reference</th>
                  <th scope="col" className="py-2 pr-4 font-bold">Customer</th>
                  <th scope="col" className="py-2 pr-4 font-bold">Phone</th>
                  <th scope="col" className="py-2 pr-4 text-right font-bold">Principal</th>
                  <th scope="col" className="py-2 pr-4 text-right font-bold">Fee (KES)</th>
                  <th scope="col" className="py-2 pr-4 font-bold">Term</th>
                  <th scope="col" className="py-2 pr-4 font-bold">Status</th>
                  <th scope="col" className="py-2 font-bold">Receipt</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((entry) => (
                  <tr key={entry.transactionId} className="border-b border-line/70 last:border-0">
                    <td className="whitespace-nowrap py-2.5 pr-4 text-muted">
                      {when.format(new Date(entry.createdAt))}
                    </td>
                    <td className="whitespace-nowrap py-2.5 pr-4 font-semibold text-ink">
                      {entry.reference}
                    </td>
                    <td className="whitespace-nowrap py-2.5 pr-4 text-ink">
                      {entry.customerName ?? "—"}
                    </td>
                    <td className="whitespace-nowrap py-2.5 pr-4 text-muted">{entry.phone}</td>
                    <td className="whitespace-nowrap py-2.5 pr-4 text-right font-semibold text-ink">
                      {amount(entry.principalUgx)}
                    </td>
                    <td className="whitespace-nowrap py-2.5 pr-4 text-right text-ink">
                      {amount(entry.feeKes)}
                    </td>
                    <td className="whitespace-nowrap py-2.5 pr-4 text-muted">{entry.months} mo</td>
                    <td className="whitespace-nowrap py-2.5 pr-4">
                      <StatusPill entry={entry} />
                    </td>
                    <td className="whitespace-nowrap py-2.5 font-mono text-[11px] text-muted">
                      {entry.receipt ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </>
        )}
      </section>

      <p className="mt-6 max-w-3xl text-[11px] leading-relaxed text-muted">
        Figures are read from <code className="rounded bg-white px-1 py-0.5">data/ledger.json</code>{" "}
        on this server, so they cover every visitor rather than one browser. Sandbox payments
        are excluded from fees, principal and repayments. Keep the admin password private and
        serve this site over HTTPS in production.
      </p>
    </main>
  );
}
