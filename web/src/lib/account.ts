"use client";

/** Account area helpers: session bootstrap, OTP login, personal API tokens. */

export type SessionInfo = {
  csrf_token: string;
  authenticated: boolean;
};

export type TokenRow = {
  id: string;
  name: string;
  source: string;
  token_prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
};

const JSON_HEADERS = { "Content-Type": "application/json" };

async function readJson(response: Response): Promise<Record<string, unknown>> {
  try {
    return (await response.json()) as Record<string, unknown>;
  } catch {
    return {};
  }
}

export async function ensureSession(): Promise<SessionInfo> {
  const response = await fetch("/v1/web/session", {
    method: "POST",
    headers: JSON_HEADERS,
    credentials: "same-origin",
  });
  const body = await readJson(response);
  if (response.status !== 200) {
    throw new Error(String(body["error"] ?? "Session unavailable"));
  }
  return body as unknown as SessionInfo;
}

export async function requestOtp(email: string): Promise<void> {
  const response = await fetch("/v1/web/auth/otp/request", {
    method: "POST",
    headers: JSON_HEADERS,
    credentials: "same-origin",
    body: JSON.stringify({ email }),
  });
  const body = await readJson(response);
  if (response.status !== 200) {
    throw new Error(String(body["error"] ?? "Could not send the code"));
  }
}

export async function verifyOtp(email: string, code: string): Promise<SessionInfo> {
  const response = await fetch("/v1/web/auth/otp/verify", {
    method: "POST",
    headers: JSON_HEADERS,
    credentials: "same-origin",
    body: JSON.stringify({ email, code }),
  });
  const body = await readJson(response);
  if (response.status !== 200) {
    throw new Error(String(body["error"] ?? "Wrong or expired code"));
  }
  return body as unknown as SessionInfo;
}

async function call(
  path: string,
  method: "GET" | "POST",
  csrf: string,
  payload?: unknown,
): Promise<Record<string, unknown>> {
  const response = await fetch(path, {
    method,
    headers: { ...JSON_HEADERS, "X-CSRF-Token": csrf },
    credentials: "same-origin",
    body: payload === undefined ? undefined : JSON.stringify(payload),
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

export async function mintCherryCode(
  csrf: string,
  port: number,
): Promise<{ code: string; redirect: string; expires_in: number }> {
  const body = await call("/v1/web/authorize/cherry", "POST", csrf, { port });
  return {
    code: String(body["code"] ?? ""),
    redirect: String(body["redirect"] ?? ""),
    expires_in: Number(body["expires_in"] ?? 60),
  };
}
