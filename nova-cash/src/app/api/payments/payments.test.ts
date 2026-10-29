import { randomUUID } from "node:crypto";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { POST as initiatePayment } from "@/app/api/payments/stkpush/route";
import { GET as checkPayment } from "@/app/api/payments/status/route";
import { resetDemoPayments } from "@/lib/demo-payments";
import { readLedger } from "@/lib/ledger";

const ledgerPath = path.join(os.tmpdir(), `novacash-ledger-${process.pid}-${randomUUID()}.json`);

const ENV_KEYS = [
  "OPTIMAPAY_MODE",
  "OPTIMAPAY_BASE_URL",
  "OPTIMAPAY_API_KEY",
  "OPTIMAPAY_API_SECRET",
  "OPTIMAPAY_ACCOUNT_ID",
  "OPTIMAPAY_UGX_PER_KES",
];

function clearEnv() {
  for (const key of ENV_KEYS) delete process.env[key];
}

function setLiveEnv() {
  process.env.OPTIMAPAY_MODE = "live";
  process.env.OPTIMAPAY_BASE_URL = "https://gateway.test";
  process.env.OPTIMAPAY_API_KEY = "key_123";
  process.env.OPTIMAPAY_API_SECRET = "secret_456";
  process.env.OPTIMAPAY_ACCOUNT_ID = "42";
  process.env.OPTIMAPAY_UGX_PER_KES = "29";
}

let ipCounter = 0;
function nextIp() {
  ipCounter += 1;
  return `10.0.0.${ipCounter}`;
}

function push(body: unknown, ip = nextIp()) {
  return new Request("http://localhost/api/payments/stkpush", {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-forwarded-for": ip },
    body: JSON.stringify(body),
  });
}

function status(transactionId: string, ip = nextIp()) {
  return new Request(
    `http://localhost/api/payments/status?transactionId=${encodeURIComponent(transactionId)}`,
    { headers: { "x-forwarded-for": ip } },
  );
}

beforeEach(async () => {
  clearEnv();
  process.env.OPTIMAPAY_MODE = "demo";
  process.env.LEDGER_PATH = ledgerPath;
  await fs.rm(ledgerPath, { force: true });
  resetDemoPayments();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  clearEnv();
});

