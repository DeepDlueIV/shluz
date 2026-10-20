# Telegram Login на сайте — дополнение web-access-v1.1

Серверная реализация официальной Telegram Login JS library. Не старый iframe widget,
не ручная ссылка на /start и не Mini App initData. Источник:
https://core.telegram.org/bots/telegram-login (проверен 20.10.2026).

## Включение и границы

`SHLUZ_WEB_TELEGRAM_CLIENT_ID` — Client ID из раздела Login Widget в BotFather.
Origins сайта должны быть добавлены в Allowed URLs этого же приложения Telegram.
Обычный Bot API token не является Client ID/Client Secret OIDC и не попадает в браузер.
В выбранном JS popup-потоке сервер только проверяет ID-token; обмен authorization
code и Client Secret не нужны. RS256 должен оставаться алгоритмом приложения в BotFather.
При другом алгоритме этот сервер откажет, а не ослабит проверку подписи.

Пустой Client ID отключает вход через Telegram; общий SHLUZ_WEB_ENABLED по умолчанию
по-прежнему false. Наличие Client ID в панели означает настройку, а не живую проверку.
Настоящая авторизация требует домена/Allowed URLs/доступа к Telegram. Тесты не заменяют
подтверждение реальным пользователем. SMTP нужен только email-входу, не Telegram Login.

## Контракт сайта

Сайт сначала создаёт `/v1/web/session` и хранит CSRF в памяти страницы, как описано
в `web-access-and-abuse.md`. Каждый POST: разрешённый Origin, JSON, cookie с
credentials:include и X-CSRF-Token. Не передавать токены в URL, логи, аналитику или localStorage.

1. `POST /v1/web/auth/telegram/start` с `{}` возвращает
   `challenge_id`, `client_id`, `nonce`, `scope:["profile"]`, `expires_in:300`.
2. Передать client_id, scope, nonce официальной JS-библиотеке Telegram.Login.auth/open.
   Popup должен открываться по клику пользователя; UI обработает отмену/блокировку popup.
   Для обмена сообщениями с popup не ставить COOP:same-origin; официальный вариант —
   same-origin-allow-popups. Это настройка frontend, не отключение серверного CSRF.
3. Callback библиотеки возвращает id_token. Не доверять отдельно возвращённому user.
   `POST /v1/web/auth/telegram/verify`:
   `{"challenge_id":"...","id_token":"..."}`.
4. Сервер проверяет RS256 по фиксированному JWKS Telegram, issuer, audience, exp/iat,
   nonce и профильный id. Nonce одноразовый и привязан к текущей браузерной сессии.
5. Успех возвращает `{authenticated:true,account_id,csrf_token}` и меняет HttpOnly
   cookie. Сайт заменяет CSRF новым значением и перечитывает `/v1/web/me`.
   Выход — прежний `/v1/web/logout`.

Подмена user_id/name без подписи никогда не является входом. `id` из проверенного
profile соответствует Telegram Bot API; `sub` OIDC непрозрачный и НЕ используется
вместо Telegram ID. Телефон, фото и разрешение на сообщения не запрашиваются.

## Данные и права

Существующая Identity(provider=telegram, subject=id) ведёт в тот же Account, что бот.
Имя, подписка, денежные данные и ранее использованная квота не перезаписываются.
Новый Telegram превращает активный guest Account в обычный либо создаёт отдельный
Account без склейки с email. Уже потраченное гостевое демо переносится в целевой
Telegram-аккаунт, а отдельный старый guest закрывается. Во время активной гостевой
генерации возвращается 409: не переносим ещё не завершённый расход между аккаунтами.
Один Start бота после веб-входа находит эту же Identity, не выдаёт второй пакет.

Вход не меняет TelegramAdmission: блокировка канала бота не снимается. Inactive и
служебный Account не получают веб-сессию. Ограничения моделей остаются пересечением
тарифа и web-канала. Вход не означает оплату. Связывание двух существующих email↔Telegram
аккаунтов/кошельков — отдельная будущая подтверждённая операция.

## Защита и ошибки

Срок challenge — 5 минут, до 5 проверок; новый /start инвалидирует прежний challenge
этой сессии. Счётчики создания/проверки ограничены по сети и глобально в SQL; используются
существующие настраиваемые лимиты частоты подтверждения входа. Сам Telegram Login не
требует Turnstile, но последующее демо по-прежнему обязательно проходит антибот-проверку.

JWKS: только https://oauth.telegram.org/.well-known/jwks.json, TLS проверяется, редиректы
не выполняются, 64 КиБ, до 16 ключей; cache 5 минут, refresh не чаще 30 секунд на процесс.
Неизвестный kid не используется как URL. Network/JSON/error не пропускают пользователя.
В БД nonce/challenge сохраняются как HMAC, JWT/профиль/ключи в логах отсутствуют.

`503 telegram_login_not_configured`, `503 telegram_login_unavailable`,
`401 telegram_login_invalid`, `401 telegram_login_expired`, `409 demo_in_progress`;
прежние Origin/CSRF/сессия/лимиты сохраняются. При 401 начать вход снова, не бесконечно
повторять verify. Успешное погашение и ротация выполняются одной короткой транзакцией.
PostgreSQL lock order: сначала Telegram identity (1397247034), затем квоты (1397247035).
Сеть не выполняется под блокировкой. Nonce убираются существующим TTL maintenance.

Миграция 20261020_10 создаёт только таблицу временных challenge и индексы. История,
аккаунты, тарифы, лимиты и старые токены не удаляются.
