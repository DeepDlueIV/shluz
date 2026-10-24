"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  GatewayError, getGatewayMessage, readGuestRemaining, startGuestSession, streamGuestCompletion,
  type GatewayMessage, type GuestSession,
} from "../lib/gateway";
import { createPendingPrompt, type PendingPrompt } from "../lib/pending-prompt";

const gatewayEnabled = process.env.NEXT_PUBLIC_GATEWAY_ENABLED === "true";

type Phase = "idle" | "preparing" | "verification" | "connecting" | "streaming" | "complete" | "stopped" | "error" | "unavailable" | "exhausted";
export type GuestChatState = {
  requestId: string | null;
  prompt: string;
  answer: string;
  phase: Phase;
  problem: GatewayMessage | null;
};

type GuestJourneyValue = {
  openPrompt(prompt: string): void;
  openChat(): void;
  beginPending(container: HTMLElement): void;
  stop(): void;
  draft: string;
  setDraft(value: string): void;
  chat: GuestChatState;
  enabled: boolean;
  maxInputChars: number;
  remaining: number | null;
};

const initialChat: GuestChatState = { requestId: null, prompt: "", answer: "", phase: "idle", problem: null };
const GuestJourneyContext = createContext<GuestJourneyValue | null>(null);
const activePhases: Phase[] = ["preparing", "verification", "connecting", "streaming"];
const isChatPath = (pathname: string | null) => pathname === "/app" || pathname === "/app/";

type TurnstileAPI = {
  render(container: HTMLElement, options: Record<string, unknown>): string | undefined;
  remove(widgetId: string): void;
};

declare global { interface Window { turnstile?: TurnstileAPI } }

let turnstileScript: Promise<TurnstileAPI> | null = null;

function loadTurnstile(): Promise<TurnstileAPI> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  if (turnstileScript) return turnstileScript;
  turnstileScript = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
    script.async = true;
    const timeout = window.setTimeout(fail, 15000);
    function fail() {
      window.clearTimeout(timeout);
      script.remove();
      turnstileScript = null;
      reject(new GatewayError("verification_unavailable"));
    }
    script.onerror = fail;
    script.onload = () => {
      const api = window.turnstile;
      if (!api) { fail(); return; }
      // Async api.js уже выполнен; ready() несовместим с async/defer.
      window.clearTimeout(timeout);
      resolve(api);
    };
    document.head.appendChild(script);
  });
  return turnstileScript;
}

async function verifyBrowser(sitekey: string, container: HTMLElement, signal: AbortSignal): Promise<string> {
  if (!sitekey) throw new GatewayError("web_not_configured");
  const api = await loadTurnstile();
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    let widgetId: string | undefined;
    let settled = false;
    const timeout = window.setTimeout(() => finish(new GatewayError("verification_failed")), 120000);
    const onAbort = () => finish(new DOMException("Aborted", "AbortError"));
    function finish(error?: Error, token?: string) {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeout);
      signal.removeEventListener("abort", onAbort);
      if (widgetId) api.remove(widgetId);
      if (error) reject(error);
      else if (token) resolve(token);
      else reject(new GatewayError("verification_failed"));
    }
    signal.addEventListener("abort", onAbort, { once: true });
    try {
      widgetId = api.render(container, {
        sitekey,
        action: "demo_chat",
        theme: "dark",
        size: "flexible",
        appearance: "interaction-only",
        retry: "never",
        "refresh-expired": "never",
        "response-field": false,
        callback: (token: string) => finish(undefined, token),
        "error-callback": () => { finish(new GatewayError("verification_failed")); return true; },
        "expired-callback": () => finish(new GatewayError("verification_failed")),
        "timeout-callback": () => finish(new GatewayError("verification_failed")),
        "unsupported-callback": () => finish(new GatewayError("verification_unavailable")),
      });
      if (!widgetId) finish(new GatewayError("verification_unavailable"));
    } catch { finish(new GatewayError("verification_unavailable")); }
  });
}

