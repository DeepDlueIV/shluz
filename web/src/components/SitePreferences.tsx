"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type Locale = "en" | "ru";
export type Translate = (english: string, russian: string) => string;
type Preferences = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: Translate;
  ambientMotion: boolean;
  preferencesReady: boolean;
  enableAnimation: () => void;
};
const Context = createContext<Preferences | null>(null);

export function SitePreferences({ children }: { children: ReactNode }) {
  // English is the SSR default, regardless of the browser's language.
  const [locale, updateLocale] = useState<Locale>("en");
  const [reducedMotion, setReducedMotion] = useState(false);
  const [animationOptIn, setAnimationOptIn] = useState(false);
  const [preferencesReady, setReady] = useState(false);
  useEffect(() => {
    try { if (localStorage.getItem("noclip.locale") === "ru") updateLocale("ru"); } catch { /* Storage may be blocked. */ }
    try { setAnimationOptIn(sessionStorage.getItem("noclip.animation") === "on"); } catch { /* In-memory preferences still work. */ }
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(query.matches);
    update();
    setReady(true);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  const setLocale = useCallback((next: Locale) => {
    updateLocale(next);
    try { localStorage.setItem("noclip.locale", next); } catch { /* In-memory selection is enough. */ }
  }, []);
  const enableAnimation = useCallback(() => {
    setAnimationOptIn(true);
    try { sessionStorage.setItem("noclip.animation", "on"); } catch { /* Optional persistence. */ }
  }, []);
  const ambientMotion = preferencesReady && (!reducedMotion || animationOptIn);
  useEffect(() => { document.documentElement.lang = locale; }, [locale]);
  useEffect(() => {
    document.documentElement.dataset.ambientMotion = ambientMotion ? "on" : "off";
  }, [ambientMotion]);
  const t = useCallback<Translate>((english, russian) => locale === "ru" ? russian : english, [locale]);
  const value = useMemo(() => ({ locale, setLocale, t, ambientMotion, preferencesReady, enableAnimation }),
    [locale, setLocale, t, ambientMotion, preferencesReady, enableAnimation]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useSitePreferences() {
  const context = useContext(Context);
  if (!context) throw new Error("SitePreferences is missing");
  return context;
}
