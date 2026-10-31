"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useSitePreferences } from "../../components/SitePreferences";
import { OtpInput } from "../../components/OtpInput";
import { ensureSession, requestOtp, verifyOtp } from "../../lib/account";
import { verifyBrowser } from "../../components/GuestJourney";
import styles from "../../components/Account.module.css";

type Step = "email" | "code";

function LoginInner() {
  const { t } = useSitePreferences();
  const params = useSearchParams();
  const next = params.get("next") ?? "/app";
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [challengeId, setChallengeId] = useState("");
  const [completedCode, setCompletedCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [authenticated, setAuthenticated] = useState(false);
  const verification = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | null>(null);
  const csrfRef = useRef("");

  useEffect(() => {
    if (authenticated) {
      const timeout = window.setTimeout(() => window.location.assign(next), 700);
      return () => window.clearTimeout(timeout);
    }
  }, [authenticated, next]);

  const sendCode = async () => {
    setBusy(true);
    setError(null);
    const controller = new AbortController();
    abort.current = controller;
    try {
      const loaded = await ensureSession();
      csrfRef.current = loaded.csrf_token;
      if (!verification.current) {
        throw new Error(t("Verification container is missing", "Контейнер проверки не найден"));
      }
      const token = await verifyBrowser(
        loaded.captcha_sitekey,
        verification.current,
        controller.signal,
        "email_otp",
      );
      const granted = await requestOtp(loaded.csrf_token, email, token, controller.signal);
      setChallengeId(granted.challenge_id);
      setStep("code");
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(String(cause instanceof Error ? cause.message : cause));
      }
    } finally {
      setBusy(false);
    }
  };

  const confirmCode = async (code: string) => {
    setBusy(true);
    setError(null);
    try {
      await verifyOtp(csrfRef.current, challengeId, code);
      setAuthenticated(true);
    } catch (cause) {
      setError(String(cause instanceof Error ? cause.message : cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.cardShell}>
      <div className={styles.cardShellInner}>
        <img src="/media/noclip-mark.png" alt="" width={46} height={38} className={styles.cardMark} />
        <h1 className={styles.cardTitle}>
          {step === "code"
            ? t("Enter the code from your email", "Введите код из письма")
            : t("Sign in to NoClip", "Вход в NoClip")}
        </h1>

        {step === "email" && (
          <>
            <label className={styles.label} htmlFor="login-email">
              {t("Email address", "Электронная почта")}
            </label>
            <input
              id="login-email"
              type="email"
              className={styles.input}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder={t("Enter your email address", "Введите вашу почту")}
              autoComplete="email"
            />
            <div style={{ height: 14 }} />
            <button
              type="button"
              className={styles.primary}
              style={{ width: "100%" }}
              disabled={busy || !email}
              onClick={sendCode}
            >
              {t("Continue", "Далее")}
            </button>
            <div ref={verification} className={styles.verification} aria-label="Browser verification" />
          </>
        )}

        {step === "code" && (
          <>
            <OtpInput
              onComplete={(code) => {
                setCompletedCode(code);
                void confirmCode(code);
              }}
              disabled={busy}
            />
            <div className={styles.otpActions}>
              <button
                type="button"
                className={styles.primary}
                style={{ width: "100%" }}
                disabled={busy || completedCode.length !== 6}
                onClick={() => void confirmCode(completedCode)}
              >
                {t("Continue", "Продолжить")}
              </button>
              <button
                type="button"
                className={styles.ghost}
                style={{ width: "100%" }}
                disabled={busy}
                onClick={sendCode}
              >
                {t("Resend the code", "Отправить код снова")}
              </button>
            </div>
            <div ref={verification} className={styles.verification} aria-label="Browser verification" />
          </>
        )}

        {error && <div className={styles.error} style={{ marginTop: 16 }}>{error}</div>}
      </div>

      <div className={styles.altLinks}>
        <div>
          {t("No account yet?", "Ещё нет аккаунта?")}{" "}
          <Link href={`/sign-up?next=${encodeURIComponent(next)}`}>
            {t("Sign up here", "Зарегистрируйтесь здесь")}
          </Link>
        </div>
        <div>
          {t("Or try NoClip", "Или попробуйте NoClip")}{" "}
          <Link href="/app">{t("without an account", "без аккаунта")}</Link>
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  const { t } = useSitePreferences();
  return (
    <main className={styles.pageBackdrop} style={{ paddingTop: 48, paddingBottom: 48 }}>
      <div style={{ width: "min(440px, calc(100% - 40px))", margin: "0 auto 22px", display: "flex" }}>
        <Link href="/" className={styles.home}>
          {t("← Back to home", "← На главную")}
        </Link>
      </div>
      <Suspense fallback={<div className={styles.cardShell}><p className={styles.loading}>Loading…</p></div>}>
        <LoginInner />
      </Suspense>
    </main>
  );
}
