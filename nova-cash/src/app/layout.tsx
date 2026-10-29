import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

export const metadata: Metadata = {
  title: "NovaCash — Instant Loans · Uganda",
  description:
    "Compare NovaCash loan amounts, see your monthly repayment instantly, and apply for mobile money funding from UGX 650,000 to UGX 5,000,000.",
  applicationName: "NovaCash",
  keywords: [
    "NovaCash",
    "instant loans Uganda",
    "mobile money loan",
    "UGX loan",
    "short term loan Kampala",
  ],
  openGraph: {
    title: "NovaCash — Instant Loans · Uganda",
    description:
      "Pick an amount, see your terms, and apply in minutes. Loan packages from UGX 650,000 to UGX 5,000,000.",
    type: "website",
  },
};

export const viewport: Viewport = {
  themeColor: "#4f46e5",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="flex min-h-dvh flex-col bg-mist text-ink antialiased">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[100] focus:rounded-full focus:bg-ink focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-white"
        >
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
