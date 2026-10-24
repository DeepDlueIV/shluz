# Туннель сайта и туннель Telegram Mini App

Это два независимых подключения:

- **Telegram Mini App:** cloudflared → HTTP-панель Mini App на 8081 внутри Docker. Запускается NoClip.cmd. Gateway/admin через этот адрес не публикуются.
- **Сайт Vercel:** серверный bridge сайта → HTTPS ngrok с Basic Auth и allowlist → гостевой SHLUZ /v1/web/*. Его нельзя направлять на Mini App или открывать весь Gateway.

Реализация web-bridge уже находится в main: `web/tunnel/README.md`, `web/tunnel/start-windows.ps1`, `web/src/lib/bridge-proxy.ts`. Текущая ветка portable исправляет PR #35 и не объединяет серверные или web-ветки.

## Локальное состояние

Подготовленные приватные настройки лежат в `config/site-bridge.json` и `config/site-bridge/`. Они намеренно не входят в Git. На Windows пути конфигурации ngrok вне AppData доступны MSIX-агенту. Права каталога ограничены владельцем и SYSTEM.

Сайт-туннель выключен и автоматически с NoClip.cmd не запускается. Прежний отдельный гостевой тестовый backend на 18001 остановлен. Работающий основной Gateway на 18000 пока не реализует /v1/web/*; подключать к нему сайт напрямую нельзя.

## Безопасное включение — отдельный этап

1. Подготовить согласованный гостевой backend и проверить /v1/web/*, Turnstile и ограничения модели без изменения пользовательских БД/расходов.
2. Запустить существующий ngrok-скрипт с policy: только POST session, POST chat/completions, GET me; остальные методы/пути закрыты, Basic Auth обязателен. Не выводить пароль в консоль/чат и отключить Inspector.
3. Проверить HTTPS, запрет admin/internal, cookie/CSRF, SSE, IP-цепочку и выключение backend.
4. Обновить server-only SHLUZ_BRIDGE_ORIGIN/USERNAME/PASSWORD существующего Vercel-проекта; пароль — Secret. Проверить защищённый контур и только затем согласовать публичное включение и необходимый redeploy.

Не включать NEXT_PUBLIC_GATEWAY_ENABLED автоматически. Секреты, runtime JSON, базы, backups и сырые диагностические traces не публиковать. Этот документ не включает аккаунты, токены, персональные данные или действующие внешние URL.
