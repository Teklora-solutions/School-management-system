import { NextResponse } from "next/server";
import { clientKey, rateLimit } from "@/lib/rate-limit";
import { getDemoPaymentStatus, isDemoTransaction } from "@/lib/demo-payments";
import { updateAttempt, type AttemptUpdate } from "@/lib/ledger";
import { PaymentProviderError, fetchStkStatus } from "@/lib/optima";

function json(payload: Record<string, unknown>, status = 200) {
  return NextResponse.json(payload, {
    status,
    headers: { "Cache-Control": "no-store" },
  });
}

/** Analytics is best-effort: a ledger write must never fail a status check. */
async function logStatus(transactionId: string, update: AttemptUpdate) {
  try {
    await updateAttempt(transactionId, update);
  } catch (error) {
    console.error("ledger: could not update an attempt", error);
  }
}

export async function GET(request: Request) {
  const limit = rateLimit(`status:${clientKey(request)}`, 120, 60_000);
  if (!limit.ok) {
    return json(
      { success: false, error: "Too many status checks. Slow down." },
      429,
    );
  }

  const transactionId = new URL(request.url).searchParams.get("transactionId")?.trim() ?? "";
  if (!transactionId) {
    return json({ success: false, error: "A transactionId query parameter is required." }, 400);
  }

  if (isDemoTransaction(transactionId)) {
    const demo = getDemoPaymentStatus(transactionId);
    if (!demo) {
      return json(
        { success: false, error: "This sandbox transaction has expired. Start a new request." },
        404,
      );
    }
    await logStatus(transactionId, {
      status: demo.status,
      receipt: demo.receipt,
      resultDesc: demo.resultDesc,
    });

    return json({
      success: true,
      mode: "demo",
      status: demo.status,
      receipt: demo.receipt,
      resultCode: demo.resultCode,
      resultDesc: demo.resultDesc,
    });
  }

  try {
    const result = await fetchStkStatus(transactionId);
    await logStatus(transactionId, {
      status: result.status,
      receipt: result.receipt,
      resultDesc: result.resultDesc,
    });

    return json({
      success: true,
      mode: "live",
      status: result.status,
      receipt: result.receipt,
      resultCode: result.resultCode,
      resultDesc: result.resultDesc,
    });
  } catch (error) {
    if (error instanceof PaymentProviderError) {
      return json(
        { success: false, error: error.message },
        error.status && error.status < 500 ? 422 : 502,
      );
    }
    return json({ success: false, error: "Could not check the payment status." }, 502);
  }
}
