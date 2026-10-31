"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useSitePreferences } from "../../components/SitePreferences";
import { useAccountGate } from "../../components/AccountGate";
import {
  createToken,
  ensureSession,
  listTokens,
  revokeToken,
  type TokenRow,
} from "../../lib/account";
import styles from "../../components/Account.module.css";

function TokensInner() {
  const { t } = useSitePreferences();
  const router = useRouter();
  const gate = useAccountGate();

  useEffect(() => {
    if (gate.stage === "login") {
      router.replace(`/login?next=${encodeURIComponent("/tokens")}`);
    }
  }, [gate.stage, router]);

  const [tokens, setTokens] = useState<TokenRow[] | null>(null);
  const [name, setName] = useState("");
  const [raw, setRaw] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (gate.stage !== "ready") return;
    let cancelled = false;
    (async () => {
      try {
        const rows = await listTokens(gate.csrf);
        if (!cancelled) setTokens(rows);
      } catch (cause) {
        if (!cancelled) setError(String(cause instanceof Error ? cause.message : cause));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [gate.stage, gate.csrf]);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const raw = await createToken(gate.csrf, name.trim() || t("API token", "API-токен"));
      setRaw(raw);
      setName("");
      setTokens(await listTokens(gate.csrf));
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
      await revokeToken(gate.csrf, id);
      setTokens(await listTokens(gate.csrf));
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
    <>
      {gate.stage === "loading" && (
        <p className={styles.loading}>{t("Loading…", "Загрузка…")}</p>
      )}
      {gate.errorNode}

      {gate.stage === "ready" && (
        <>
          {raw && (
            <section className={styles.secret}>
              <h2 className={styles.secretTitle}>
                {t(
                  "Your new token — copy it now, it is shown only once",
                  "Ваш новый токен — скопируйте сейчас, он показывается один раз",
                )}
              </h2>
              <code className={styles.code}>{raw}</code>
              <button type="button" className={styles.copy} onClick={copy}>
                {copied ? t("Copied", "Скопировано") : t("Copy token", "Скопировать токен")}
              </button>
            </section>
          )}

          <section className={styles.section}>
            <h2 className={styles.sectionTitle}>
              {t("Create a token", "Создать токен")}
            </h2>
            <div className={styles.formRow}>
              <input
                className={styles.input}
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder={t("Token name, e.g. NoClip Desktop", "Название токена, например NoClip Desktop")}
                maxLength={100}
              />
              <button
                type="button"
                className={styles.primary}
                disabled={busy}
                onClick={create}
              >
                {t("Create", "Создать")}
              </button>
            </div>
          </section>

          <section className={styles.section}>
            <h2 className={styles.sectionTitle}>
              {t("Your tokens", "Ваши токены")}
            </h2>
            {tokens !== null && tokens.length === 0 && (
              <p className={styles.empty}>{t("No tokens yet.", "Токенов пока нет.")}</p>
            )}
            <ul className={styles.list}>
              {(tokens ?? []).map((token) => (
                <li key={token.id} className={styles.card}>
                  <div className={styles.row}>
                    <div>
                      <p className={styles.name}>
                        {token.name}{" "}
                        <span className={styles.prefix}>{token.token_prefix}…</span>
                        {token.revoked_at && (
                          <span className={styles.revoked}>
                            {" "}
                            {t("revoked", "отозван")}
                          </span>
                        )}
                      </p>
                      <p className={styles.meta}>
                        {t("created", "создан")}{" "}
                        {new Date(token.created_at).toLocaleString()}
                        {token.last_used_at
                          ? ` · ${t("last used", "использован")} ${new Date(token.last_used_at).toLocaleString()}`
                          : ` · ${t("never used", "не использовался")}`}
                      </p>
                    </div>
                    {!token.revoked_at && (
                      <button
                        type="button"
                        className={`${styles.ghost} ${styles.danger}`}
                        disabled={busy}
                        onClick={() => revoke(token.id)}
                      >
                        {t("Revoke", "Отозвать")}
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </>
  );
}

export default function TokensPage() {
  const { t } = useSitePreferences();
  return (
    <main className={styles.main}>
      <header className={styles.header}>
        <Link href="/" className={styles.wordmark}>NoClip</Link>
        <Link href="/app" className={styles.home}>
          {t("Back to chat", "Вернуться к чату")}
        </Link>
      </header>
      <h1 className={styles.title}>{t("API tokens", "API-токены")}</h1>
      <p className={styles.subtitle}>
        {t(
          "Personal tokens for desktop apps and OpenAI-compatible clients. A token is shown in full only once, right after creation.",
          "Личные токены для настольных приложений и OpenAI-совместимых клиентов. Полное значение показывается один раз — сразу после создания.",
        )}
      </p>
      <TokensInner />
    </main>
  );
}
