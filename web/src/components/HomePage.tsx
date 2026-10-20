"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import { AnimatePresence, motion, useMotionValueEvent, useReducedMotion, useScroll, useTransform } from "motion/react";
import { useGuestJourney } from "./GuestJourney";
import LandingSections from "./LandingSections";
import styles from "./HomePage.module.css";

const examples = [
  { label: "Поговорить откровенно", prompt: "Давай обсудим сложную тему без нравоучений. Как научиться отстаивать своё мнение, когда все вокруг с ним не согласны?", icon: "chat" },
  { label: "Придумать историю", prompt: "Придумай начало атмосферного нуарного рассказа: город, в котором никто не может лгать, и человек, который научился.", icon: "spark" },
  { label: "Написать код", prompt: "Помоги создать минималистичный сайт-портфолио с красивой типографикой и плавными анимациями. Начни с плана и структуры.", icon: "code" },
  { label: "Удиви меня", prompt: "Расскажи неожиданную идею о мире, которая заставит меня посмотреть на привычные вещи по-другому.", icon: "dice" },
] as const;

const navLinks = [
  { label: "About", href: "#about" },
  { label: "Features", href: "#features" },
  { label: "Token", soon: true },
  { label: "Pricing", href: "#pricing" },
  { label: "Resources", href: "#developers" },
  { label: "Store", soon: true },
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

function HeroVideo() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const reduceMotion = useReducedMotion();
  const [mobile, setMobile] = useState<boolean | null>(null);

  useEffect(() => {
    const query = window.matchMedia("(max-width: 700px)");
    const update = () => setMobile(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || mobile === null || reduceMotion) return;
    const visibility = () => {
      const visible = !document.hidden && video.getBoundingClientRect().bottom > 0;
      if (visible) void video.play().catch(() => {});
      else video.pause();
    };
    const observer = new IntersectionObserver(visibility, { threshold: 0 });
    observer.observe(video);
    document.addEventListener("visibilitychange", visibility);
    visibility();
    return () => { observer.disconnect(); document.removeEventListener("visibilitychange", visibility); };
  }, [mobile, reduceMotion]);

  return <div className={styles.heroMedia} aria-hidden="true">
    <picture>
      <source media="(max-width: 700px)" srcSet="/media/hero-poster-mobile.webp" />
      <img className={styles.poster} src="/media/hero-poster.webp" alt="" fetchPriority="high" />
    </picture>
    {mobile !== null && !reduceMotion && <video key={mobile ? "mobile" : "desktop"} ref={videoRef}
      className={styles.video} autoPlay muted loop playsInline preload="auto"
      poster={mobile ? "/media/hero-poster-mobile.webp" : "/media/hero-poster.webp"}>
      <source src={mobile ? "/media/hero-mobile.mp4" : "/media/hero-desktop.mp4"} type="video/mp4" />
    </video>}
    <div className={styles.videoShade} />
  </div>;
}

