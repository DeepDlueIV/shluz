"use client";

/** Account area: session bootstrap, email-OTP login, personal API tokens.
 *  Reuses the guest session endpoint: an authenticated session is preserved. */

import { startGuestSession, type GuestSession } from "./gateway";

const JSON_HEADERS = { "Content-Type": "application/json", Accept: "application/json" };

export type { GuestSession };

export type TokenRow = {
  id: string;
  name: string;
  source: string;
  token_prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
};

async function readJson(response: Response): Promise<Record<string, unknown>> {
  try {
    return (await response.json()) as Record<string, unknown>;
  } catch {
    return {};
  }
}

async function ensureSession(signal?: AbortSignal): Promise<GuestSession> {
  return startGuestSession(signal);
}

async function requestOtp(
  csrf: string,
  email: string,
  turnstileToken: string,
  signal?: AbortSignal,
): Promise<{ challenge_id: string; expires_in: number; resend_after: number }> {
  const response = await fetch("/v1/web/auth/otp/request", {
    method: "POST",
    credentials: "include",
    headers: { ...JSON_HEADERS, "X-CSRF-Token": csrf },
    body: JSON.stringify({ email, turnstile_token: turnstileToken }),
    cache: "no-store",
    signal,
  });
  const body = await readJson(response);
  if (response.status !== 200) {
    throw new Error(JSON.stringify(body));
  }
  return {
    challenge_id: String(body["challenge_id"] ?? ""),
    expires_in: Number(body["expires_in"] ?? 600),
    resend_after: Number(body["resend_after"] ?? 60),
  };
}

async function verifyOtp(
  csrf: string,
  challengeId: string,
  code: string,
  signal?: AbortSignal,
): Promise<GuestSession & { challenge_consumed: true }> {
  const response = await fetch("/v1/web/auth/otp/verify", {
    method: "POST",
    credentials: "include",
    headers: { ...JSON_HEADERS, "X-CSRF-Token": csrf },
    body: JSON.stringify({ challenge_id: challengeId, code }),
    cache: "no-store",
    signal,
  });
  const body = await readJson(response);
  if (response.status !== 200) {
    throw new Error(JSON.stringify(body));
  }
  // После проверки выдаётся новая сессия с новым csrf.
  const session = await startGuestSession();
  return session as GuestSession & { challenge_consumed: true };
}

async function call(
  path: string,
  method: "GET" | "POST",
  csrf: string,
  payload?: unknown,
): Promise<Record<string, unknown>> {
  const response = await fetch(path, {
    method,
    credentials: "include",
    headers: { ...JSON_HEADERS, "X-CSRF-Token": csrf },
    body: payload === undefined ? undefined : JSON.stringify(payload),
    cache: "no-store",
  });
  const body = await readJson(response);
  if (response.status !== 200) {
    const error = body["error"];
    const message =
      typeof error === "object" && error !== null && "message" in error
        ? String((error as Record<string, unknown>)["message"])
        : "Request failed";
    throw new Error(message);
  }
  return body;
}

export async function listTokens(csrf: string): Promise<TokenRow[]> {
  const body = await call("/v1/web/tokens", "GET", csrf);
  return (body["data"] as TokenRow[]) ?? [];
}

export async function createToken(csrf: string, name: string): Promise<string> {
  const body = await call("/v1/web/tokens", "POST", csrf, { name });
  return String(body["raw_token"] ?? "");
}

export async function revokeToken(csrf: string, id: string): Promise<void> {
  await call("/v1/web/tokens/revoke", "POST", csrf, { id });
}

export async function mintAuthorizeCode(
  csrf: string,
  client: string,
  port: number,
): Promise<{ code: string; redirect: string; expires_in: number }> {
  const body = await call("/v1/web/authorize", "POST", csrf, { client, port });
  return {
    code: String(body["code"] ?? ""),
    redirect: String(body["redirect"] ?? ""),
    expires_in: Number(body["expires_in"] ?? 60),
  };
}

export { ensureSession, requestOtp, verifyOtp };
