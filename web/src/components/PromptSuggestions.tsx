"use client";

import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { PromptSuggestion } from '../lib/prompt-suggestions';
import styles from './PromptSuggestions.module.css';

type Props = {
  primary: PromptSuggestion[];
  additional: PromptSuggestion[];
  enabled: boolean;
  moreLabel: string;
  onPreview(id: string): void;
  onChoose(prompt: string): void;
};

function SuggestionIcon({ name }: { name: string }) {
  const paths: Record<string, ReactNode> = {
    eyeOff: <><path d="m3 3 18 18M10.5 5.2 12 5c6 0 10 7 10 7a18 18 0 0 1-3 3.5M6.5 6.5C3.5 8.5 2 12 2 12s4 7 10 7a12 12 0 0 0 5.5-1.5" /><path d="M10 10a3 3 0 0 0 4 4" /></>,
    unlock: <><rect x="4" y="10" width="16" height="11" rx="3" /><path d="M8 10V6a4 4 0 0 1 7.5-2M12 14v3" /></>,
    feather: <><path d="M20 4c-7-3-15 5-12 12 7 3 15-5 12-12ZM3 21 15 9M9 15h6" /></>,
    scales: <><path d="M12 3v18M7 21h10M4 7h16M5 7l-3 7h6L5 7Zm14 0-3 7h6l-3-7Z" /></>,
    key: <><circle cx="8" cy="8" r="5" /><path d="m12 12 9 9m-4-4 3-3m-6 0 3-3" /></>,
    shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z" /><path d="m9 12 2 2 4-4" /></>,
    fileCode: <><path d="M14 2H5v20h14V7l-5-5Zm0 0v6h5M9 12l-3 3 3 3m5-6 3 3-3 3" /></>,
    phone: <><rect x="6" y="2" width="12" height="20" rx="3" /><path d="M10 5h4M11 18h2" /></>,
    rocket: <><path d="M14 4c3-2 6-2 7-1 1 1 1 4-1 7l-8 8-6-6 8-8ZM6 12H2l4-6h6m0 12v4l6-4v-6M7 17l-4 4" /><circle cx="16" cy="7" r="1.5" /></>,
    flask: <><path d="M8 2h8M10 2v7L4 19c-1 2 0 3 2 3h12c2 0 3-1 2-3L14 9V2M7 15h10" /></>,
    ghost: <><path d="M5 21V10a7 7 0 0 1 14 0v11l-4-3-3 3-3-3-4 3Z" /><path d="M9 10h.01M15 10h.01" strokeWidth="3" /></>,
    atom: <><ellipse cx="12" cy="12" rx="10" ry="4" /><ellipse cx="12" cy="12" rx="10" ry="4" transform="rotate(60 12 12)" /><ellipse cx="12" cy="12" rx="10" ry="4" transform="rotate(120 12 12)" /></>,
    building: <><path d="M4 22V3h12v19M16 10h4v12M8 7h4M8 11h4M8 15h4M9 22v-3h2v3" /></>,
    book: <><path d="M12 5c-3-2-6-2-10-1v15c4-1 7-1 10 1 3-2 6-2 10-1V4c-4-1-7-1-10 1Zm0 0v15" /></>,
  };
  return <svg data-suggestion-icon width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

export default function PromptSuggestions({ primary, additional, enabled, moreLabel, onPreview, onChoose }: Props) {
  const [open, setOpen] = useState(false);
  const moreRef = useRef<HTMLButtonElement>(null);
  const firstExtraRef = useRef<HTMLButtonElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const focusExtra = useRef(false);
  const pointerInside = useRef(false);
  const hoverSession = useRef(false);
  const expanded = open && enabled;

  function cancelClose() {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    closeTimer.current = null;
  }
  function closeSoon() {
    cancelClose();
    closeTimer.current = setTimeout(() => { setOpen(false); onPreview(''); }, 180);
  }
  useEffect(() => () => { if (closeTimer.current) clearTimeout(closeTimer.current); }, []);
  useEffect(() => { if (!enabled) { hoverSession.current = false; setOpen(false); } }, [enabled]);
  useEffect(() => {
    if (expanded && focusExtra.current) { firstExtraRef.current?.focus(); focusExtra.current = false; }
  }, [expanded]);

  function choice(item: PromptSuggestion, first = false, extra = false) {
    return <button key={item.id} type="button" data-suggestion={item.id} title={item.label} aria-label={item.label}
      ref={first ? firstExtraRef : undefined} tabIndex={enabled && (!extra || expanded) ? 0 : -1}
      onPointerEnter={event => { if (event.pointerType !== 'touch' && enabled) onPreview(item.id); }}
      onFocus={() => { if (enabled) onPreview(item.id); }}
      onClick={() => { cancelClose(); hoverSession.current = false; setOpen(false); onChoose(item.prompt); }}>
      <SuggestionIcon name={item.icon} /><span>{item.label}</span>
    </button>;
  }

  return <div className={styles.region} data-suggestion-region inert={!enabled}
    onPointerEnter={event => { if (event.pointerType !== 'touch') { pointerInside.current = true; cancelClose(); if (hoverSession.current && enabled) setOpen(true); } }}
    onPointerLeave={event => { if (event.pointerType !== 'touch') { pointerInside.current = false; closeSoon(); } }}
    onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget) && !pointerInside.current) closeSoon(); }}
    onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); cancelClose(); hoverSession.current = false; setOpen(false); onPreview(''); moreRef.current?.focus(); }
    }}>
    <div className={styles.primary} data-primary-suggestions>
      {primary.map(item => choice(item))}
      <button ref={moreRef} type="button" data-more-prompts aria-expanded={expanded} aria-controls="additional-prompts"
        tabIndex={enabled ? 0 : -1} className={styles.more}
        onPointerEnter={event => { if (event.pointerType !== 'touch' && enabled) { cancelClose(); hoverSession.current = true; setOpen(true); } }}
        onClick={event => {
          cancelClose();
          // Мышь уже открыла меню по hover; click не должен немедленно запускать обратный fade.
          if (event.detail > 0 && pointerInside.current) { hoverSession.current = true; setOpen(true); }
          else { hoverSession.current = false; setOpen(value => !value); }
        }}
        onKeyDown={event => {
          if (event.key === 'ArrowDown') { event.preventDefault(); cancelClose(); focusExtra.current = true; setOpen(true); if (expanded) { firstExtraRef.current?.focus(); focusExtra.current = false; } }
        }}>
        <span>{moreLabel}</span><svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m6 9 6 6 6-6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </button>
    </div>
    {/* Постоянный DOM: CSS продолжает переход из текущего кадра при смене направления. */}
    <div id="additional-prompts" className={styles.disclosure} data-additional-prompts
      data-open={expanded} aria-hidden={!expanded} inert={!expanded}
      onTransitionEnd={event => {
        if (event.propertyName === 'opacity' && !expanded) hoverSession.current = false;
      }}>
      <div className={styles.additionalViewport}>
        <div className={styles.additional}>{additional.map((item, index) => choice(item, index === 0, true))}</div>
      </div>
    </div>
  </div>;
}
