"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useSitePreferences } from "./SitePreferences";
import { verifyBrowser } from "./GuestJourney";
import {
  ensureSession,
  requestOtp,
  verifyOtp,
  type GuestSession,
} from "@/lib/account";
import styles from "./Account.module.css";

type Stage = "loading" | "login" | "ready";
type Translate = (en: string, ru: string) => string;

type Gate = {
  stage: Stage;
  csrf: string;
  errorNode: ReactNode;
  verificationNode: ReactNode;
  email: string;
  setEmail: (value: string) => void;
  otpCode: string;
  setOtpCode: (value: string) => void;
  otpSent: boolean;
  busy: boolean;
  sendCode: () => Promise<void>;
  confirm: () => Promise<void>;
};

/** Требует авторизованную сессию: иначе показывает вход по email-OTP.
 *  Детям передаётся csrf авторизованной сессии. */
export function useAccountGate(): Gate {
  const { t } = useSitePreferences();
  const [stage, setStage] = useState<Stage>("loading");
  const [csrf, setCsrf] = useState("");
  const [session, setSession] = useState<GuestSession | null>(null);
  const [email, setEmail] = useState("");
  const [otpCode, setOtpCode] = useState("");
  const [challengeId, setChallengeId] = useState("");
  const [otpSent, setOtpSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const verification = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const loaded = await ensureSession();
        if (cancelled) return;
        setSession(loaded);
        setCsrf(loaded.csrf_token);
        setStage(loaded.authenticated ? "ready" : "login");
      } catch (cause) {
        if (!cancelled) {
          setError(String(cause instanceof Error ? cause.message : cause));
          setStage("login");
        }
      }
    })();
    return () => {
      cancelled = true;
      abort.current?.abort();
    };
  }, []);

  const sendCode = useCallback(async () => {
    if (!session) return;
    setBusy(true);
    setError(null);
    const controller = new AbortController();
    abort.current = controller;
    try {
      if (!verification.current) {
        throw new Error(t("Verification container is missing", "Контейнер проверки не найден"));
      }
      const token = await verifyBrowser(
        session.captcha_sitekey,
        verification.current,
        controller.signal,
        "email_otp",
      );
      const granted = await requestOtp(csrf, email, token, controller.signal);
      setChallengeId(granted.challenge_id);
      setOtpSent(true);
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(String(cause instanceof Error ? cause.message : cause));
      }
    } finally {
      setBusy(false);
    }
  }, [csrf, email, session, t]);

  const confirm = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const session = await verifyOtp(csrf, challengeId, otpCode);
      setSession(session);
      setCsrf(session.csrf_token);
      setStage("ready");
    } catch (cause) {
      setError(String(cause instanceof Error ? cause.message : cause));
    } finally {
      setBusy(false);
    }
  }, [csrf, challengeId, otpCode]);

  const errorNode = error ? <div className={styles.error}>{error}</div> : null;
  const verificationNode = (
    <div ref={verification} className={styles.verification} aria-label="Browser verification" />
  );

  return {
    stage,
    csrf,
    errorNode,
    verificationNode,
    email,
    setEmail,
    otpCode,
    setOtpCode,
    otpSent,
    busy,
    sendCode,
    confirm,
  };
}

export function LoginForm({ gate }: { gate: Gate }) {
  const { t } = useSitePreferences();
  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>{t("Sign in", "Вход")}</h2>
      {!gate.otpSent ? (
        <div className={styles.formRow}>
          <input
            type="email"
            className={styles.input}
            value={gate.email}
            onChange={(event) => gate.setEmail(event.target.value)}
            placeholder="you@example.com"
            autoComplete="email"
          />
          <button
            type="button"
            className={styles.primary}
            disabled={gate.busy || !gate.email}
            onClick={() => void gate.sendCode()}
          >
            {t("Send code", "Отправить код")}
          </button>
        </div>
      ) : (
        <div className={styles.formRow}>
          <input
            className={styles.input}
            value={gate.otpCode}
            onChange={(event) => gate.setOtpCode(event.target.value)}
            placeholder={t("6-digit code from the email", "6-значный код из письма")}
            inputMode="numeric"
            autoComplete="one-time-code"
          />
          <button
            type="button"
            className={styles.primary}
            disabled={gate.busy || gate.otpCode.length !== 6}
            onClick={() => void gate.confirm()}
          >
            {t("Verify", "Подтвердить")}
          </button>
        </div>
      )}
      {gate.verificationNode}
      {gate.errorNode}
    </section>
  );
}
