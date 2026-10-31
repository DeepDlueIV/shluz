# Runbook: подключение отправки OTP через Resend (бесплатный тариф)

Код входа на сайте отправляет шлюз (`SmtpMailer`, SMTP_SSL + логин).
Провайдер: **Resend** — бесплатный тариф 100 писем/день и 3000/мес
(для OTP этого достаточно). Альтернатива — sender.net (15 000/мес),
совместим так же, но доки проще у Resend. Ссылки:
- https://resend.com/docs/send-with-smtp
- https://resend.com/pricing

## Шаг 1. Аккаунт Resend (владелец, ~2 минуты)
1. https://resend.com → Sign up (email + пароль).
2. Подтвердить email.

## Шаг 2. Домен (владелец, ~3 минуты + ожидание DNS)
1. Dashboard → Domains → **Add Domain** → `toscanopersonainc.online`
   (регион оставляем по умолчанию).
2. Resend покажет DNS-записи (DKIM TXT `resend._domainkey`, TXT/SPF на
   поддомене `send`, опционально DMARC). Скопировать их мне — я добавлю
   в Cloudflare (или добавить самому: Cloudflare → DNS → Add record,
   proxy выключен для этих записей).
3. Дождаться статуса **Verified** (обычно минуты; DNS на зоне уже активен).

Важно: записи Resend живут на ПОДДОМЕНАХ — конфликт с почтой Zoho
(MX/SPF на корне) исключён; корневой SPF Zoho не трогаем.

## Шаг 3. API-ключ (владелец)
Dashboard → API Keys → Create → права Sending access. Скопировать ключ
`re_…` и передать агенту (или вставить самому в Шаг 5).

## Шаг 4. Перенастройка site-gateway (агент, ~2 минуты)
Добавить в env контейнера noclip-site-gateway и пересоздать:
```
SHLUZ_WEB_SMTP_HOST=smtp.resend.com
SHLUZ_WEB_SMTP_PORT=465
SHLUZ_WEB_SMTP_SSL=true
SHLUZ_WEB_SMTP_USERNAME=resend
SHLUZ_WEB_SMTP_PASSWORD=re_…
SHLUZ_WEB_SMTP_SENDER=NoClip <no-reply@toscanopersonainc.online>
```
(См. текущую процедуру в памяти проекта: пересоздание site-gateway
с bind-mount БД.)

## Шаг 5. Проверка (агент)
1. /login → ввести почту → письмо с 6-значным кодом.
2. Код → вход → сессия авторизована.
3. Ограничения Resend бесплатно: 100/день — при росте добавить платный
   план или второй провайдер.

## Откат
Убрать SMTP_* env и пересоздать контейнер — вход вернётся в состояние
«письма не подключены».
