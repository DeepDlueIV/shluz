"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type Locale = "en" | "ru";
export type Translate = (english: string, russian: string) => string;
type Preferences = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: Translate;
};
const Context = createContext<Preferences | null>(null);

export function SitePreferences({ children }: { children: ReactNode }) {
  // English is the SSR default, regardless of the browser's language.
  const [locale, updateLocale] = useState<Locale>("en");
  useEffect(() => {
    try { if (localStorage.getItem("noclip.locale") === "ru") updateLocale("ru"); } catch { /* Storage may be blocked. */ }
  }, []);
  const setLocale = useCallback((next: Locale) => {
    updateLocale(next);
    try { localStorage.setItem("noclip.locale", next); } catch { /* In-memory selection is enough. */ }
  }, []);
  useEffect(() => { document.documentElement.lang = locale; }, [locale]);
  const t = useCallback<Translate>((english, russian) => locale === "ru" ? russian : english, [locale]);
  const value = useMemo(() => ({ locale, setLocale, t }),
    [locale, setLocale, t]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useSitePreferences() {
  const context = useContext(Context);
  if (!context) throw new Error("SitePreferences is missing");
  return context;
}
