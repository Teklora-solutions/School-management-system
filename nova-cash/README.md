# NovaCash — instant loans (Next.js)

A demonstration instant-loan web app: 12 loan packages, an interactive calculator,
a five-step application flow and a mobile money payment step wired to
[OptimaPay](https://global.optimapaybridge.co.ke/).

The UI follows the structure and interaction model of the reference site, with
its own brand (indigo + lime, "NovaCash" wordmark and spark mark).

> **Demo notice:** in sandbox mode no credit bureau is contacted, no eligibility
> check is performed and no money moves. Live mode only collects the one-time
> processing fee through OptimaPay; disbursing the loan itself is outside this
> project. Confirm licensing and consumer-credit requirements in your market
> before offering real loans.

## Getting started

```bash
cd nova-cash
npm install
cp .env.example .env.local   # sandbox works with no credentials
npm run dev                  # http://localhost:3000
```

Other scripts:

```bash
npm run lint        # ESLint
npx tsc --noEmit    # type check
npm test            # Vitest (66 tests)
npm run build       # production build
npm start           # serve the production build
```

## Payment modes

Mode is resolved on the server in `src/lib/optima.ts`.

| `OPTIMAPAY_MODE` | Behaviour |
| --- | --- |
| `demo` (default) | Local sandbox. A `demo_…` transaction is created in memory and settles after ~6 seconds with a `DEMO******` receipt. Ugandan numbers are accepted. |
| `live` | Real OptimaPay STK push. Requires the secret values below. Only Kenyan M-Pesa numbers (`+2547…` / `+2541…`) are accepted, because that is what the gateway collects in. |
| `auto` | Live when all credentials are present, demo otherwise. |

Missing credentials never break the app: an incomplete live configuration falls
back to sandbox and the UI labels it as such.

### Configuration

Set these in `.env.local` (never commit that file):

```dotenv
OPTIMAPAY_MODE=demo
OPTIMAPAY_BASE_URL=https://global.optimapaybridge.co.ke
OPTIMAPAY_API_KEY=
OPTIMAPAY_API_SECRET=
OPTIMAPAY_ACCOUNT_ID=
OPTIMAPAY_UGX_PER_KES=29
```

- `OPTIMAPAY_API_KEY` / `OPTIMAPAY_API_SECRET` — sent as `X-API-KEY` and
  `X-API-SECRET` headers; only read on the server.
- `OPTIMAPAY_ACCOUNT_ID` — the numeric payment account ID (Paybill/Till record)
  the STK push is billed to, shown in the OptimaPay dashboard under Payment
  Accounts. Without it, `live` mode deliberately stays in sandbox.
- `OPTIMAPAY_UGX_PER_KES` — conversion used to bill the UGX processing fee in
  KES (default `29`). Update it when the rate moves; it is a fixed assumption,
  not a live quote.

The browser never sees these values — all gateway calls happen in route
handlers under `src/app/api/payments/`.

## Loan model

- Amounts: UGX 650,000 – 5,000,000 in the 12 listed packages, or any custom
  amount from the calculator.
- Terms and flat interest on the principal: 3 mo → 5%, 6 mo → 10%, 9 mo → 15%,
  12 mo → 20%. The monthly figure is `total / months` (rounded).
- One-time processing fee: the exact fee listed on the package, otherwise
  `round(amount × 0.014416)`. It is collected **before** disbursement, is not
  deducted from the principal and is not part of the repayment total — this is
  stated on every card, in the calculator and again before payment.
- First repayment date is estimated at 30 days per month from the approval date.

## Payment flow

1. `POST /api/payments/stkpush` — validates the request, recomputes the fee on
   the server (client-submitted fees are ignored), returns a demo transaction or
   starts a live STK push.
2. `GET /api/payments/status?transactionId=…` — demo status from the in-memory
   store, live status from `GET /api/v2/stk-push/status/{transactionId}`.

Both endpoints are rate limited per client IP (8 pushes / 5 min, 120 status
checks / min) and send `Cache-Control: no-store`. Demo transactions expire after
30 minutes.

An application is only counted and stored once `/api/payments/status` reports
`completed` with a receipt. Closing the dialog at any earlier step discards the
draft, and `saveLoan` rejects any record without a receipt, so a loan can never
appear in My Loans or in the header count before its STK payment is confirmed.

Confirmed loans are also stored in `localStorage` (`novacash.loans.v1`, max 20)
for the visitor's own My Loans drawer. The National ID number is used for the
in-session form only and is never persisted.

## Admin dashboard

`/admin` is a server-rendered operations view over every attempt, not just the
current browser. It sits behind a login form at `/admin/login`, backed by
SQLite (`data/novacash.db`, `DATABASE_PATH` overrides the location):

```dotenv
ADMIN_USER=admin
ADMIN_PASSWORD=change-me
```

On the first request the app creates the `admin_users` and `admin_sessions`
tables and seeds one account from `ADMIN_USER`/`ADMIN_PASSWORD`. The password is
stored as a salted scrypt hash, and seeding only happens while the table is
empty — editing the env values later does not rotate an existing account. With
no `ADMIN_PASSWORD` nothing is seeded, and `/admin/login` shows setup
instructions instead of a form.

Sessions are random tokens in an HttpOnly `novacash_admin` cookie, kept hashed
in `admin_sessions` with a seven-day expiry; sign-in is limited to 10 attempts
per IP per 15 minutes. Delete `data/novacash.db` (or the row in `admin_users`)
to reseed from the env on the next request.

The page
shows fees collected (KES and UGX), confirmed loans and success rate, principal
approved, expected repayments and interest, the live/pending/failed/sandbox
pipeline, a seven-day fee chart, the loan mix by term and the last 12
applications with gateway receipts.

How the figures are produced:

- Every successful STK initiation writes a `pending` attempt to
  `data/ledger.json` (`LEDGER_PATH` overrides the location).
- Status checks update that attempt to `completed` or `failed` and store the
  M-Pesa receipt. A completed attempt never regresses.
- Sandbox runs are listed but excluded from fees, principal and repayments, so
  test payments can never inflate revenue.
- Ledger writes are best-effort: a failure is logged and never blocks a payment.

The store is a single JSON file with serialised, atomic writes — right for one
server process. Replace it with a shared database before running more than one
instance, and treat `data/ledger.json` as customer data (it holds names and
phone numbers).

## Project layout

```
src/
  app/
    page.tsx                     request-time entry, resolves payment mode
    admin/page.tsx               operations dashboard (session protected)
    admin/login/                 login form, server actions
    layout.tsx, globals.css      fonts, theme tokens, animations
    api/payments/stkpush/        collection request handler
    api/payments/status/         transaction status handler
  components/
    nova-cash-app.tsx            page-level state and composition
    site-header.tsx, hero.tsx, site-footer.tsx
    packages-section.tsx, package-card.tsx
    calculator-panel.tsx, calculator-banner.tsx
    application-modal.tsx        5-step application + payment flow
    my-loans-drawer.tsx          locally saved applications
    modal-shell.tsx              shared dialog a11y (focus trap, Esc, scroll lock)
    faq-section.tsx, brand.tsx
  lib/
    loans.ts                     packages, quotes, fees, validators, formatting
    optima.ts                    OptimaPay client and mode resolution
    demo-payments.ts             sandbox transaction store
    ledger.ts                    server-side attempt store (JSON file)
    db.ts                        SQLite admin users and sessions (seeded)
    admin-auth.ts                reads the session cookie
    stats.ts                     admin aggregations (sandbox excluded)
    storage.ts                   localStorage persistence
    rate-limit.ts                fixed-window limiter
```

Tests live next to the code (`src/lib/*.test.ts`,
`src/app/api/payments/payments.test.ts`).

## Limits worth knowing

- Sandbox state is in memory: restarting the dev server clears pending demo
  transactions (saved loans in the browser are unaffected).
- Live collection supports M-Pesa (Kenya) only; Ugandan MTN/Airtel numbers work
  in sandbox mode only.
- The admin ledger is a JSON file for one server process, and there is no review
  queue or disbursement integration — the payment step is the end of the flow.
- The admin login protects `/admin` only; the public site is open by design.
  Login rate limiting is in-process, so it resets on restart and is not shared
  between instances.
