"use client";

import { Suspense, useEffect } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useSitePreferences } from "../../components/SitePreferences";
import { LoginForm, useAccountGate } from "../../components/AccountGate";
import styles from "../../components/Account.module.css";

function LoginInner() {
  const { t } = useSitePreferences();
  const params = useSearchParams();
  const next = params.get("next") ?? "/app";
  const gate = useAccountGate();

  useEffect(() => {
    if (gate.stage === "ready") window.location.assign(next);
  }, [gate.stage, next]);

  return (
    <>
      {gate.stage === "loading" && (
        <p className={styles.loading}>{t("Loading…", "Загрузка…")}</p>
      )}
      {gate.stage === "login" && <LoginForm gate={gate} />}
      {gate.stage === "ready" && (
        <p className={styles.loading}>
          {t("Signed in. Redirecting…", "Вход выполнен. Переходим…")}
        </p>
      )}
      <p className={styles.hint}>
        {t(
          "The code is valid for 10 minutes. Check the spam folder if it does not arrive.",
          "Код действует 10 минут. Если письмо не пришло — проверьте папку «Спам».",
        )}
      </p>
      <p className={styles.hint}>
        {next === "/app"
          ? t(
              "After sign-in you return to your conversation.",
              "После входа вы вернётесь к вашему разговору.",
            )
          : t(
              "After sign-in you return where you started.",
              "После входа вы вернётесь туда, где начали.",
            )}
      </p>
      <p className={styles.hint}>
        <Link href="/" className={styles.home}>
          {t("Cancel", "Отмена")}
        </Link>
      </p>
    </>
  );
}

export default function LoginPage() {
  const { t } = useSitePreferences();
  return (
    <main className={styles.main}>
      <header className={styles.header}>
        <Link href="/" className={styles.wordmark}>NoClip</Link>
        <Link href="/" className={styles.home}>{t("Home", "На главную")}</Link>
      </header>
      <h1 className={styles.title}>{t("Sign in to NoClip", "Вход в NoClip")}</h1>
      <p className={styles.subtitle}>
        {t(
          "A one-time code is sent to your email. A new address automatically creates a new account.",
          "Одноразовый код отправляется на вашу почту. Новый адрес — и аккаунт создаётся автоматически.",
        )}
      </p>
      <Suspense fallback={<p className={styles.loading}>Loading…</p>}>
        <LoginInner />
      </Suspense>
    </main>
  );
}
