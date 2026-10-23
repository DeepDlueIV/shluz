# Mini App owner trial

Goal: one-click reversible local test in the existing Telegram bot, monochrome UI.
Architecture: existing bot/gateway preserved. Temporarily pause the old polling bot;
run a pinned candidate image with a private copy of its history, same gateway and
server-side test-owner allowlist. Cloudflare Quick Tunnel exposes only the candidate
Mini App HTTP listener, never gateway/admin. Global Telegram menu is not changed.
Spec: owner's approved Mini App and request for safe local testing + website palette.

- [ ] TDD: owner-only authorization in polling and signed Mini App requests, scoped menu.
- [ ] Use website background #0c0d0f / muted #919295; grayscale surfaces, white actions;
      Telegram theme must not override the selected palette.
- [ ] Standalone stdlib Windows launcher: preflight/download before old bot stops,
      snapshot clone, HTTPS tunnel, isolated candidate, readiness, Start/Stop recovery.
- [ ] Build immutable test release only on feature branch; normal preview/main untouched.
- [ ] Run relevant/full tests, real Chromium/HTTP, Docker orchestration smoke and Windows.
- [ ] Provide two-click start/stop and explicit limits; no live paid call automatically.

Constraints: no secrets in archive/logs/Git; no open admin ports; no repeated paid request;
no direct local machine access claimed. API quota and spending on intentionally sent
prompts remain real. Trial dialog changes stay in the trial copy. Tunnel is third-party
TLS termination; not E2EE, not production. Stop restores original bot/menu. Desktop
security hold and web branch are out of scope. Owner ID is local config, not repo default.