describe("POST /api/payments/stkpush", () => {
  it("starts a sandbox payment with a server-computed fee", async () => {
    const response = await initiatePayment(
      push({ amount: 650_000, phone: "0772 123 456", months: 6 }),
    );
    const data = await response.json();

    expect(response.status).toBe(200);
    expect(data).toMatchObject({
      success: true,
      mode: "demo",
      principal: 650_000,
      months: 6,
      feeUgx: 9_371,
      feeKes: 323,
    });
    expect(data.transactionId).toMatch(/^demo_[a-f0-9]{16}$/);
    expect(data.reference).toMatch(/^NVC-[A-HJ-NP-Z2-9]{7}$/);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
  });

  it("derives the principal from a package id instead of trusting the client", async () => {
    const response = await initiatePayment(
      push({ packageId: "pkg-3", amount: 100, phone: "0772 123 456", months: 3 }),
    );
    const data = await response.json();
    expect(data.principal).toBe(1_100_000);
    expect(data.feeUgx).toBe(15_850);
  });

  it("rejects out-of-range amounts, bad terms and short phone numbers", async () => {
    const tooSmall = await initiatePayment(
      push({ amount: 100, phone: "0772 123 456", months: 6 }),
    );
    expect(tooSmall.status).toBe(422);

    const badTerm = await initiatePayment(
      push({ amount: 650_000, phone: "0772 123 456", months: 5 }),
    );
    expect(badTerm.status).toBe(422);

    const badPhone = await initiatePayment(
      push({ amount: 650_000, phone: "123", months: 6 }),
    );
    expect(badPhone.status).toBe(422);
  });

  it("rejects an unknown package", async () => {
    const response = await initiatePayment(
      push({ packageId: "pkg-99", phone: "0772 123 456", months: 6 }),
    );
    expect(response.status).toBe(422);
  });

  it("rate limits repeated attempts from one address", async () => {
    const ip = "10.9.9.9";
    for (let index = 0; index < 8; index += 1) {
      const response = await initiatePayment(
        push({ amount: 650_000, phone: "0772 123 456", months: 3 }, ip),
      );
      expect(response.status).toBe(200);
    }

    const blocked = await initiatePayment(
      push({ amount: 650_000, phone: "0772 123 456", months: 3 }, ip),
    );
    expect(blocked.status).toBe(429);
    expect(blocked.headers.get("Retry-After")).toBeTruthy();
  });

  it("creates a live push through the gateway adapter", async () => {
    setLiveEnv();
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          success: true,
          data: { transaction_id: 142, checkout_request_id: "ws_CO_1" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await initiatePayment(
      push({ amount: 650_000, phone: "+254 712 345 678", months: 6 }),
    );
    const data = await response.json();

    expect(response.status).toBe(200);
    expect(data).toMatchObject({ mode: "live", transactionId: "142", feeKes: 323 });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const rows = await readLedger();
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      transactionId: "142",
      mode: "live",
      status: "pending",
      principalUgx: 650_000,
      feeKes: 323,
      customerName: null,
    });
  });

  it("refuses live collection from a non-M-Pesa number", async () => {
    setLiveEnv();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await initiatePayment(
      push({ amount: 650_000, phone: "0772 123 456", months: 6 }),
    );
    expect(response.status).toBe(422);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("GET /api/payments/status", () => {
  it("moves a sandbox payment from pending to completed", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-26T09:00:00.000Z"));

    const start = await initiatePayment(
      push({ amount: 650_000, phone: "0772 123 456", months: 6 }),
    );
    const session = await start.json();

    const pending = await checkPayment(status(session.transactionId));
    await expect(pending.json()).resolves.toMatchObject({
      success: true,
      mode: "demo",
      status: "pending",
    });

    vi.advanceTimersByTime(6_100);

    const completed = await checkPayment(status(session.transactionId));
    const result = await completed.json();
    expect(result).toMatchObject({ success: true, mode: "demo", status: "completed" });
    expect(result.receipt).toMatch(/^DEMO/);

    const [row] = await readLedger();
    expect(row).toMatchObject({
      transactionId: session.transactionId,
      mode: "demo",
      status: "completed",
    });
    expect(row.receipt).toMatch(/^DEMO/);
  });

  it("marks a stored live attempt completed when the gateway confirms it", async () => {
    setLiveEnv();
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          new Response(JSON.stringify({ success: true, data: { transaction_id: 142 } }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        )
        .mockResolvedValueOnce(
          new Response(
            JSON.stringify({
              success: true,
              data: {
                status: "completed",
                mpesa_receipt_number: "QGH789XYZ1",
                result_code: 0,
              },
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        ),
    );

    await initiatePayment(push({ amount: 650_000, phone: "+254 712 345 678", months: 6 }));
    await checkPayment(status("142"));

    const [row] = await readLedger();
    expect(row).toMatchObject({
      transactionId: "142",
      status: "completed",
      receipt: "QGH789XYZ1",
    });
  });

  it("returns 404 once a sandbox transaction expires from memory", async () => {
    const response = await checkPayment(status("demo_deadbeefdeadbeef"));
    expect(response.status).toBe(404);
  });

  it("proxies a live status check", async () => {
    setLiveEnv();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            success: true,
            data: {
              status: "completed",
              mpesa_receipt_number: "QGH789XYZ1",
              result_code: 0,
            },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    const response = await checkPayment(status("142"));
    await expect(response.json()).resolves.toMatchObject({
      success: true,
      mode: "live",
      status: "completed",
      receipt: "QGH789XYZ1",
    });
  });
});
