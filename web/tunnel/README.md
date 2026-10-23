# NoClip: временный туннель к локальному шлюзу

Сайт остаётся на Vercel, шлюз работает на ПК. Этот туннель предназначен только для разработки. При выключении ngrok или шлюза запросы недоступны.

## Что потребуется сделать на ПК Windows

1. Создать бесплатный аккаунт ngrok, установить агент: https://ngrok.com/download/windows . Выполнить ngrok config add-authtoken с токеном из личного кабинета ngrok. Токены не передавать в чат.
2. Запустить шлюз обычным способом. Он должен реализовать POST /v1/web/session, POST /v1/web/chat/completions и GET /v1/web/me. В более раннем backend из main этих маршрутов нет: нужна твоя рабочая ветка с web-access. Для реального ответа также нужны включённый SHLUZ_WEB_ENABLED, допустимый Origin https://noclip-web.vercel.app, серверные ключи Turnstile и настроенная демо-модель.
3. Открыть PowerShell в корне shluz и выполнить: powershell -ExecutionPolicy Bypass -File web/tunnel/start-windows.ps1 -GatewayPort 8000 . Если шлюз на другом порту, заменить 8000. Скрипт запускает только туннель и создаёт случайный пароль в %LOCALAPPDATA%\NoClipTunnel, вне GitHub.
4. В существующем проекте Vercel noclip-web открыть Environment Variables (Production) и задать три серверные переменные: SHLUZ_BRIDGE_ORIGIN = HTTPS forwarding URL, который покажет ngrok; SHLUZ_BRIDGE_USERNAME = noclip; SHLUZ_BRIDGE_PASSWORD = пароль, который покажет PowerShell. Последнюю пометить Sensitive. Не публиковать пароль.
5. Проверить, что /admin на прямом адресе ngrok заблокирован и что без Basic Auth гостевые маршруты отклоняются. До реального сквозного теста с валидной сессией, cookie/CSRF и потоковым ответом оставить NEXT_PUBLIC_GATEWAY_ENABLED=false. После проверки можно переключить на true и выполнить один redeploy Vercel. До этого публичная генерация намеренно отключена.

## Безопасность и последующий перенос

На стороне ngrok Traffic Policy сначала ограничивает методы и пути, затем требует Basic Auth. Аналогичное ограничение действует в серверном посреднике Vercel. Админка, Telegram, OTP и другие API не открываются. Секрет отправляется только сервером Vercel; браузер работает через тот же origin /v1/web/*, а ответы SSE идут потоком.

Backend по-прежнему обязан проверять Origin, CSRF, Turnstile, права и финансовые лимиты. Перед приглашением сторонних тестировщиков отдельно проверить передачу реального IP через цепочку прокси и правила anti-abuse — сейчас нельзя автоматически считать IP от ngrok адресом конечного клиента.

Когда шлюз переедет на VPS, браузерный API не изменится. Надо будет перенастроить защищённое подключение и перепроверить cookie, SSE и безопасность.

Документация: https://ngrok.com/docs/universal-gateway/examples/ollama и https://vercel.com/docs/routing/rewrites .
