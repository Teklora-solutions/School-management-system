"use server";

import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import { clientKey, rateLimit } from "@/lib/rate-limit";
import {
  authenticate,
  createSession,
  deleteSession,
  SESSION_COOKIE,
  SESSION_TTL_MS,
} from "@/lib/db";

async function clientAddress(): Promise<string> {
  const requestHeaders = await headers();
  return (
    clientKey(new Request("http://admin.local", { headers: requestHeaders })) || "local"
  );
}

export async function login(formData: FormData) {
  const limit = rateLimit(`admin-login:${await clientAddress()}`, 10, 15 * 60_000);
  if (!limit.ok) redirect("/admin/login?error=rate");

  const username = String(formData.get("username") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  if (!username || !password) redirect("/admin/login?error=missing");

  const user = authenticate(username, password);
  if (!user) redirect("/admin/login?error=invalid");

  const token = createSession(user.id);
  const store = await cookies();
  store.set(SESSION_COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    secure: process.env.NODE_ENV === "production",
    maxAge: Math.floor(SESSION_TTL_MS / 1_000),
  });

  redirect("/admin");
}

export async function logout() {
  const store = await cookies();
  const token = store.get(SESSION_COOKIE)?.value;
  if (token) deleteSession(token);
  store.delete(SESSION_COOKIE);
  redirect("/admin/login");
}
