# NoClip: защищённый туннель сайта к локальному Gateway

Сайт остаётся в существующем проекте Vercel, Gateway — на Windows-ПК. HTTPS Mini App (`cloudflared`) работает отдельно и этими командами не управляется.

## Подготовленная portable-установка

- `Start-Website.cmd` в корне установки запускает **существующий** `noclip-site-gateway` и ngrok скрыто. Повторный запуск проверяет уже работающий агент и сохраняет его HTTPS URL, не создавая вторую установку.
- `Stop-Website.cmd` останавливает только проверенный website-агент и его Gateway. Отсутствие/повреждение `state.json` не мешает остановке. Контейнеры, тома, история и секреты не удаляются; основной Gateway, Telegram и Cloudflare не затрагиваются.
- Локальные настройки — `config/site-bridge.json`: `port`, `configPath`, `trafficPolicy`. Пути конфигурации и policy должны находиться в одном закрытом каталоге внутри `config/`, вне MSIX-виртуализации AppData. Установку и credentials готовит агент; при их отсутствии launcher отказывает, не создавая сервисы и ключи самостоятельно.
- `config/site-bridge/state.json` содержит только PID, HTTPS URL и пути. Это диагностическая запись, а не доказательство принадлежности процесса: перед остановкой проверяются фактический executable ngrok и его точные параметры config/policy/upstream.

## Граница безопасности

Используется исходный `start-windows.ps1`: сначала deny всех маршрутов, кроме `POST /v1/web/session`, `POST /v1/web/chat/completions`, `GET /v1/web/me`, затем обязательный Basic Auth. На стороне Vercel требуется такая же точная allowlist. Admin, Telegram, OTP, внутренние API не публикуются.

Скрипт поддерживает `RuntimeDirectory`, `NgrokConfig`, `GatewayPort` и `PrepareOnly`. Последний только готовит policy без запуска туннеля. ACL runtime и файлов ограничены владельцем и SYSTEM; пароль не печатается. HTTP Inspector ngrok отключён (`--inspect=false`). Файлы config, policy, password и логи нельзя отправлять в Git или чат.

Launcher проверяет label, закреплённый image ID/revision, единственный loopback port binding и реальные гостевые маршруты Gateway. Чужой процесс на порту ngrok 4040 не останавливается; запуск завершается отказом.

## Vercel и живые проверки

Серверные переменные существующего проекта: `SHLUZ_BRIDGE_ORIGIN`, `SHLUZ_BRIDGE_USERNAME`, `SHLUZ_BRIDGE_PASSWORD` (Secret). В браузер пароль не передаётся. Launcher не меняет Vercel, `NEXT_PUBLIC_GATEWAY_ENABLED`, тарифы или провайдера. После изменения env нужен согласованный deployment существующего проекта; не создавать новый проект.

До публичного доступа нужны проверенные Turnstile, Origin/CSRF, cookie, реальные SSE-ответы разрешённой модели, отключение/повторный запуск и доверенная IP-цепочка. `NEXT_PUBLIC_GATEWAY_ENABLED=false` закрывает и интерфейс, и серверный bridge; проверку выполнять в защищённом согласованном контуре. Не считать mock-ответ работой настоящей модели и не включать расходы автоматически.

При остановке ПК, Docker или ngrok сайт теряет доступ к Gateway. После нового запуска адрес HTTPS надо сверить с Vercel; автоматического обновления env нет.
