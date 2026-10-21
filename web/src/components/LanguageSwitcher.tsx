"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { useSitePreferences, type Locale } from "./SitePreferences";
import styles from "./LanguageSwitcher.module.css";

function Flag({ locale }: { locale: Locale }) {
  return <svg data-language-flag viewBox="0 0 30 20" width="24" height="16" aria-hidden="true" className={styles.flag}>
    {locale === "ru" ? <><path fill="#fff" d="M0 0h30v20H0z" /><path fill="#2455a6" d="M0 6.67h30v6.66H0z" /><path fill="#d72d39" d="M0 13.33h30V20H0z" /></>
      : <><path fill="#243b79" d="M0 0h30v20H0z" /><path d="m0 0 30 20M30 0 0 20" stroke="#fff" strokeWidth="5" /><path d="m0 0 30 20M30 0 0 20" stroke="#c63242" strokeWidth="2" /><path d="M15 0v20M0 10h30" stroke="#fff" strokeWidth="7" /><path d="M15 0v20M0 10h30" stroke="#c63242" strokeWidth="4" /></>}
  </svg>;
}

export default function LanguageSwitcher() {
  const { locale, setLocale, t } = useSitePreferences();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    root.current?.querySelector<HTMLButtonElement>('[aria-checked="true"]')?.focus();
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);
  function close() { setOpen(false); trigger.current?.focus(); }
  function keys(event: KeyboardEvent) {
    if (event.key === "Escape") { event.preventDefault(); close(); return; }
    if (event.key === "Tab") { setOpen(false); return; }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    if (!open) { setOpen(true); return; }
    const options = [...(root.current?.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]') || [])];
    const current = options.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 : (current + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
    options[next]?.focus();
  }
  return <div ref={root} className={styles.root} onKeyDown={keys}>
    <button ref={trigger} type="button" className={styles.trigger} aria-label={t("Change language", "Выбрать язык")}
      aria-haspopup="menu" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen(!open)}>
      <svg data-language-globe width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true"><circle cx="12" cy="12" r="9" /><ellipse cx="12" cy="12" rx="4" ry="9" /><path d="M3 12h18M5 6.5h14M5 17.5h14" /></svg><span>{locale.toUpperCase()}</span>
      <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true"><path d="m3 4.5 3 3 3-3" stroke="currentColor" strokeWidth="1.3" /></svg>
    </button>
    {open && <div id={id} role="menu" aria-label={t("Language", "Язык")} className={styles.menu}>
      {([ ["en", "English"], ["ru", "Русский"] ] as const).map(([code, label]) => <button key={code} type="button" role="menuitemradio"
        aria-checked={locale === code} className={styles.option} onClick={() => { setLocale(code); close(); }}>
        <Flag locale={code} /><span>{label}</span><span className={styles.check} aria-hidden="true">{locale === code ? "✓" : ""}</span>
      </button>)}
    </div>}
  </div>;
}
