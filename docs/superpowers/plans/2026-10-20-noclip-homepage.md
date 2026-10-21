# NoClip homepage v1 — Implementation Plan

> **For agentic workers:** execute the approved website blueprint; independent asset and gateway tasks may be delegated. Approval to implement was given by the owner on 2026-10-20.

**Goal:** Первая адаптивная главная NoClip с предоставленным видео, настоящим backdrop blur и превращением ввода в закреплённую навигацию.
**Architecture:** Next.js App Router in web/; public landing + minimal guest chat destination. Existing gateway remains authoritative. No changes to gateway or Telegram.
**Tech Stack:** Next.js, React, TypeScript, CSS Modules, Motion.
**Spec:** docs/website-passport.md and previously approved NoClip_Web_Blueprint.md.

## Global Constraints

- Header labels mirror the reference temporarily; no additional marketing routes.
- Provided video and logo are provisional assets; no invented provider claims, prices, customer metrics or live AI responses.
- New guest created on first admitted prompt; new-email OTP preserves identity/usage.
- Prompt stays in application memory and never in URL; navigation to /app precedes one SSE POST.
- No production publication, gateway edits or real generation without configured service.
- Russian UI; minimal English reference navigation labels.

## Review Focus

- Width390px: no horizontal scrolling; composer and navigation remain reachable.
- Scroll down/up: same draft survives, underlying content is genuinely blurred.
- Reduced motion/video autoplay failure: poster and usable input.
- Unconfigured gateway: honest unavailable state, saved prompt, no fake answer.
- Streaming EOF/duplicate effect: no silent success and no automatic second paid POST.

## Tasks

### 1. Media and shell
- [x] Optimise supplied HEVC to H264 loop, desktop/mobile variants; extract logo and favicon.
- [x] Create web package/config/layout/global styles and repository ignores.
- [x] Verify metadata and dependency installation.

### 2. Landing and motion
- [x] Build hero, scroll-linked persistent composer/navigation, prompt examples.
- [x] Build model strip, five-stage capability section, plans, API, trust, closing CTA/footer.
- [x] Implement responsive layout, keyboard navigation, reduced-motion fallback.
- [x] Check build/types and rendered page if browser capability is available.

### 3. Guest destination
- [x] Implement in-memory handoff and minimal /app shell.
- [x] Implement session/Turnstile/fetch-SSE adapter behind explicit configuration.
- [x] Cover split SSE frames, incomplete streams and one-time handoff with meaningful tests.
- [x] Verify disabled-gateway path; real provider integration remains an external gate.

### 4. Delivery
- [x] Run TypeScript, focused tests, production build; review source and behaviour.
- [x] Save short passport and local feature commit.
- [x] Prepare concrete reviewable output; respect AGENTS.md explicit-push rule.


## Результат проверки — 2026-10-20

- TypeScript: PASS.
- Тесты SSE/guest handoff: 13/13 PASS; без реальной сети.
- Production build и статический export: проверены при сборке deliverable.
- Независимое ревью кода завершено. Исправлены восстановление session после временной ошибки, фокус скрытых подсказок, IME Enter, mobile input и sticky на низком viewport.
- Browser QA пропущен: инструкция Sites запрещает запуск preview без отсутствующего control-browser. Скриншотов и подтверждения фактической вёрстки в браузере нет.

Открытые проверки перед публикацией:

- [ ] Визуально проверить desktop/mobile, morph, blur, navigation, /app и reduced motion.
- [ ] Проверить реальную интеграцию HTTPS/Origin, Turnstile, SSE и гостевых лимитов с актуальной веткой шлюза.
- [x] Разрешение на push получено 2026-10-21; соответствующий запрет в AGENTS.md удалён по указанию владельца. Production deployment не выполнялся.
