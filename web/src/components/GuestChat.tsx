"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useRef, type FormEvent, type KeyboardEvent } from "react";
import { useGuestJourney } from "./GuestJourney";
import styles from "./GuestChat.module.css";
import presentation from "./Presentation.module.css";
import LanguageSwitcher from "./LanguageSwitcher";
import { useSitePreferences } from "./SitePreferences";
import { GatewayError, getGatewayMessage } from "../lib/gateway";

function Arrow({ back = false }: { back?: boolean }) {
  return <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" width="22" height="22" style={back ? { transform: "rotate(-90deg)" } : undefined}>
    <path d="M12 19V5m0 0-6 6m6-6 6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
  </svg>;
}

export default function GuestChat() {
  const { locale, t } = useSitePreferences();
  const phaseLabels = {
    preparing: t("Preparing chat…", "Подготавливаем разговор…"),
    verification: t("Complete the browser verification below if prompted.", "При необходимости завершите проверку браузера ниже."),
    connecting: t("Waiting for a response…", "Ждём начала ответа…"),
    streaming: t("NoClip is responding…", "NoClip отвечает…"),
  };
  const { chat, draft, setDraft, openPrompt, beginPending, stop, enabled, maxInputChars, remaining } = useGuestJourney();
  const problem = chat.problem ? getGatewayMessage(new GatewayError(chat.problem.code), locale) : null;
  const verification = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const busy = chat.phase in phaseLabels;
  const count = Array.from(draft).length;
  const canSubmit = draft.trim().length > 0 && !busy && count <= maxInputChars && remaining !== 0;

  useEffect(() => {
    if (verification.current) beginPending(verification.current);
    // Запрос принадлежит provider: cleanup этого effect не отменяет StrictMode replay.
  }, [chat.requestId, beginPending]);

  useEffect(() => {
    const field = textarea.current;
    if (!field) return;
    field.style.height = "auto";
    field.style.height = `${Math.min(field.scrollHeight, 176)}px`;
  }, [draft]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (canSubmit) openPrompt(draft);
  }

  function keydown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      if (canSubmit) event.currentTarget.form?.requestSubmit();
    }
  }

  return <div className={styles.shell}>
    <aside className={styles.rail} aria-label={t("Chat navigation", "Навигация чата")}>
      <Link href="/" className={styles.railLogo} aria-label={t("NoClip home", "NoClip — на главную")}>
        <Image src="/media/noclip-icon.png" alt="" width={32} height={32} />
      </Link>
      <button type="button" className={styles.railButton} aria-label={t("Write a prompt", "Написать вопрос")} title={t("Write a prompt", "Написать вопрос")} onClick={() => { textarea.current?.focus(); }}>
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden="true"><path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg>
      </button>
      <Link href="/" className={`${styles.railButton} ${styles.railBottom}`} aria-label={t("Back to home", "Вернуться на главную")} title={t("Home", "На главную")}><Arrow back /></Link>
    </aside>

    <main className={styles.main}>
      <header className={styles.header}>
        <Link href="/" className={styles.wordmark}>NoClip</Link>
        <div className={presentation.headerActions}><span className={styles.guestBadge}><span />{t("Guest", "Гость")}</span><LanguageSwitcher /></div>
      </header>

      <section className={styles.transcript} aria-label={t("Your conversation with NoClip", "Ваш разговор с NoClip")}>
        {chat.prompt ? <div className={styles.question}>
          <p className={styles.eyebrow}>{t("Your prompt", "Ваш вопрос")}</p>
          <h1>{chat.prompt}</h1>
        </div> : <div className={styles.welcome}>
          <Image className={styles.welcomeMark} src="/media/noclip-mark.png" alt="" width={52} height={44} />
          <h1>{t("Ask anything", "Спросите о чём угодно")}</h1>
        </div>}

        {chat.answer && <section className={styles.answerSection} aria-label={t("NoClip response", "Ответ NoClip")}>
          <div className={styles.answerByline}><Image className={styles.smallMark} src="/media/noclip-mark.png" alt="" width={24} height={20} />NoClip</div>
          <div className={styles.answer}>{chat.answer}</div>
        </section>}

        {busy && <div className={styles.loading} role="status">
          <span className={styles.loadingDots} aria-hidden="true"><i /><i /><i /></span>
          <span>{phaseLabels[chat.phase as keyof typeof phaseLabels]}</span>
        </div>}

        <div ref={verification} className={styles.verification} aria-label={t("Browser verification", "Проверка браузера")} />

        {problem && <div className={styles.notice} role="status">
          <span className={styles.noticeMark} aria-hidden="true">↗</span>
          <div><h2>{problem.title}</h2><p>{problem.message}</p></div>
        </div>}

        {chat.phase === "stopped" && <div className={styles.notice} role="status">
          <span className={styles.noticeMark} aria-hidden="true">Ⅱ</span>
          <div><h2>{t("Request stopped", "Запрос остановлен")}</h2><p>{t("Text remains in this tab. The request may have counted toward your quota; it will not be retried automatically.", "Текст сохранён в этой вкладке. Попытка могла быть использована; автоматической повторной отправки не будет.")}</p></div>
        </div>}

        {chat.phase === "complete" && <p className={styles.complete} role="status">{t("Response complete", "Ответ завершён")}</p>}
        {chat.phase === "complete" && remaining === 0 && <div className={styles.notice} role="status">
          <span className={styles.noticeMark} aria-hidden="true">↗</span>
          <div><h2>{t("Demo limit reached", "Демо исчерпано")}</h2><p>{t("The demo quota is used up. A permanent account is required to continue.", "Лимит пробного доступа исчерпан. Для продолжения понадобится постоянный аккаунт.")}</p></div>
        </div>}
      </section>

      <div className={styles.composerDock}>
        <div className={styles.composerWidth}>
          <form onSubmit={submit} className={styles.composer} aria-label={t("New prompt", "Новый вопрос")}>
            <label htmlFor="guest-prompt" className={styles.srOnly}>{t("Your prompt", "Ваш вопрос")}</label>
            <textarea
              ref={textarea} id="guest-prompt" value={draft} rows={1}
              placeholder={t("Ask anything\u2026", "Спросите о чём угодно…")} readOnly={busy}
              onChange={(event) => setDraft(event.target.value)} onKeyDown={keydown}
              aria-describedby="guest-composer-help" aria-invalid={count > maxInputChars || undefined}
              autoComplete="off" spellCheck
            />
            {busy ? <button type="button" onClick={stop} className={styles.sendButton} aria-label={t("Stop response", "Остановить ответ")} title={t("Stop response", "Остановить ответ")}><span className={styles.stopIcon} /></button>
              : <button type="submit" disabled={!canSubmit} className={styles.sendButton} aria-label={enabled ? t("Send prompt", "Отправить вопрос") : t("Save prompt", "Сохранить вопрос")} title={enabled ? t("Send prompt", "Отправить вопрос") : t("Save prompt", "Сохранить вопрос")}><Arrow /></button>}
          </form>
          <div id="guest-composer-help" className={styles.composerHelp}>
            <span className={count > maxInputChars ? styles.overLimit : undefined}>{count > 0 ? `${count.toLocaleString(locale === "ru" ? "ru-RU" : "en-US")} / ${maxInputChars.toLocaleString(locale === "ru" ? "ru-RU" : "en-US")}` : t("One prompt \u2014 one response", "Один вопрос — один ответ")}</span>
            <span className={styles.keyboardHint}>{t("Enter to send · Shift + Enter for a new line", "Enter — отправить · Shift + Enter — новая строка")}</span>
          </div>
          {!enabled ? <p className={styles.availability}>{t("The demo is not connected. Text remains in this tab.", "Демонстрация временно недоступна. Текст остаётся в этой вкладке.")}</p>
            : remaining !== null && remaining > 0 ? <p className={styles.availability}>{t("Requests remaining:", "Осталось запросов:")} {remaining}. {t("No registration required.", "Без регистрации.")}</p>
            : <p className={styles.availability}>{t("Responses may contain errors. Verify important information.", "Ответ может содержать неточности. Важное стоит проверить.")}</p>}
        </div>
      </div>
    </main>
  </div>;
}
