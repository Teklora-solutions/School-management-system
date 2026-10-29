import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { connection } from "next/server";
import { KeyRound, ShieldAlert } from "lucide-react";
import { Logo } from "@/components/brand";
import { getAdminSession } from "@/lib/admin-auth";
import { hasAdminUser } from "@/lib/db";
import { login } from "./actions";

export const instant = false;

export const metadata: Metadata = {
  title: "NovaCash — Admin sign in",
  robots: { index: false, follow: false },
};

const MESSAGES: Record<string, string> = {
  invalid: "That username or password is incorrect.",
  rate: "Too many sign-in attempts. Try again in a few minutes.",
  missing: "Enter both your username and password.",
};

export default async function AdminLoginPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  await connection();
  const session = await getAdminSession();
  if (session) redirect("/admin");

  const params = await searchParams;
  const errorKey = typeof params.error === "string" ? params.error : undefined;
  const error = errorKey ? (MESSAGES[errorKey] ?? "Sign-in failed. Try again.") : null;
  const seeded = hasAdminUser();

  return (
    <main className="flex flex-1 items-center justify-center px-4 py-12">
      <div className="w-full max-w-md">
        <div className="flex justify-center">
          <Logo tagline />
        </div>

        <div className="mt-6 rounded-panel border border-line bg-white p-6 shadow-card sm:p-8">
          <p className="text-[11px] font-bold uppercase tracking-[0.22em] text-brand">
            Admin access
          </p>
          <h1 className="mt-1 text-2xl font-extrabold tracking-[-0.02em] text-ink">Sign in</h1>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            Loan book, payment pipeline and collections. Authorised staff only.
          </p>

          {seeded ? (
            <form action={login} className="mt-6 space-y-4">
              <div>
                <label
                  htmlFor="username"
                  className="block text-[11px] font-bold uppercase tracking-[0.12em] text-muted"
                >
                  Username
                </label>
                <input
                  id="username"
                  name="username"
                  type="text"
                  autoComplete="username"
                  required
                  className="mt-1.5 w-full rounded-xl border border-line bg-mist/60 px-3.5 py-2.5 text-sm text-ink outline-none transition focus:border-brand focus:bg-white"
                />
              </div>

              <div>
                <label
                  htmlFor="password"
                  className="block text-[11px] font-bold uppercase tracking-[0.12em] text-muted"
                >
                  Password
                </label>
                <input
                  id="password"
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  required
                  className="mt-1.5 w-full rounded-xl border border-line bg-mist/60 px-3.5 py-2.5 text-sm text-ink outline-none transition focus:border-brand focus:bg-white"
                />
              </div>

              {error ? (
                <p
                  role="alert"
                  className="flex items-start gap-2 rounded-xl border border-danger/20 bg-danger/5 px-3 py-2.5 text-xs leading-relaxed text-danger"
                >
                  <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  {error}
                </p>
              ) : null}

              <button
                type="submit"
                className="inline-flex w-full items-center justify-center gap-2 rounded-full bg-brand px-4 py-3 text-sm font-bold text-white shadow-[0_14px_30px_-16px_rgba(79,70,229,0.9)] transition hover:bg-brand-deep"
              >
                <KeyRound className="h-4 w-4" aria-hidden="true" />
                Sign in
              </button>
            </form>
          ) : (
            <div className="mt-6 rounded-2xl border border-dashed border-line bg-mist/60 p-4 text-xs leading-relaxed text-muted">
              <p className="font-semibold text-ink">No admin account is seeded yet.</p>
              <p className="mt-1.5">
                Set <code className="rounded bg-white px-1 py-0.5">ADMIN_USER</code> and{" "}
                <code className="rounded bg-white px-1 py-0.5">ADMIN_PASSWORD</code> in{" "}
                <code className="rounded bg-white px-1 py-0.5">.env.local</code>, then restart
                the server. The account is created in the database on first start.
              </p>
            </div>
          )}
        </div>

        <p className="mt-4 text-center text-xs text-muted">
          <Link href="/" className="font-semibold text-brand transition hover:text-brand-deep">
            Back to NovaCash
          </Link>
        </p>
      </div>
    </main>
  );
}
