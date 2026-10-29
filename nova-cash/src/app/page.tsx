import { connection } from "next/server";
import { NovaCashApp } from "@/components/nova-cash-app";
import { resolvePaymentMode } from "@/lib/optima";

export const instant = false;

export default async function Home() {
  await connection();
  return <NovaCashApp paymentMode={resolvePaymentMode()} />;
}
