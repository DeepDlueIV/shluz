"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import { AnimatePresence, motion, useMotionValueEvent, useReducedMotion, useScroll, useTransform } from "motion/react";
import { useGuestJourney } from "./GuestJourney";
import LandingSections from "./LandingSections";
import styles from "./HomePage.module.css";
import presentation from "./Presentation.module.css";
import HeroVideo from "./HeroVideo";
import PromptPreview from "./PromptPreview";
import LanguageSwitcher from "./LanguageSwitcher";
import { useSitePreferences, type Translate } from "./SitePreferences";

const getExamples = (t: Translate) => [
  { id: "chat", label: t("Discuss a topic", "Обсудить тему"), prompt: t("Discuss a controversial topic from different perspectives, without moralizing. How can I defend my opinion when everyone around me disagrees?", "Обсуди спорную тему с разных сторон, без нравоучений. Как отстаивать своё мнение, когда окружающие с ним не согласны?"), icon: "chat" },
  { id: "story", label: t("Write a story", "Написать историю"), prompt: t("Write the opening of a noir story about a city where nobody can lie, and one person who learns how.", "Напиши начало нуарного рассказа о городе, в котором никто не может лгать, и человеке, который научился."), icon: "spark" },
  { id: "code", label: t("Write code", "Написать код"), prompt: t("Help me build a minimal portfolio website with responsive typography. Start with the file structure and implementation plan.", "Помоги создать минималистичный сайт-портфолио с адаптивной типографикой. Начни со структуры файлов и плана реализации."), icon: "code" },
  { id: "explain", label: t("Explain a concept", "Объяснить понятие"), prompt: t("Explain how a language model generates an answer. Use one concrete example and distinguish facts from common misconceptions.", "Объясни, как языковая модель создаёт ответ. Используй конкретный пример и отдели факты от распространённых заблуждений."), icon: "dice" },
];

const navLinks = [
  { label: "About", labelRu: "О проекте", href: "#about" },
  { label: "Features", labelRu: "Возможности", href: "#features" },
  { label: "Token", labelRu: "Токен", soon: true },
  { label: "Pricing", labelRu: "Тарифы", href: "#pricing" },
  { label: "Resources", labelRu: "Ресурсы", href: "#developers" },
  { label: "Store", labelRu: "Магазин", soon: true },
] as const;

function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const paths: Record<string, React.ReactNode> = {
    arrow: <><path d="M12 19V5M5 12l7-7 7 7" /></>,
    down: <><path d="M12 4v16m-6-6 6 6 6-6" /></>,
    right: <><path d="M5 12h14m-6-6 6 6-6 6" /></>,
    chat: <path d="M20 11.5a7.5 7.5 0 0 1-7.5 7.5H5l-2 2V11.5A7.5 7.5 0 0 1 10.5 4h2A7.5 7.5 0 0 1 20 11.5Z" />,
    code: <><path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-14-2 18" /></>,
    spark: <><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z" /></>,
    dice: <><rect x="4" y="4" width="16" height="16" rx="4" /><path d="M8 8h.01M16 8h.01M12 12h.01M8 16h.01M16 16h.01" strokeWidth="3" /></>,
    menu: <><path d="M5 8h14M5 16h14" /></>,
    close: <><path d="m6 6 12 12M6 18 18 6" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.35" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.spark}</svg>;
}