export default function HomePage() {
  const { openPrompt, openChat } = useGuestJourney();
  const { scrollY } = useScroll();
  const reduceMotion = useReducedMotion();
  const anchorRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [draft, setDraft] = useState("");
  const [preview, setPreview] = useState("");
  const [docked, setDocked] = useState(false);
  const [suggestionsVisible, setSuggestionsVisible] = useState(true);
  const [mobileMenu, setMobileMenu] = useState(false);
  const [metrics, setMetrics] = useState({ top: 560, width: 740, navWidth: 1120, mobile: false, ready: false });
  const [notice, setNotice] = useState({ title: "", body: "" });

  const distance = Math.max(1, metrics.top - 18);
  const progress = useTransform(scrollY, value => Math.min(1, Math.max(0, value / distance)));
  const top = useTransform(scrollY, value => Math.max(18, metrics.top - value));
  const width = useTransform(progress, [0, 0.35, 1], [metrics.width, metrics.width, metrics.navWidth]);
  const radius = useTransform(progress, [0, 1], [38, 26]);
  const formWidth = useTransform(progress, [0, 0.6, 1], [metrics.width - 30, metrics.width - 30, metrics.mobile ? metrics.navWidth - 82 : 264]);
  const navOpacity = useTransform(progress, [0, 0.76, 0.96], [0, 0, 1]);
  const navWidth = useTransform(progress, [0, 0.6, 1], [0, 0, metrics.mobile ? 48 : metrics.navWidth - 300]);
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
  }, []);

  useMotionValueEvent(progress, "change", value => {
    const next = value > 0.88;
    setDocked(previous => previous === next ? previous : next);
    const showSuggestions = value < 0.35;
    setSuggestionsVisible(previous => previous === showSuggestions ? previous : showSuggestions);
    if (!next) setMobileMenu(false);
  });

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

  const renderNav = (mobile = false) => <nav aria-label={mobile ? "Навигация на мобильном экране" : "Основная навигация"} className={mobile ? styles.menuLinks : styles.navLinks}>
    {navLinks.map(item => "href" in item
      ? <a key={item.label} href={item.href} tabIndex={docked ? 0 : -1} onClick={() => setMobileMenu(false)}>{item.label}</a>
      : <button key={item.label} type="button" className={styles.soonLink} tabIndex={docked ? 0 : -1}
          onClick={() => showNotice(item.label, "Этот раздел пока не открыт. Сейчас можно познакомиться с возможностями NoClip.")}>{item.label}<span className={styles.soonDot} aria-label="Скоро" /></button>)}
    <button type="button" className={styles.loginLink} tabIndex={docked ? 0 : -1}
      onClick={() => showNotice("Вход в NoClip", "Вход в личный аккаунт пока недоступен. Вы можете продолжить знакомство с NoClip и попробовать гостевой запрос.")}>Log in <Icon name="right" size={14} /></button>
  </nav>;

  return <main className={styles.page}>
    <a className={styles.skipLink} href="#features">Перейти к возможностям</a>
    <section id="home" className={styles.hero} aria-labelledby="hero-title">
      <HeroVideo />
      <header className={styles.header}>
        <a href="#home" className={styles.wordmark} aria-label="NoClip — на главную">
          <img src="/media/noclip-mark.png" width="38" height="32" alt="" />
          <span>NoClip</span>
        </a>
        <button className={styles.headerLogin} onClick={() => showNotice("Вход в NoClip", "Вход в личный аккаунт пока недоступен. Вы можете продолжить знакомство с NoClip и попробовать гостевой запрос.")}>Войти <Icon name="right" size={15} /></button>
      </header>
      <div className={styles.heroContent}>
        <motion.div className={styles.heroCopy} style={{ opacity: titleOpacity, y: titleY }}>
          <div className={styles.eyebrow}><span /> YOUR MIND. YOUR RULES.</div>
          <h1 id="hero-title">Спросите<br /><em>о чём угодно.</em></h1>
          <p>Меньше ограничений. Больше возможностей.</p>
        </motion.div>
        <div ref={anchorRef} className={styles.composerAnchor} />
        <motion.div className={styles.suggestions} style={{ opacity: chipOpacity, pointerEvents: suggestionsVisible ? "auto" : "none" }} aria-label="Примеры запросов" aria-hidden={!suggestionsVisible || !!draft}>
          <div className={styles.chipRow} style={{ visibility: draft ? "hidden" : "visible" }}>
            {examples.map(example => <button key={example.label} type="button"
              tabIndex={suggestionsVisible && !draft ? 0 : -1}
              onMouseEnter={() => !draft && setPreview(example.prompt)}
              onMouseLeave={() => setPreview("")}
              onFocus={() => !draft && setPreview(example.prompt)}
              onBlur={() => setPreview("")}
              onClick={() => { setDraft(example.prompt); setPreview(""); inputRef.current?.focus(); }}>
              <Icon name={example.icon} size={14} /><span>{example.label}</span>
            </button>)}
          </div>
          <p className={styles.heroNote}>Начните с вопроса. Остальное — за вашим воображением.</p>
        </motion.div>
      </div>
      <a className={styles.scrollHint} href="#models"><span>Откройте больше</span><Icon name="down" size={19} /></a>
      <div className={styles.heroIndex} aria-hidden="true"><span>NOCLIP</span><span>01 — EXPLORE</span></div>
    </section>

    <motion.div className={styles.glassPanel} data-docked={docked} data-ready={metrics.ready}
      style={{ top, width, borderRadius: radius }} aria-label={docked ? "Ввод и навигация" : "Поле запроса"}>
      <motion.form onSubmit={submit} className={styles.composer} style={{ width: formWidth }}>
        <button type="button" className={styles.composerMark} onClick={focusComposer} aria-label="Вернуться к началу">
          <img src="/media/noclip-mark.png" width="30" height="26" alt="" />
        </button>
        <label htmlFor="hero-prompt" className="sr-only">Ваш вопрос для NoClip</label>
        <input ref={inputRef} id="hero-prompt" name="prompt" value={draft}
          onChange={event => { setDraft(event.target.value); setPreview(""); }}
          onKeyDown={event => { if (event.key === "Enter" && event.nativeEvent.isComposing) event.preventDefault(); }}
          autoComplete="off" spellCheck placeholder={preview || (docked ? "Задайте вопрос…" : "Что вы хотите исследовать?")}
          aria-describedby="prompt-guidance" />
        <button type="submit" className={styles.sendButton} disabled={!draft.trim()} aria-label="Отправить запрос"><Icon name="arrow" size={21} /></button>
      </motion.form>
      <motion.div className={styles.navArea} style={{ width: navWidth, opacity: navOpacity, pointerEvents: docked ? "auto" : "none" }} aria-hidden={!docked}>
        {metrics.mobile
          ? <button className={styles.menuButton} type="button" onClick={() => setMobileMenu(!mobileMenu)} aria-expanded={mobileMenu} aria-controls="mobile-navigation" aria-label={mobileMenu ? "Закрыть меню" : "Открыть меню"} tabIndex={docked ? 0 : -1}><Icon name={mobileMenu ? "close" : "menu"} size={21} /></button>
          : renderNav()}
      </motion.div>
    </motion.div>
    <span id="prompt-guidance" className="sr-only">После отправки вы перейдёте в гостевой чат. Регистрация для первого запроса не требуется.</span>

    <AnimatePresence>
      {mobileMenu && docked && <motion.div id="mobile-navigation" className={styles.mobileMenu}
        initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}>
        {renderNav(true)}
      </motion.div>}
    </AnimatePresence>

    <LandingSections onPrompt={openPrompt} onOpenChat={openChat} />

    <dialog ref={dialogRef} className={styles.noticeDialog} aria-labelledby="notice-title" aria-describedby="notice-body" onClick={event => {
      if (event.target === event.currentTarget) dialogRef.current?.close();
    }}>
      <div className={styles.noticeInner}>
        <button className={styles.dialogClose} onClick={() => dialogRef.current?.close()} aria-label="Закрыть"><Icon name="close" /></button>
        <img src="/media/noclip-mark.png" width="42" height="36" alt="" />
        <h2 id="notice-title">{notice.title}</h2><p id="notice-body">{notice.body}</p>
        <button className={styles.dialogAction} onClick={() => { dialogRef.current?.close(); focusComposer(); }}>К своему вопросу <Icon name="right" size={16} /></button>
      </div>
    </dialog>
  </main>;
}
