"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { createToken, ensureSession, mintCherryCode, verifyOtp, requestOtp } from "@/lib/account";

type Stage = "loading" | "login" | "consent" | "redirecting" | "done";

export default function AuthorizePage() {
  return (
    <Suspense fallback={<main className="mx-auto max-w-xl px-6 py-16 text-white">Loading…</main>}>
      <AuthorizeConsent />
    </Suspense>
  );
}

function AuthorizeConsent() {
  const params = useSearchParams();
  const clientName = params.get("client") === "cherry" ? "Cherry Studio" : (params.get("client") ?? "this application");
  const portRaw = params.get("port") ?? "";
  const port = Number(portRaw);
  const portValid = Number.isInteger(port) && port >= 1024 && port <= 65535;

  const [stage, setStage] = useState<Stage>("loading");
  const [csrf, setCsrf] = useState("");
  const [raw, setRaw] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [email, setEmail] = useState("");
  const [otpCode, setOtpCode] = useState("");
  const [otpSent, setOtpSent] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const session = await ensureSession();
        if (cancelled) return;
        setCsrf(session.csrf_token);
        setStage(session.authenticated ? "consent" : "login");
      } catch (cause) {
        if (!cancelled) {
          setError(String(cause instanceof Error ? cause.message : cause));
          setStage("login");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = async () => {
    setBusy(true);
    setError(null);
    try {
      if (!otpSent) {
        await requestOtp(email);
        setOtpSent(true);
      } else {
        const session = await verifyOtp(email, otpCode);
        setCsrf(session.csrf_token);
        setStage("consent");
      }
    } catch (cause) {
      setError(String(cause instanceof Error ? cause.message : cause));
    } finally {
      setBusy(false);
    }
  };

  const allowWithRedirect = async () => {
    setBusy(true);
    setError(null);
    try {
      const grant = await mintCherryCode(csrf, port);
      setStage("redirecting");
      window.location.href = grant.redirect;
    } catch (cause) {
      setError(String(cause instanceof Error ? cause.message : cause));
      setBusy(false);
    }
  };

  const allowWithShownToken = async () => {
    setBusy(true);
    setError(null);
    try {
      const token = await createToken(csrf, "Cherry Studio");
      setRaw(token);
      setStage("done");
    } catch (cause) {
      setError(String(cause instanceof Error ? cause.message : cause));
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    if (raw) {
      await navigator.clipboard.writeText(raw);
      setCopied(true);
    }
  };

  return (
    <main className="mx-auto max-w-xl px-6 py-16 text-white">
      <h1 className="text-2xl font-semibold">
        {clientName} requests access to your account
      </h1>
      <p className="mt-2 text-sm text-neutral-400">
        Approving creates a personal API token for {clientName}. The token acts on
        behalf of your account and can be revoked any time on the API tokens page.
      </p>

      {stage === "loading" && <p className="mt-8 text-neutral-400">Loading…</p>}

      {stage === "login" && (
        <section className="mt-8 space-y-3">
          <h2 className="text-lg font-medium">Sign in first</h2>
          {!otpSent ? (
            <div className="flex gap-2">
              <input
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="you@example.com"
                className="w-full rounded border border-neutral-700 bg-neutral-900 px-3 py-2"
              />
              <button
                onClick={login}
                disabled={busy || !email}
                className="rounded bg-white px-4 py-2 font-medium text-black disabled:opacity-40"
              >
                Send code
              </button>
            </div>
          ) : (
            <div className="flex gap-2">
              <input
                value={otpCode}
                onChange={(event) => setOtpCode(event.target.value)}
                placeholder="6-digit code from the email"
                className="w-full rounded border border-neutral-700 bg-neutral-900 px-3 py-2"
              />
              <button
                onClick={login}
                disabled={busy || !otpCode}
                className="rounded bg-white px-4 py-2 font-medium text-black disabled:opacity-40"
              >
                Verify
              </button>
            </div>
          )}
        </section>
      )}

      {stage === "consent" && (
        <section className="mt-8 space-y-4">
          {!portValid && (
            <p className="rounded border border-amber-700 bg-amber-950/40 px-3 py-2 text-sm text-amber-200">
              The application did not pass a valid local callback port, so use
              “show the token” below and paste it into {clientName}.
            </p>
          )}
          {portValid && (
            <button
              onClick={allowWithRedirect}
              disabled={busy}
              className="w-full rounded bg-white px-4 py-3 font-medium text-black disabled:opacity-40"
            >
              Allow and open {clientName}
            </button>
          )}
          <button
            onClick={allowWithShownToken}
            disabled={busy}
            className="w-full rounded border border-neutral-600 px-4 py-3 font-medium disabled:opacity-40"
          >
            Allow and show the token here
          </button>
        </section>
      )}

      {stage === "redirecting" && (
        <p className="mt-8 text-neutral-300">
          Approved. Returning you to {clientName}…
        </p>
      )}

      {stage === "done" && raw && (
        <section className="mt-8 rounded border border-amber-700 bg-amber-950/40 p-4">
          <h2 className="font-medium text-amber-200">
            Approved — paste this token into {clientName}
          </h2>
          <code className="mt-2 block break-all rounded bg-black/60 p-3 text-sm">
            {raw}
          </code>
          <button
            onClick={copy}
            className="mt-2 rounded bg-amber-200 px-3 py-1.5 text-sm font-medium text-black"
          >
            {copied ? "Copied" : "Copy token"}
          </button>
        </section>
      )}

      {error && (
        <p className="mt-6 rounded border border-red-800 bg-red-950 px-3 py-2 text-sm text-red-300">
          {error}
        </p>
      )}
    </main>
  );
}
