# Задача для разработчика NoClip Desktop (форк): авторизация через сайт

## Контекст
Сайт https://noclip-web.vercel.app поддерживает вход по email-OTP и выдачу
персональных API-токенов из кабинета (/tokens), а также authorize-флоу для
настольных приложений: /authorize?client=<id>&port=<локальный порт>.

## Что реализует Desktop
1. Пользователь нажимает кнопку «Войти через сайт» в Desktop.
2. Desktop запускает локальный HTTP-сервер на 127.0.0.1:<случайный порт
   1024–65535> и открывает в системном браузере:
   `https://noclip-web.vercel.app/authorize?client=noclip-desktop&port=<порт>`
3. Пользователь логинится на сайте (email-OTP) и нажимает «Разрешить».
4. Сайт делает редирект на `http://127.0.0.1:<порт>/callback?code=<код>`.
   Код одноразовый, живёт 60 секунд.
5. Desktop принимает callback, затем ОБМЕНИВАЕТ код на токен:
   `POST https://noclip-web.vercel.app/v1/web/token`
   Content-Type: application/json, body: {"code": "<код>"}
   Ответ: {"raw_token": "shluz_…", "name": "NoClip Desktop", "token_prefix": "shluz_Xx"}
6. Токен хранится Desktop'ом локально и используется как
   `Authorization: Bearer shluz_…` для OpenAI-совместимых вызовов
   (/v1/chat/completions, /v1/models — с capabilities моделей).
   Обмен работает и через локальный шлюз 127.0.0.1:18000 (эндпоинт тот же).

## Безопасность
- Код одноразовый и привязан к аккаунту; обмен без CSRF (нет браузерной сессии).
- Desktop не сохраняет пароли — только токен; отзыв в кабинете /tokens.
- Не логировать raw_token.

## Endpoint'ы (контракт)
- GET  /authorize?client=noclip-desktop&port=NNNN — браузерная consent-страница
- POST /v1/web/token {"code": "..."} → 200 {"raw_token": "shluz_…"} | 400 {"error": {...}}
- Ошибки — OpenAI-стиль {"error": {"code", "message", ...}}; коды: code_invalid (перепросить код)

## Статус у владельца
Страницы /authorize и /tokens уже в проде (main). Требуется только реализация
Desktop-стороны (шаги 2, 4-6).