export function GuestJourneyProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [chat, setChat] = useState<GuestChatState>(initialChat);
  const [draft, setDraft] = useState("");
  const [session, setSession] = useState<GuestSession | null>(null);
  const [remaining, setRemaining] = useState<number | null>(null);
  const attempt = useRef<PendingPrompt | null>(null);
  const sessionRequest = useRef<Promise<GuestSession> | null>(null);
  const sessionController = useRef<AbortController | null>(null);
  const disposeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const disposed = useRef(false);

  const ensureSession = useCallback(() => {
    if (!sessionRequest.current) {
      sessionController.current = new AbortController();
      const request = startGuestSession(sessionController.current.signal).then((value) => {
        if (!disposed.current) setSession(value);
        return value;
      }).catch((error) => {
        // Новая ручная попытка может восстановить сессию после временного сбоя.
        // Сам запрос к модели автоматически не повторяется.
        if (sessionRequest.current === request) sessionRequest.current = null;
        throw error;
      });
      sessionRequest.current = request;
    }
    return sessionRequest.current;
  }, []);

  useEffect(() => {
    if (gatewayEnabled) void ensureSession().catch(() => {});
  }, [ensureSession]);

  useEffect(() => {
    if (disposeTimer.current) clearTimeout(disposeTimer.current);
    disposed.current = false;
    return () => {
      // StrictMode немедленно повторяет setup. Реальное размонтирование отменяет работу.
      disposeTimer.current = setTimeout(() => {
        disposed.current = true;
        attempt.current?.cancel();
        sessionController.current?.abort();
      }, 0);
    };
  }, []);

  const stop = useCallback(() => {
    attempt.current?.cancel();
    setChat((current) => activePhases.includes(current.phase) ? { ...current, phase: "stopped", problem: null } : current);
  }, []);

  useEffect(() => {
    if (!isChatPath(pathname)) stop();
  }, [pathname, stop]);

  const openChat = useCallback(() => { router.push("/app/"); }, [router]);

  const openPrompt = useCallback((value: string) => {
    const prompt = value.trim();
    if (!prompt) { router.push("/app/"); return; }
    attempt.current?.cancel();
    const next = createPendingPrompt(prompt, crypto.randomUUID());
    attempt.current = next;
    setDraft(prompt);
    setChat({
      requestId: next.requestId, prompt, answer: "",
      phase: gatewayEnabled ? "preparing" : "unavailable",
      problem: gatewayEnabled ? null : getGatewayMessage(new GatewayError("web_not_configured")),
    });
    // Только фиксированный путь. Текст и идентификаторы не попадают в URL или storage.
    router.push("/app/");
  }, [router]);

  const beginPending = useCallback((container: HTMLElement) => {
    const current = attempt.current;
    if (!current || !gatewayEnabled) return;
    const isCurrent = () => !disposed.current && attempt.current === current;
    const update = (patch: Partial<GuestChatState>) => {
      if (isCurrent()) setChat((value) => value.requestId === current.requestId ? { ...value, ...patch } : value);
    };
    void current.start(async (signal) => {
      let posted = false;
      try {
        const settings = await ensureSession();
        signal.throwIfAborted();
        if (Array.from(current.prompt).length > settings.demo.max_input_chars) throw new GatewayError("input_too_large");
        const allowance = await readGuestRemaining(signal);
        signal.throwIfAborted();
        if (isCurrent()) setRemaining(allowance);
        if (allowance === 0) throw new GatewayError("demo_exhausted");
        update({ phase: "verification" });
        const token = await verifyBrowser(settings.captcha_sitekey, container, signal);
        signal.throwIfAborted();
        update({ phase: "connecting" });
        if (isCurrent()) setRemaining(null);
        posted = true;
        await streamGuestCompletion({
          prompt: current.prompt,
          requestId: current.requestId,
          csrfToken: settings.csrf_token,
          turnstileToken: token,
          signal,
          onText: (delta) => {
            if (isCurrent()) setChat((value) => value.requestId === current.requestId
              ? { ...value, phase: "streaming", answer: value.answer + delta } : value);
          },
        });
        update({ phase: "complete" });
        if (isCurrent()) setDraft("");
      } catch (error) {
        if (signal.aborted) { update({ phase: "stopped", problem: null }); }
        else {
          const problem = getGatewayMessage(error);
          const phase = problem.code === "demo_exhausted" ? "exhausted"
            : ["web_not_configured", "invalid_response", "demo_closed"].includes(problem.code) ? "unavailable" : "error";
          update({ phase, problem });
          if (isCurrent() && problem.code === "demo_exhausted") setRemaining(0);
        }
      } finally {
        // Остаток берём у сервера. При отмене не показываем старый лимит как новый остаток.
        if (posted && !signal.aborted && isCurrent()) {
          try {
            const value = await readGuestRemaining(signal);
            if (isCurrent()) setRemaining(value);
          } catch { /* Ответ уже показан; неопределённый остаток остаётся скрыт. */ }
        }
      }
    }).catch(() => { /* Отмена до начала микрозадачи; повторного запуска нет. */ });
  }, [ensureSession]);

  return <GuestJourneyContext.Provider value={{
    openPrompt, openChat, beginPending, stop, draft, setDraft, chat,
    enabled: gatewayEnabled, maxInputChars: session?.demo.max_input_chars ?? 3000, remaining,
  }}>{children}</GuestJourneyContext.Provider>;
}

export function useGuestJourney(): GuestJourneyValue {
  const value = useContext(GuestJourneyContext);
  if (!value) throw new Error("useGuestJourney must be used inside GuestJourneyProvider");
  return value;
}
