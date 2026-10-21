"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type Locale = "en" | "ru";
export type Translate = (english: string, russian: string) => string;
type Preferences = { locale: Locale; setLocale: (locale: Locale) => void; t: Translate };
const Context = createContext<Preferences | null>(null);

export function SitePreferences({ children }: { children: ReactNode }) {
  // Начальный язык одинаков на сервере и в браузере, выбор пользователя хранится локально.
  const [locale, updateLocale] = useState<Locale>("en");
  useEffect(() => {
    try { if (localStorage.getItem("noclip.locale") === "ru") updateLocale("ru"); } catch { /* Хранилище необязательно. */ }
    // Фоновое видео и ленты запускаются сразу: согласованное поведение витрины.
    document.documentElement.dataset.ambientMotion = "on";
  }, []);
  const setLocale = useCallback((next: Locale) => {
    updateLocale(next);
    try { localStorage.setItem("noclip.locale", next); } catch { /* Выбор остаётся в памяти вкладки. */ }
  }, []);
  useEffect(() => { document.documentElement.lang = locale; }, [locale]);
  const t = useCallback<Translate>((english, russian) => locale === "ru" ? russian : english, [locale]);
  const value = useMemo(() => ({ locale, setLocale, t }), [locale, setLocale, t]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useSitePreferences() {
  const context = useContext(Context);
  if (!context) throw new Error("SitePreferences is missing");
  return context;
}
