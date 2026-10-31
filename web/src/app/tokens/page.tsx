"use client";

import { useCallback, useEffect, useState } from "react";
import {
  createToken,
  ensureSession,
  listTokens,
  requestOtp,
  revokeToken,
  verifyOtp,
  type SessionInfo,
  type TokenRow,
} from "@/lib/account";

type Stage = "loading" | "login" | "ready";

export default function TokensPage() {
  const [stage, setStage] = useState<Stage>("loading");
  const [csrf, setCsrf] = useState("");
  const [tokens, setTokens] = useState<TokenRow[]>([]);
  const [name, setName] = useState("");
  const [rawShown, setRawShown] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [email, setEmail] = useState("");
  const [otpCode, setOtpCode] = useState("");
  const [otpSent, setOtpSent] = useState(false);

  const refresh = useCallback(
    async (activeCsrf: string) => {
      const rows = await listTokens(activeCsrf);
      setTokens(rows);
    },
    [],
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const session = await ensureSession();
        if (cancelled) return;
        if (!session.authenticated) {
          setStage("login");
          return;
        }
        setCsrf(session.csrf_token);
        await refresh(session.csrf_token);
        setStage("ready");
      } catch (cause) {
        if (!cancelled) setError(String(cause instanceof Error ? cause.message : cause));
        if (!cancelled) setStage("login");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

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
        await refresh(session.csrf_token);
        setStage("ready");
      }
    } catch (cause) {
      setError(String(cause instanceof Error ? cause.message : cause));
    } finally {
      setBusy(false);
    }
  };

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const raw = await createToken(csrf, name.trim() || "API token");
      setRawShown(raw);
      setName("");
      await refresh(csrf);
    } catch (cause) {
      setError(String(cause instanceof Error ? cause.message : cause));
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (id: string) => {
    setBusy(true);
    setError(null);
    try {
      await revokeToken(csrf, id);
      await refresh(csrf);
    } catch (cause) {
      setError(String(cause instanceof Error ? cause.message : cause));
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    if (rawShown) {
      await navigator.clipboard.writeText(rawShown);
      setCopied(true);
    }
  };

  return (
    <main className="mx-auto max-w-2xl px-6 py-16 text-white">
      <h1 className="text-2xl font-semibold">API tokens</h1>
      <p className="mt-2 text-sm text-neutral-400">
        Personal tokens for desktop apps and OpenAI-compatible clients. A token is
        shown in full only once, right after creation.
      </p>

      {stage === "loading" && <p className="mt-8 text-neutral-400">Loading…</p>}

      {stage === "login" && (
        <section className="mt-8 space-y-3">
          <h2 className="text-lg font-medium">Sign in</h2>
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

      {stage === "ready" && (
        <>
          {error && (
            <p className="mt-4 rounded border border-red-800 bg-red-950 px-3 py-2 text-sm text-red-300">
              {error}
            </p>
          )}

          {rawShown && (
            <section className="mt-6 rounded border border-amber-700 bg-amber-950/40 p-4">
              <h2 className="font-medium text-amber-200">
                Your new token — copy it now, it is shown only once
              </h2>
              <code className="mt-2 block break-all rounded bg-black/60 p-3 text-sm">
                {rawShown}
              </code>
              <button
                onClick={copy}
                className="mt-2 rounded bg-amber-200 px-3 py-1.5 text-sm font-medium text-black"
              >
                {copied ? "Copied" : "Copy token"}
              </button>
            </section>
          )}

          <section className="mt-8">
            <h2 className="text-lg font-medium">Create a token</h2>
            <div className="mt-3 flex gap-2">
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Token name, e.g. Cherry Studio"
                maxLength={100}
                className="w-full rounded border border-neutral-700 bg-neutral-900 px-3 py-2"
              />
              <button
                onClick={create}
                disabled={busy}
                className="rounded bg-white px-4 py-2 font-medium text-black disabled:opacity-40"
              >
                Create
              </button>
            </div>
          </section>

          <section className="mt-8">
            <h2 className="text-lg font-medium">Your tokens</h2>
            {tokens.length === 0 && (
              <p className="mt-2 text-sm text-neutral-400">No tokens yet.</p>
            )}
            <ul className="mt-3 space-y-2">
              {tokens.map((token) => (
                <li
                  key={token.id}
                  className="flex items-center justify-between rounded border border-neutral-800 bg-neutral-900/60 px-4 py-3"
                >
                  <div>
                    <p className="font-medium">
                      {token.name}{" "}
                      <span className="ml-1 rounded bg-neutral-800 px-1.5 py-0.5 text-xs text-neutral-300">
                        {token.token_prefix}…
                      </span>
                      {token.revoked_at && (
                        <span className="ml-1 text-xs text-red-400">revoked</span>
                      )}
                    </p>
                    <p className="text-xs text-neutral-500">
                      created {new Date(token.created_at).toLocaleString()}
                      {token.last_used_at
                        ? ` · last used ${new Date(token.last_used_at).toLocaleString()}`
                        : " · never used"}
                    </p>
                  </div>
                  {!token.revoked_at && (
                    <button
                      onClick={() => revoke(token.id)}
                      disabled={busy}
                      className="rounded border border-red-800 px-3 py-1.5 text-sm text-red-300 disabled:opacity-40"
                    >
                      Revoke
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </main>
  );
}