export default function HomePage() {
  const { openPrompt, openChat } = useGuestJourney();
  const { t } = useSitePreferences();
  const examples = getExamples(t);
  const { scrollY } = useScroll();
  const reduceMotion = useReducedMotion();
  const anchorRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const promptToggleRef = useRef<HTMLButtonElement>(null);
  const [draft, setDraft] = useState("");
  const [preview, setPreview] = useState("");
  const [docked, setDocked] = useState(false);
  const [promptExpanded, setPromptExpanded] = useState(false);
  const [suggestionsVisible, setSuggestionsVisible] = useState(true);
  const [mobileMenu, setMobileMenu] = useState(false);
  const [metrics, setMetrics] = useState({ top: 560, width: 740, navWidth: 1120, mobile: false, ready: false });
  const [notice, setNotice] = useState({ title: "", body: "" });

  const distance = Math.max(1, metrics.top - 18);
  const progress = useTransform(scrollY, value => Math.min(1, Math.max(0, value / distance)));
  const top = useTransform(scrollY, value => Math.max(18, metrics.top - value));
  const width = useTransform(progress, [0, 0.35, 1], [metrics.width, metrics.width, metrics.navWidth]);
  const radius = useTransform(progress, [0, 1], [38, 26]);
  const titleOpacity = useTransform(scrollY, [0, Math.max(250, metrics.top * 0.66)], [1, 0]);
  const titleY = useTransform(scrollY, [0, metrics.top], [0, reduceMotion ? 0 : -65]);
  const chipOpacity = useTransform(progress, [0, 0.35], [1, 0]);

  useLayoutEffect(() => {
    const anchor = anchorRef.current;
    if (!anchor) return;
    const measure = () => {
      const rect = anchor.getBoundingClientRect();
      const vw = document.documentElement.clientWidth;
      setMetrics({
        top: rect.top + window.scrollY,
        width: rect.width,
        navWidth: Math.min(vw - (vw < 720 ? 24 : 48), 1120),
        mobile: vw < 1000,
        ready: true,
      });
    };
    measure();
    const resize = new ResizeObserver(measure);
    resize.observe(anchor);
    window.addEventListener("resize", measure);
    void document.fonts.ready.then(measure);
    return () => { resize.disconnect(); window.removeEventListener("resize", measure); };
  }, [t]);

  useMotionValueEvent(progress, "change", value => {
    const next = value > 0.88;
    setDocked(previous => previous === next ? previous : next);
    const showSuggestions = value < 0.35;
    setSuggestionsVisible(previous => previous === showSuggestions ? previous : showSuggestions);
    if (!next) { setMobileMenu(false); setPromptExpanded(false); }
  });

  const navigationVisible = docked && !promptExpanded;
  const promptVisible = !docked || promptExpanded;
  const togglePrompt = () => {
    setMobileMenu(false);
    setPreview("");
    if (promptExpanded) promptToggleRef.current?.focus({ preventScroll: true });
    setPromptExpanded(!promptExpanded);
  };
  useEffect(() => {
    if (!docked || !promptExpanded) return;
    const frame = requestAnimationFrame(() => inputRef.current?.focus({ preventScroll: true }));
    return () => cancelAnimationFrame(frame);
  }, [docked, promptExpanded]);

  useEffect(() => {
    if (!mobileMenu) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setMobileMenu(false); };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [mobileMenu]);

  const focusComposer = useCallback(() => {
    window.scrollTo({ top: 0, behavior: reduceMotion ? "instant" : "smooth" });
    inputRef.current?.focus({ preventScroll: true });
  }, [reduceMotion]);

  const showNotice = (title: string, body: string) => {
    setNotice({ title, body });
    dialogRef.current?.showModal();
    setMobileMenu(false);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (draft.trim()) openPrompt(draft.trim());
    else inputRef.current?.focus();
  };

  const renderNav = (mobile = false) => <nav aria-label={mobile ? t("Mobile navigation", "Навигация на мобильном экране") : t("Main navigation", "Основная навигация")} className={mobile ? styles.menuLinks : styles.navLinks}>
    {navLinks.map(item => "href" in item
      ? <a key={t(item.label, item.labelRu)} href={item.href} tabIndex={navigationVisible ? 0 : -1} onClick={() => setMobileMenu(false)}>{t(item.label, item.labelRu)}</a>
      : <button key={t(item.label, item.labelRu)} type="button" className={styles.soonLink} tabIndex={navigationVisible ? 0 : -1}
          onClick={() => showNotice(t(item.label, item.labelRu), t("This section is not available in the preview.", "Этот раздел пока не открыт. Сейчас можно познакомиться с возможностями NoClip."))}>{t(item.label, item.labelRu)}<span className={styles.soonDot} aria-label={t("Coming soon", "Скоро")} /></button>)}
    <button type="button" className={styles.loginLink} tabIndex={navigationVisible ? 0 : -1}
      onClick={() => showNotice(t("Log in to NoClip", "Вход в NoClip"), t("Account sign-in is not available in this preview. You can still open the guest chat.", "Вход в личный аккаунт пока недоступен. Вы можете продолжить знакомство с NoClip и попробовать гостевой запрос."))}>{t("Log in", "Войти")} <Icon name="right" size={14} /></button>
  </nav>;

  return <main className={styles.page}>
    <a className={styles.skipLink} href="#features">{t("Skip to features", "Перейти к возможностям")}</a>
    <section id="home" className={`${styles.hero} ${presentation.heroSection}`} aria-labelledby="hero-title">
      <HeroVideo />
      <header className={styles.header}>
        <a href="#home" className={styles.wordmark} aria-label={t("NoClip home", "NoClip — на главную")}>
          <img src="/media/noclip-mark.png" width="38" height="32" alt="" />
          <span>NoClip</span>
        </a>
        <div className={presentation.headerActions}><LanguageSwitcher /><button className={styles.headerLogin} onClick={() => showNotice(t("Log in to NoClip", "Вход в NoClip"), t("Account sign-in is not available in this preview. You can still open the guest chat.", "Вход в личный аккаунт пока недоступен. Вы можете продолжить знакомство с NoClip и попробовать гостевой запрос."))}>{t("Log in", "Войти")} <Icon name="right" size={15} /></button></div>
      </header>
      <div className={`${styles.heroContent} ${presentation.heroContent}`}>
        <motion.div className={styles.heroCopy} style={{ opacity: titleOpacity, y: titleY }}>
          <img data-hero-logo className={styles.heroMark} src="/media/noclip-mark.png" alt="" width="42" height="36" />
          <h1 id="hero-title">{t("Ask anything", "Спросите о чём угодно")}</h1>
        </motion.div>
        <div ref={anchorRef} className={styles.composerAnchor} />
        <motion.div className={styles.suggestions} style={{ opacity: chipOpacity, pointerEvents: suggestionsVisible ? "auto" : "none" }} aria-label={t("Example prompts", "Примеры запросов")} aria-hidden={!suggestionsVisible || !!draft}>
          <div className={styles.chipRow} style={{ visibility: draft ? "hidden" : "visible" }}>
            {examples.map(example => <button key={example.id} type="button"
              tabIndex={suggestionsVisible && !draft ? 0 : -1}
              onMouseEnter={() => !draft && setPreview(example.prompt)}
              onMouseLeave={() => setPreview("")}
              onFocus={() => !draft && setPreview(example.prompt)}
              onBlur={() => setPreview("")}
              onClick={() => { setDraft(example.prompt); setPreview(""); inputRef.current?.focus(); }}>
              <Icon name={example.icon} size={14} /><span>{example.label}</span>
            </button>)}
          </div>
        </motion.div>
      </div>
      <a className={styles.scrollHint} href="#models" aria-label={t("View models", "Смотреть модели")}><Icon name="down" size={19} /></a>
    </section>

    <motion.div className={styles.glassPanel} data-docked={docked} data-ready={metrics.ready}
      data-panel-mode={docked ? (promptExpanded ? "prompt" : "navigation") : "hero"}
      style={{ top, width, borderRadius: radius }} aria-label={docked ? t("Prompt and navigation", "Ввод и навигация") : t("Prompt", "Поле запроса")}>
      <button type="button" className={styles.composerMark} onClick={focusComposer} aria-label={t("Back to top", "Вернуться к началу")}>
        <img src="/media/noclip-mark.png" width="30" height="26" alt="" />
      </button>
      <div className={styles.panelBody}>
        <form id="topbar-composer" onSubmit={submit} className={styles.composer} data-composer-form
          inert={!promptVisible} aria-hidden={!promptVisible}>
          <label htmlFor="hero-prompt" className="sr-only">{t("Your prompt for NoClip", "Ваш вопрос для NoClip")}</label>
          <div className={styles.inputArea}>
            <input ref={inputRef} id="hero-prompt" name="prompt" value={draft} tabIndex={promptVisible ? 0 : -1}
              onChange={event => { setDraft(event.target.value); setPreview(""); }}
              onKeyDown={event => {
                if (event.key === "Enter" && event.nativeEvent.isComposing) event.preventDefault();
                if (event.key === "Escape" && docked && promptExpanded) { event.preventDefault(); togglePrompt(); }
              }}
              autoComplete="off" spellCheck placeholder="" aria-describedby="prompt-guidance" />
            <PromptPreview text={preview || t("Ask anything…", "Задайте вопрос…")} visible={!draft} />
          </div>
          {(!docked || !!draft.trim()) && <button type="submit" className={styles.sendButton} disabled={!draft.trim()}
            tabIndex={promptVisible ? 0 : -1} aria-label={t("Send prompt", "Отправить запрос")} title={t("Send prompt", "Отправить запрос")}>
            <Icon name={docked ? "right" : "arrow"} size={21} />
          </button>}
        </form>
        <button ref={promptToggleRef} type="button" data-prompt-toggle data-open={docked && promptExpanded}
          className={styles.promptToggle} tabIndex={docked ? 0 : -1} aria-hidden={!docked}
          aria-expanded={docked && promptExpanded} aria-controls="topbar-composer topbar-navigation"
          aria-label={promptExpanded ? t("Show navigation", "Показать навигацию") : t("Ask anything: expand prompt input", "Спросите о чём угодно: раскрыть поле")}
          title={promptExpanded ? t("Show navigation", "Показать навигацию") : t("Expand prompt input", "Раскрыть поле")}
          onClick={togglePrompt}>
          <span>{t("Ask anything", "Спросите о чём угодно")}</span><Icon name="arrow" size={19} />
        </button>
        <div id="topbar-navigation" className={styles.dockedNavigation} data-nav-content inert={!navigationVisible} aria-hidden={!navigationVisible}>
          {metrics.mobile
            ? <button className={styles.menuButton} type="button" onClick={() => setMobileMenu(!mobileMenu)} aria-expanded={mobileMenu} aria-controls="mobile-navigation" aria-label={mobileMenu ? t("Close menu", "Закрыть меню") : t("Open menu", "Открыть меню")} tabIndex={navigationVisible ? 0 : -1}><Icon name={mobileMenu ? "close" : "menu"} size={21} /></button>
            : renderNav()}
        </div>
      </div>
    </motion.div>
    <span id="prompt-guidance" className="sr-only">{t("Submitting a prompt opens the guest chat. No registration is required for the initial demo.", "После отправки вы перейдёте в гостевой чат. Регистрация для первого запроса не требуется.")}</span>

    <AnimatePresence>
      {mobileMenu && navigationVisible && <motion.div id="mobile-navigation" className={styles.mobileMenu}
        initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}>
        {renderNav(true)}
      </motion.div>}
    </AnimatePresence>

    <LandingSections onPrompt={openPrompt} onOpenChat={openChat} />

    <dialog ref={dialogRef} className={styles.noticeDialog} aria-labelledby="notice-title" aria-describedby="notice-body" onClick={event => {
      if (event.target === event.currentTarget) dialogRef.current?.close();
    }}>
      <div className={styles.noticeInner}>
        <button className={styles.dialogClose} onClick={() => dialogRef.current?.close()} aria-label={t("Close", "Закрыть")}><Icon name="close" /></button>
        <img src="/media/noclip-mark.png" width="42" height="36" alt="" />
        <h2 id="notice-title">{notice.title}</h2><p id="notice-body">{notice.body}</p>
        <button className={styles.dialogAction} onClick={() => { dialogRef.current?.close(); focusComposer(); }}>{t("Back to your prompt", "К своему вопросу")} <Icon name="right" size={16} /></button>
      </div>
    </dialog>
  </main>;
}
