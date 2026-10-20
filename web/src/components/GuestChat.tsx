"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useRef, type FormEvent, type KeyboardEvent } from "react";
import { useGuestJourney } from "./GuestJourney";
import styles from "./GuestChat.module.css";

function Arrow({ back = false }: { back?: boolean }) {
  return <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" width="22" height="22" style={back ? { transform: "rotate(-90deg)" } : undefined}>
    <path d="M12 19V5m0 0-6 6m6-6 6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
  </svg>;
}

const phaseLabels = {
  preparing: "Подготавливаем разговор…",
  verification: "Проверяем браузер. Если появится проверка, завершите её ниже.",
  connecting: "Ждём начала ответа…",
  streaming: "NoClip отвечает…",
};

export default function GuestChat() {
  const { chat, draft, setDraft, openPrompt, beginPending, stop, enabled, maxInputChars, remaining } = useGuestJourney();
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
    <aside className={styles.rail} aria-label="Навигация чата">
      <Link href="/" className={styles.railLogo} aria-label="NoClip — на главную">
        <Image src="/media/noclip-icon.png" alt="" width={32} height={32} />
      </Link>
      <button type="button" className={styles.railButton} aria-label="Написать вопрос" title="Написать вопрос" onClick={() => { textarea.current?.focus(); }}>
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden="true"><path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg>
      </button>
      <Link href="/" className={`${styles.railButton} ${styles.railBottom}`} aria-label="Вернуться на главную" title="На главную"><Arrow back /></Link>
    </aside>

    <main className={styles.main}>
      <header className={styles.header}>
        <Link href="/" className={styles.wordmark}>NoClip</Link>
        <span className={styles.guestBadge}><span />Гостевой режим</span>
      </header>

      <section className={styles.transcript} aria-label="Ваш разговор с NoClip">
        {chat.prompt ? <div className={styles.question}>
          <p className={styles.eyebrow}>Ваш вопрос</p>
          <h1>{chat.prompt}</h1>
        </div> : <div className={styles.welcome}>
          <Image className={styles.welcomeMark} src="/media/noclip-mark.png" alt="" width={52} height={44} />
          <p className={styles.eyebrow}>Пространство для ваших идей</p>
          <h1>С чего начнём?</h1>
          <p>Задайте вопрос. Разберём сложное,<br />найдём слова или начнём с чистого листа.</p>
        </div>}

        {chat.answer && <section className={styles.answerSection} aria-label="Ответ NoClip">
          <div className={styles.answerByline}><Image className={styles.smallMark} src="/media/noclip-mark.png" alt="" width={24} height={20} />NoClip</div>
          <div className={styles.answer}>{chat.answer}</div>
        </section>}

        {busy && <div className={styles.loading} role="status">
          <span className={styles.loadingDots} aria-hidden="true"><i /><i /><i /></span>
          <span>{phaseLabels[chat.phase as keyof typeof phaseLabels]}</span>
        </div>}

        <div ref={verification} className={styles.verification} aria-label="Проверка браузера" />

        {chat.problem && <div className={styles.notice} role="status">
          <span className={styles.noticeMark} aria-hidden="true">↗</span>
          <div><h2>{chat.problem.title}</h2><p>{chat.problem.message}</p></div>
        </div>}

        {chat.phase === "stopped" && <div className={styles.notice} role="status">
          <span className={styles.noticeMark} aria-hidden="true">Ⅱ</span>
          <div><h2>Запрос остановлен</h2><p>Текст сохранён в этой вкладке. Попытка могла быть использована; автоматической повторной отправки не будет.</p></div>
        </div>}

        {chat.phase === "complete" && <p className={styles.complete} role="status">Ответ завершён</p>}
        {chat.phase === "complete" && remaining === 0 && <div className={styles.notice} role="status">
          <span className={styles.noticeMark} aria-hidden="true">↗</span>
          <div><h2>Демо исчерпано</h2><p>Лимит пробного доступа исчерпан. Для продолжения понадобится постоянный аккаунт.</p></div>
        </div>}
      </section>

      <div className={styles.composerDock}>
        <div className={styles.composerWidth}>
          <form onSubmit={submit} className={styles.composer} aria-label="Новый вопрос">
            <label htmlFor="guest-prompt" className={styles.srOnly}>Ваш вопрос</label>
            <textarea
              ref={textarea} id="guest-prompt" value={draft} rows={1}
              placeholder="Спросите о чём угодно…" readOnly={busy}
              onChange={(event) => setDraft(event.target.value)} onKeyDown={keydown}
              aria-describedby="guest-composer-help" aria-invalid={count > maxInputChars || undefined}
              autoComplete="off" spellCheck
            />
            {busy ? <button type="button" onClick={stop} className={styles.sendButton} aria-label="Остановить ответ" title="Остановить ответ"><span className={styles.stopIcon} /></button>
              : <button type="submit" disabled={!canSubmit} className={styles.sendButton} aria-label={enabled ? "Отправить вопрос" : "Сохранить вопрос"} title={enabled ? "Отправить вопрос" : "Сохранить вопрос"}><Arrow /></button>}
          </form>
          <div id="guest-composer-help" className={styles.composerHelp}>
            <span className={count > maxInputChars ? styles.overLimit : undefined}>{count > 0 ? `${count.toLocaleString("ru-RU")} / ${maxInputChars.toLocaleString("ru-RU")}` : "Один вопрос — один ответ"}</span>
            <span className={styles.keyboardHint}>Enter — отправить · Shift + Enter — новая строка</span>
          </div>
          {!enabled ? <p className={styles.availability}>Демонстрация временно недоступна. Текст остаётся в этой вкладке.</p>
            : remaining !== null && remaining > 0 ? <p className={styles.availability}>Осталось запросов: {remaining}. Без регистрации.</p>
            : <p className={styles.availability}>Ответ может содержать неточности. Важное стоит проверить.</p>}
        </div>
      </div>
    </main>
  </div>;
}
