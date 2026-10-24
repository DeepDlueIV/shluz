# Website NoClip: запуск с Windows

**Сайт:** https://noclip-web.vercel.app

- **`D:\NoClip\Shluz\Start-Website.cmd`** — запускает существующий локальный Gateway сайта и защищённый ngrok. Повторный запуск кратко перезапускает только свой ngrok, заново проверяя policy и права файлов; второй агент не создаётся.
- **`D:\NoClip\Shluz\Stop-Website.cmd`** — останавливает только website Gateway и его ngrok, в том числе без `state.json`. Контейнеры, тома, история и конфигурация не удаляются.
- `NoClip.cmd` — отдельный запуск основного Gateway и Telegram Mini App. Cloudflare HTTPS для Mini App не связан с website-ngrok и этими двумя командами не управляется.

## Текущее ограничение

Публичное гостевое демо остаётся закрытым до разрешённого настоящего E2E: Vercel → защищённый HTTPS → SHLUZ → Turnstile → настоящая модель/SSE, включая проверку IP-цепочки и отключения сервера. Mock не доказывает работу модели. Launcher не включает публичный флаг, не меняет Vercel и не разрешает расходы автоматически.

## Что хранится локально

`config/site-bridge.json` указывает порт и канонические `config/site-bridge/ngrok.yml`, `config/site-bridge/traffic-policy.yml`. Credentials настраивает агент; при их отсутствии новая установка/ключи автоматически не создаются. `config/site-bridge/state.json` содержит только PID, HTTPS URL и пути. Наличие state не доказывает принадлежность процесса: перед остановкой проверяются actual executable и точные config/policy/upstream.

Существующий `web/tunnel/start-windows.ps1` создаёт точную allowlist из трёх маршрутов: `POST /v1/web/session`, `POST /v1/web/chat/completions`, `GET /v1/web/me`; остальные запросы запрещает и требует Basic Auth. Inspector выключен. Пароль в консоль не выводится; DACL credentials/policy — текущий владелец и SYSTEM. Файлы `config/`, password, policy и логи нельзя передавать в Git/чат.

Launcher проверяет label, закреплённый image ID/revision, единственный loopback port binding и гостевые маршруты уже существующего `noclip-site-gateway`. Чужой агент на 4040 не останавливается — запуск отклоняется. Перед повторным запуском своего ngrok он останавливается, policy/ACL готовятся заново; при ошибке небезопасный старый агент не остаётся работать.

## Vercel

Используется существующий проект `noclip-web`. Серверные env: `SHLUZ_BRIDGE_ORIGIN`, `SHLUZ_BRIDGE_USERNAME`, `SHLUZ_BRIDGE_PASSWORD` (Secret). После изменения env нужен согласованный deployment; новый проект не создаётся. При `NEXT_PUBLIC_GATEWAY_ENABLED=false` закрыты и интерфейс, и bridge: проверки выполнять в защищённом согласованном контуре.

После выключения ПК, Docker или ngrok HTTPS недоступен. URL после нового запуска надо сверить с Vercel; автоматического изменения env нет.
