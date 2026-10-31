"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useSitePreferences } from "../../components/SitePreferences";
import { useAccountGate } from "../../components/AccountGate";
import { createToken, ensureSession, mintCherryCode } from "../../lib/account";
import styles from "../../components/Account.module.css";

function AuthorizeInner() {
  const { t } = useSitePreferences();
  const router = useRouter();
  const params = useSearchParams();
  const gate = useAccountGate();
  const client = params.get("client") ?? "this application";
  const clientName = client === "cherry" ? "Cherry Studio" : client;
  const portRaw = params.get("port") ?? "";
  const port = Number(portRaw);
  const portValid = Number.isInteger(port) && port >= 1024 && port <= 65535;

  const [appName] = useState(clientName);
  const [raw, setRaw] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (gate.stage === "login") {
      const next = encodeURIComponent(`/authorize?client=${client}&port=${portRaw}`);
      router.replace(`/login?next=${next}`);
    }
  }, [gate.stage, client, portRaw, router]);

  const mint = async () => {
    setBusy(true);
    setError(null);
    try {
      await ensureSession();
      const grant = await mintCherryCode(gate.csrf, port);
      window.location.href = grant.redirect;
    } catch (cause) {
      setError(String(cause instanceof Error ? cause.message : cause));
      setBusy(false);
    }
  };

  const showToken = async () => {
    setBusy(true);
    setError(null);
    try {
      await ensureSession();
      const token = await createToken(gate.csrf, appName);
      setRaw(token);
      setBusy(false);
    } catch (cause) {
      setError(String(cause instanceof Error ? cause.message : cause));
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
    <>
      {gate.stage === "loading" && (
        <p className={styles.loading}>{t("Loading…", "Загрузка…")}</p>
      )}

      {gate.stage === "ready" && (
        <>
          {error && <div className={styles.error}>{error}</div>}
          {!raw && (
            <section className={styles.section}>
              <h2 className={styles.sectionTitle}>
                {t("Approve access", "Подтвердите доступ")}
              </h2>
              <p className={styles.subtitle}>
                {t(
                  `${appName} will receive a personal API token acting on behalf of your account. You can revoke it any time on the API tokens page.`,
                  `${appName} получит персональный API-токен от имени вашего аккаунта. Отозвать его можно в любой момент на странице API-токенов.`,
                )}
              </p>
              <div className={styles.formRow}>
                <button
                  type="button"
                  className={styles.primary}
                  disabled={busy}
                  onClick={portValid ? mint : showToken}
                >
                  {t("Approve", "Разрешить")}
                </button>
                {!portValid && (
                  <span className={styles.hint}>
                    {t(
                      "No valid local callback port was passed — the token will be shown here.",
                      "Приложение не передало корректный локальный порт — токен будет показан здесь.",
                    )}
                  </span>
                )}
              </div>
              {portValid && (
                <p className={styles.hint}>
                  {t(
                    "Or show the token here and paste it into the application manually.",
                    "Или покажите токен здесь и вставьте его в приложение вручную.",
                  )}
                  {" "}
                  <button type="button" className={styles.ghost} disabled={busy} onClick={showToken}>
                    {t("Show the token", "Показать токен")}
                  </button>
                </p>
              )}
            </section>
          )}

          {raw && (
            <section className={styles.secret}>
              <h2 className={styles.secretTitle}>
                {t(
                  "Approved — paste this token into the application",
                  "Доступ разрешён — вставьте токен в приложение",
                )}
              </h2>
              <code className={styles.code}>{raw}</code>
              <button type="button" className={styles.copy} onClick={copy}>
                {copied ? t("Copied", "Скопировано") : t("Copy token", "Скопировать токен")}
              </button>
              <p className={styles.hint}>
                {t(
                  "You can revoke this token any time on the API tokens page.",
                  "Отозвать этот токен можно в любой момент на странице API-токенов.",
                )}
              </p>
            </section>
          )}
        </>
      )}

      {gate.stage === "login" && (
        <p className={styles.loading}>
          {t("Sign-in required. Redirecting…", "Требуется вход. Перенаправляем…")}
        </p>
      )}
    </>
  );
}

export default function AuthorizePage() {
  const { t } = useSitePreferences();
  return (
    <main className={styles.main}>
      <header className={styles.header}>
        <Link href="/" className={styles.wordmark}>NoClip</Link>
        <Link href="/tokens" className={styles.home}>
          {t("API tokens", "API-токены")}
        </Link>
      </header>
      <h1 className={styles.title}>
        {t("Authorize a desktop application", "Авторизация настольного приложения")}
      </h1>
      <p className={styles.subtitle}>
        {t(
          "Confirm access for the application that sent you here. Nothing is shared except a personal API token you can revoke.",
          "Подтвердите доступ приложению, которое вас сюда направило. Кроме персонального токена, который можно отозвать, ничего не передаётся.",
        )}
      </p>
      <Suspense fallback={<p className={styles.loading}>Loading…</p>}>
        <AuthorizeInner />
      </Suspense>
    </main>
  );
}
