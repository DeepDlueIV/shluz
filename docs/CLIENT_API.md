# Контракт клиентов Shluz — индекс

Версия веб-контракта: **web-access-v1.1, 20.10.2026**.

Это начальная точка для агента сайта. Прочитать до реализации API-запросов.
Сайт живёт в `web/`, gateway в `app/`, Telegram в `telegram_bot/`. Один серверный
учёт тарифов, демо и расходов. Браузер не получает административный, bootstrap,
служебный Telegram или Venice-ключ.

| Документ | Назначение и статус |
|---|---|
| [telegram-web-login.md](telegram-web-login.md) | Официальный Telegram Login через JS library и серверный RS256/nonce; требует Client ID и Allowed URLs |
| [web-access-and-abuse.md](web-access-and-abuse.md) | Реализованные cookie-сессии, гостевой демо-чат, OTP, CORS/CSRF, Turnstile и ограничения |
| [client-api-demo.md](client-api-demo.md) | Существующий Bearer API профиля, моделей, тарифов и расхода; используется desktop/ботом, не публичным гостем |
| [streaming-and-metrics.md](streaming-and-metrics.md) | SSE, `[DONE]`, ошибки, отмена, неопределённый usage и метрики |

Новые guest/OTP routes описаны ниже и присутствуют в `/openapi.json`.
Наличие маршрутов НЕ означает, что подключены домен, SMTP, настоящий Turnstile
или оплаченный поставщик. До настройки `SHLUZ_WEB_ENABLED` они отвечают 503.
Тестовые adapters доступны только при программном создании тестового приложения,
не через публичный endpoint или query parameter.

## Согласование агентов

Агент сайта читает этот индекс и использует `/v1/web/*` с `credentials:include`.
Перед изменением маршрута/DTO нужно обновить этот контракт и контрактные тесты в
`tests/webaccess/`, не создавать независимую реализацию биллинга в браузере.
Изменения API добавляются обратно совместимо; старые маршруты клиентов не удалять.
Рабочие ветки могут различаться. Сверять версию контракта и опубликованный commit,
а не считать имя ветки гарантией актуальности. Разработку интерфейса на заглушках
явно отличать от проверки настоящего gateway и доставки писем.

Официальный Telegram Login сервера реализован дополнением v1.1. Связывание уже
существующих email- и Telegram-аккаунтов, frontend и платёжный API не реализованы.

Перед публичным запуском: [GO_LIVE_CHECKLIST.md](GO_LIVE_CHECKLIST.md).
Это отложенные подключения; тесты и наличие API не означают их настройки.

## Дополнение 20.10.2026 — wallet-v1 и путь гостя

[wallet-and-access.md](wallet-and-access.md): optional PAYG, общий кошелёк,
представление USD/credits, назначение тарифов каналов и новые read-only wallet routes.
`/plans` добавляет billing_mode; у PAYG цена в месяц null, не рисовать её как подписку.
[guest-dashboard-journey.md](guest-dashboard-journey.md): первый prompt с главной
переводит в гостевой кабинет, один SSE-запрос; OTP превращает гостя без второго демо.
[venice-pricing-and-wallet.md](venice-pricing-and-wallet.md): проверенные внешние
источники, старый billing/usage нельзя использовать как действующий.
Frontend в web/ не изменён этим блоком. Публичный доступ и настоящие оплаты выключены.

## Экспериментальная панель Telegram — miniapp-v1, 23.10.2026

Отдельная ветка `feat/telegram-miniapp-control`, не текущий preview.
[telegram-miniapp.md](telegram-miniapp.md): управление ботом через MenuButtonWebApp
без сообщений навигации в чате. Это не login сайта: проверяется Mini App initData
подписью бота. `/miniapp/api/*` обслуживает процесс Telegram с общей историей;
клиент получает лишь короткую panel-сессию. Сайт/desktop по-прежнему используют свои
действующие контракты; miniapp endpoint не является новым маршрутом генерации.
