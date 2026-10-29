import { NextResponse } from "next/server";
import {
  APPLICATION_TERMS,
  MAX_LOAN_UGX,
  MIN_LOAN_UGX,
  feeForAmount,
  formatPhone,
  generateReference,
  getPackage,
  isTermMonths,
  type TermMonths,
} from "@/lib/loans";
import { clientKey, rateLimit } from "@/lib/rate-limit";
import { createDemoPayment } from "@/lib/demo-payments";
import { recordAttempt, type LedgerEntry } from "@/lib/ledger";
import {
  PaymentProviderError,
  getOptimaConfig,
  initiateStkPush,
  ugxToKes,
} from "@/lib/optima";

const STK_LIMIT = 8;
const STK_WINDOW_MS = 5 * 60 * 1_000;

function fail(message: string, status = 400, extra: Record<string, unknown> = {}) {
  return NextResponse.json(
    { success: false, error: message, ...extra },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

/** Analytics is best-effort: a ledger write must never fail a payment. */
async function logAttempt(entry: LedgerEntry) {
  try {
    await recordAttempt(entry);
  } catch (error) {
    console.error("ledger: could not record an attempt", error);
  }
}

export async function POST(request: Request) {
  const limit = rateLimit(`stk:${clientKey(request)}`, STK_LIMIT, STK_WINDOW_MS);
  if (!limit.ok) {
    return NextResponse.json(
      {
        success: false,
        error: `Too many attempts. Try again in ${limit.retryAfterSeconds} seconds.`,
      },
      {
        status: 429,
        headers: {
          "Cache-Control": "no-store",
          "Retry-After": String(limit.retryAfterSeconds),
        },
      },
    );
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return fail("Send a valid JSON body.");
  }

  if (!body || typeof body !== "object") return fail("Send a valid JSON body.");
  const input = body as Record<string, unknown>;

  const months = Number(input.months);
  if (!isTermMonths(months)) {
    return fail(`Choose a repayment term of ${APPLICATION_TERMS.join(", ")} months.`, 422);
  }

  let principal: number;
  if (typeof input.packageId === "string" && input.packageId) {
    const pkg = getPackage(input.packageId);
    if (!pkg) return fail("Unknown loan package.", 422);
    principal = pkg.amount;
  } else {
    principal = Number(input.amount);
  }

  if (!Number.isInteger(principal) || principal < MIN_LOAN_UGX || principal > MAX_LOAN_UGX) {
    return fail("Loan amount is outside the supported range.", 422);
  }

  const rawPhone = typeof input.phone === "string" ? input.phone : "";
  const phoneDigits = rawPhone.replace(/\D/g, "");
  if (phoneDigits.length < 9 || phoneDigits.length > 15) {
    return fail("Enter a valid mobile money number.", 422);
  }

  const config = getOptimaConfig();
  const feeUgx = feeForAmount(principal);
  const feeKes = ugxToKes(feeUgx);
  const reference = generateReference();
  const customerName =
    typeof input.fullName === "string" ? input.fullName.trim().slice(0, 80) : "";

  try {
    if (config.mode === "demo") {
      const demo = createDemoPayment({ reference, phone: phoneDigits, amountKes: feeKes });
      const now = new Date().toISOString();
      await logAttempt({
        transactionId: demo.id,
        reference,
        customerName: customerName || null,
        phone: formatPhone(phoneDigits),
        principalUgx: principal,
        feeUgx,
        feeKes,
        months: months as TermMonths,
        mode: "demo",
        status: "pending",
        receipt: null,
        resultDesc: null,
        createdAt: now,
        updatedAt: now,
      });
      return NextResponse.json(
        {
          success: true,
          mode: "demo",
          reference,
          principal,
          months: months as TermMonths,
          feeUgx,
          feeKes,
          transactionId: demo.id,
          settlesAt: demo.settlesAt,
          phone: formatPhone(phoneDigits),
        },
        { headers: { "Cache-Control": "no-store" } },
      );
    }

    const push = await initiateStkPush({
      phone: rawPhone,
      amountKes: feeKes,
      reference,
    });

    const now = new Date().toISOString();
    await logAttempt({
      transactionId: push.transactionId,
      reference,
      customerName: customerName || null,
      phone: formatPhone(rawPhone),
      principalUgx: principal,
      feeUgx,
      feeKes,
      months: months as TermMonths,
      mode: "live",
      status: "pending",
      receipt: null,
      resultDesc: null,
      createdAt: now,
      updatedAt: now,
    });

    return NextResponse.json(
      {
        success: true,
        mode: "live",
        reference,
        principal,
        months: months as TermMonths,
        feeUgx,
        feeKes,
        transactionId: push.transactionId,
        checkoutRequestId: push.checkoutRequestId,
        phone: formatPhone(rawPhone),
      },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    if (error instanceof PaymentProviderError) {
      return fail(error.message, error.status && error.status < 500 ? 422 : 502);
    }
    return fail("The payment could not be started. Please try again.", 502);
  }
}

export function GET() {
  return NextResponse.json(
    { success: false, error: "Method not allowed." },
    { status: 405, headers: { Allow: "POST", "Cache-Control": "no-store" } },
  );
}
