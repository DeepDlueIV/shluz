# Передача облачному разработчику: NoClip / SHLUZ

Состояние: 24 октября 2026, Asia/Novosibirsk. Репозиторий: https://github.com/DeepDlueIV/shluz.
Задача владельца: запустить Telegram-бота/новую Mini App на Windows, отдельно туннелировать сайт на Vercel к локальному SHLUZ, подключить Venice и получить настоящие ответы. Этот документ содержит контекст продолжения; секреты и пользовательские данные в GitHub не загружены.

## 1. Текущий этап — что действительно получилось

| Контур | Подтверждено | Осталось |
|---|---|---|
| Windows portable | Единая рабочая папка D:\NoClip\Shluz; исправлены start/stop/restart, сохранены данные и резервные копии | Полная повторная живая проверка намеренного preflight-сбоя финальной версии; админка автоматически не открывается |
| Telegram Mini App | Запускается вместе с Gateway; отдельный HTTPS Cloudflare; меню Telegram проверено; Mini App открывалась в настоящем Telegram, показывала профиль и 16 прежних диалогов | Новый действующий ключ Venice подключён только сайту. Реальный ответ Venice в Telegram в этой работе НЕ подтверждён |
| Основной сайт | https://noclip-web.vercel.app/app реально получил ответ Venice через Vercel → ngrok → локальный гостевой SHLUZ | Проверенная IP-цепочка перед расширением нагрузки; остальной продуктовый go-live остаётся отдельной задачей |
| Публичное демо сайта | Включено после явного согласия владельца с небольшими пределами | Не расширять лимиты/модели/платежи без согласования |

Сайт сейчас НЕ использует mock: действующий provider=venice, модель mistral-small-3-2-24b-instruct. Смена модели на Mistral — контрольная проверка нормального ответа, а не перенос Telegram-профилей на другую модель.

## 2. Ветки и порядок чтения

Все изменения опубликованы в GitHub, main автоматически НЕ объединялся.

| Работа | Ветка / коммит реализации | PR и база |
|---|---|---|
| Исходный portable launcher | feat/unified-portable-control-20261024 / 77261fc8069ef5d0621fdbe4356d0a9e3cbb6e0e | #35 |
| Исправления Windows/Telegram/Cloudflare | fix/windows-portable-live-20261024 / f47c4738dfe14a4e2ceaf320f0ebcbe87bd670ab | https://github.com/DeepDlueIV/shluz/pull/36 → ветка PR35 |
| Запуск отдельного сайта-туннеля | feat/windows-site-tunnel-20261024 / e2f26d09d47dcf5520f8950a0d64a20aeac3b5cf перед добавлением этого отчёта | https://github.com/DeepDlueIV/shluz/pull/37 → fix/windows-portable-live-20261024 |
| Исправление frontend Turnstile и скрытие счётчика | test/site-bridge-protected-20261024 / fd58ca705832a9095ac7753693479d7922cf05cb | https://github.com/DeepDlueIV/shluz/pull/38 → main |
| main на момент передачи | 3035e66dcbc246fc2fc266dd70eae6ca780fd28a | Не содержит исправления Turnstile из PR38 |

**Критично:** работающий Production опубликован напрямую из fd58ca7 отдельной frontend-ветки. Старый main ещё содержит дефект Turnstile. Следующая автоматическая публикация старого main может вернуть этот дефект. Сначала проверить PR38 и согласовать интеграцию; не считать успешный Production доказательством слияния main. PR36/37 образуют отдельную зависимую цепочку, не направлять её целиком в main без анализа серверных различий.

Сначала прочитать AGENTS.md, этот отчёт, docs/site-tunnel-status.md и docs/GO_LIVE_CHECKLIST.md. Старые запреты в документации про полностью выключенный сайт были частично переопределены владельцем 24.10: разрешены настройка Turnstile, новые локальные credentials, реальные короткие проверки Venice и ограниченное публичное демо. Это НЕ разрешение на платежи, новый VPS, платные тарифы или неограниченный каталог.

## 3. Архитектура — два независимых туннеля

```text
Telegram → HTTPS cloudflared → новая Mini App → основной Gateway :18000

Браузер → noclip-web.vercel.app → server-only bridge Vercel
        → HTTPS ngrok + Basic Auth + allowlist
        → отдельный гостевой Gateway 127.0.0.1:18001 → Venice
```

Основной Gateway был старой версией без нужных /v1/web/*; его не заменяли серверной веткой. Для сайта использован ранее согласованный отдельный гостевой экземпляр с отдельным существующим томом. Это сознательная изоляция, а не готовая унификация пользователей/истории Telegram и сайта.

### Работающие контейнеры при свежей сверке

- shluz-owner-check-shluz-1: основной Gateway, healthy, только 127.0.0.1:18000 → 8000.
- noclip-miniapp: новая Telegram Mini App, healthy, прежняя Telegram-история.
- noclip-miniapp-https: cloudflared только для Mini App.
- noclip-site-gateway: сайт, healthy, только 127.0.0.1:18001 → 8000.
- ngrok сайта — отдельный процесс Windows, не cloudflared-контейнер. Inspector выключен.

Гостевой сервер сайта: image noclip-web-guest-test:0814540; полный image ID sha256:d681c163ed411b2e82df6e4d67a83faf44019253da9fb5c938b997fcff6bd50b; source 08145402383f681f2843e325f054af3d50dac1e0. Это реальная работающая серверная версия, а не текущий main. Перед изменением жёстко закреплённого image в launcher/site_bridge.py отдельно проверить совместимость и воспроизводимость образа в облаке.
Mini App image ID: sha256:9b55df5ff971aac89fd3dd2b8885bcd69c8eb29c457e9c607096440dfc62c612; ранее подготовленный preview c47ff5b8acc4a7b24b8f34b19e3a9d024aba0fea. Тестирование launcher-кода не означает, что контейнеры были пересобраны из его HEAD.

### Файлы запуска на компьютере владельца

- D:\NoClip\Shluz\NoClip.cmd: основной Gateway + новая Telegram Mini App + её HTTPS. Старый classic-бот — только явный rollback.
- Stop-NoClip.cmd: остановка управляемого Telegram-контура, в том числе после неполного запуска/без state.
- Start-Website.cmd: отдельный гостевой Gateway + ngrok; после успешного запуска без аргументов открывает https://noclip-web.vercel.app.
- Stop-Website.cmd: останавливает только сайт-туннель и его Gateway, не Telegram.
- Backup-NoClip.cmd: штатная резервная копия.
- Локальная админка: http://127.0.0.1:18000/admin; 401 без авторизации ожидаем. Её автоматическое открытие пока не реализовано.

Работоспособность локального сайта зависит от включённого Windows-компьютера, Docker Desktop, сети и ngrok. Сайт остаётся на Vercel при выключении компьютера, но генерация недоступна. Облачный агент не имеет автоматически доступа к Windows/Docker, ключам или локальным БД; не выдавать облачные unit-тесты за живую Windows-проверку.

## 4. Что исправлено в коде

### Portable / Telegram (PR36)

- Stop обнаруживает управляемые сервисы независимо от отсутствующего/повреждённого state.json; очистка после ошибок охватывает preflight.
- Classic-бот не включается как скрытый fallback.
- Проверяются существующие образы вместо лишних копий архивов/старых установок; прежние launcher CMD перенаправлены на единую рабочую папку.
- Подтверждается фактическое обновление Telegram menu URL; ограничены таймауты проверки.
- Причина сетевой ошибки установлена: принудительный Cloudflare HTTP/2 получал TLS handshake EOF; UDP/QUIC работал. protocol auto позволил зарегистрировать QUIC. VPN, DNS, TLS-защита и Telegram initData не отключались.

### Сайт-туннель (PR37)

- Добавлены Start-Website.cmd / Stop-Website.cmd и launcher/site_bridge.py; переиспользуется подготовленный web/tunnel/start-windows.ps1.
- Gateway проверяется по label, image ID, source revision и loopback-привязке порта.
- Разрешены только канонические пути policy/config. Перед стартом перезапускается только подтверждённый собственный ngrok, заново готовятся policy/ACL. Простое совпадение argv не считается доказательством безопасной уже загруженной policy.
- ACL credentials ограничиваются владельцем и SYSTEM; ссылки отклоняются. Секреты не печатаются, Inspector выключен.
- Stop не удаляет контейнеры/тома; работает без state.json. Корневые CMD имеют CRLF без BOM, закреплено .gitattributes. Ошибка разбора обрывков CMD воспроизведена и исправлена настоящим cmd.exe.
- Успешный default-start открывает настоящий сайт; stop и неудачный start браузер не открывают.

### Frontend (PR38)

- Реальный Cloudflare API выбрасывал ошибку при turnstile.ready() внутри async script.onload. Теперь loaded API возвращается из onload, без отключения Turnstile.
- Число оставшихся бесплатных запросов убрано одновременно EN/RU. Внутренний учёт и блокировка при исчерпании сохранены.

## 5. Доказательства настоящего Venice

Старый ключ из работающего owner Gateway и актуального config/.env.owner совпадал, но GET /api_keys/rate_limits вернул401; формат/пробелы/кавычки причиной не были. POST с ним не отправлялся. Владелец ввёл новый ключ в локальное защищённое окно. Бесплатная проверка нового ключа: HTTP200, accessPermitted=true. Новый ключ используется только гостевым Gateway сайта. Старый ключ owner Gateway/Telegram НЕ заменён — это главный незакрытый пункт для Telegram.

Проведено три реальных вызова; provider=venice, streamed=true, usage_known=true, cost_known=true, status=success:

| Где / модель | Результат | Input / output | Длительность | Расход USD |
|---|---|---|---|---|
| Закрытый preview, olafangensan-glm-4.7-flash-heretic | Только «Г»: 512 токенов ушли преимущественно на reasoning; полноценный ответ НЕ засчитан | 1503 / 512 | 7.912с | 0.000257825 |
| Закрытый preview, mistral-small-3-2-24b-instruct | «Готово» | 1601 / 4 | 1.188с | 0.00015109375 |
| **Основной noclip-web.vercel.app**, Mistral | **«Сумма 17 и 25 равна 42.»** | 1613 / 17 | 2.223с | 0.00015546875 |

Всего $0.0005643875. Записи расходов, лимитов и истории не обнулялись. Для второго контрольного вызова увеличен лимит только собственного preview-тестового аккаунта; новые гости получают один запрос. Повторные платные запросы ради этого отчёта не выполнялись.

Production: dpl_6fs6xXA3VGmyGwCSa7AoQv8cdiCh, READY, source fd58ca705832a9095ac7753693479d7922cf05cb. Существующий Vercel projectId: prj_IMSWjsl9RU0e8mV1RXv7QyLz0VJD, имя noclip-web. Новый проект не создавался. В этой сессии Vercel connector работал без явного teamId; добавление teamId ранее давало403 — не сбрасывать OAuth вслепую.

## 6. Публичные пределы и безопасность

Владелец явно согласовал: один запрос на нового гостя; максимум10 запросов на весь сайт в сутки; общий денежный предохранитель $0.02/сутки; один параллельный вызов; ввод до1000 символов; вывод до512 токенов; только Mistral. Это небольшой согласованный demo, не завершённый продуктовый запуск.

- Basic Auth хранится только на сервере Vercel и локально. Ключ Venice не передаётся браузеру и не хранится в Vercel frontend env.
- Allowlist туннеля: POST /v1/web/session; POST /v1/web/chat/completions; GET /v1/web/me. Всё остальное закрыто.
- /admin, /health, /openapi.json, /v1/chat/completions, OTP и неверные методы вернули403 через ngrok даже с корректным bridge Basic Auth. Guest без Basic Auth401.
- Cookie Secure + HttpOnly + SameSite=strict; /session и /me200; чужой Origin403; нет CSRF403; нет Turnstile403. Настоящий Turnstile прошёл серверную проверку, реальные SSE дошли до браузера.
- Turnstile Managed, pre-clearanceOFF, только точные hostname: noclip-web.vercel.app и защищённая тестовая ветка noclip-web-git-test-site-bridg-055918-deepdlueiv-7467s-projects.vercel.app. Замена тестового hostname согласована отдельно.
- Production flag включён с согласия владельца; общий preview-default остаётся false, отдельная закрытая тестовая ветка имеет свой true override.
- При остановке upstream ngrok502, сайт сообщает недоступность и сохраняет введённый текст; сервис восстановлен. Самостоятельный повтор POST не включался.

**Ограничение IP:** trusted_proxies=[] игнорирует поддельный XFF, но backend видит локальный Docker peer вместо посетителя. Все делят сетевой bucket; его предел10 совпадает с согласованным глобальным лимитом. Реальные CAPTCHA при этом прошли, однако корректная передача IP не реализована. Перед расширением доступа: валидированный Vercel client IP → аутентифицированный служебный заголовок → проверенный ngrok rewrite → доверие только конкретному peer /32; обязательны spoofing-negative tests. Не доверять всем прокси и не принимать произвольный браузерный XFF.

## 7. Данные и резервные копии — только локально

Ничего из перечисленного ниже не публиковать в GitHub и не переносить в облако автоматически.

| Данные | Где находятся |
|---|---|
| Основная Gateway БД | volume shluz-owner-check_owner_data, /app/data/shluz.db |
| Telegram-профиль/история | shluz-owner-check_owner_telegram_data, /app/data/telegram.sqlite |
| Telegram connector | shluz-owner-check_owner_telegram_connector |
| Отдельная БД сайта | noclip_web_guest_test_data, /app/data/guest-test.sqlite |
| Главный preservation snapshot | D:\NoClip\Shluz\backups\preservation-20261024-092630 |
| Приватный runtime сайта | D:\NoClip\Shluz\config\site-bridge\ |

Preservation содержит все6 исходных volumes и важные настройки/профиль .noclip; проверены SHA256211 исходных файлов. Пять SQLite реально восстановлены в отдельный verification volume, integrity_check=ok, логические хеши совпали. Рабочие БД не перезаписывались.

Дополнительно для сайта: coherent SQLite backup API, восстановление в отдельный локальный файл; integrity/counts/суммы расходов совпали. Финальная final-guest.sqlite содержит3 настоящих usage-записи, integrity_check=ok. Файлы pre-venice.sqlite, verified-real.sqlite, restore-verification.sqlite, final-guest.sqlite и обезличенный live-proof.json находятся в закрытом config/site-bridge. SQL backup не подменять обычным копированием открытой WAL-БД.

Секреты ngrok/Basic Auth/Turnstile/Venice находятся вне Git с ACL owner+SYSTEM. Сырые .env, docker inspect целиком, access links, Telegram initData, UI traces и архивы не публиковались. .gitignore и отсутствие tracked config проверены.

Остановленные noclip-site-gateway-pre-preview, noclip-site-gateway-pre-venice, noclip-site-gateway-before-live-key — сохранённые конфигурации одного гостевого тома, не независимые установки. Не запускать их параллельно. Старый список контейнеров из раннего HANDOFF-WINDOWS-LAUNCHER.md исторический: часть диагностических контейнеров позже удалена владельцем/другим агентом. Перед любой очисткой сверять свежие docker ps -a и mounts. Не выполнять docker system prune или down -v.

## 8. Проверки и честные границы

- Лаунчер: финальный локальный прогон118 passed,2 skipped; RuffPASS. Website subset25PASS. Настоящий CMD Start/Stop/Start и отсутствие state проверены.
- Frontend: целевые7PASS, typecheckPASS, Next production buildPASS. Полный suite60PASS/1FAIL: deployment-filter одинаково падает на Windows на неизменённом main3035; это не новый дефект Turnstile. CI повторно оценивать по точному head, не объявлять весь suite зелёным.
- Telegram: настоящее открытие Mini App и старая история подтверждены; проверка дальнейшего перехода в историю была прервана владельцем. Последние preflight cleanup-исправления покрыты unit-тестами, окончательная fault-injection приёмка не завершена.
- Сайт: настоящая модель и основной Production доказаны; платная генерация Telegram не доказана. Регистрация/SMTP, постоянный аккаунт, платежи и общая унификация данных не проверены этим этапом; свериться с GO_LIVE_CHECKLIST.
- Текущая передача документов не меняет код приложения, пользовательские данные или настройки доступа.

## 9. Рекомендованный порядок продолжения в облаке

1. Проверить PR38 и не допустить отката рабочего Production старым main. Подготовить согласованную интеграцию frontend, не сливая серверные ветки автоматически.
2. Изучить цепочку PR35 → PR36 → PR37, подтвердить совместимость реальных runtime images и перенести нужные исправления осознанно.
3. Подготовить локальную задачу безопасного подключения действующего ключа Venice к основному Gateway/Telegram. Сначала backup, затем секрет вне Git, контролируемый restart и один настоящий ответ через Telegram с сохранением истории. Облачному агенту не запрашивать ключ в чат и не выдавать прямой Venice POST за Telegram E2E.
4. Исправить IP-цепочку и воспроизвести исходный Windows deployment-filter тест в CI; до этого не расширять публичные пределы.
5. Завершить preflight failure/stop/restart приёмку, UX админки и согласованную политику хранения previous-контейнеров. Не удалять пользовательские volumes/документы/профили.

При любом продолжении сначала сверить GitHub refs, фактический Production, Docker и локальный state.json. Не повторять установку/миграцию вслепую, не путать сайт-туннель с Mini App и не считать наличие API-ключа доказательством успешного ответа.

## 10. Architecture review and GitHub synchronization — 24.10.2026

**Факты:** production Vercel подтверждён на `fd58ca705832a9095ac7753693479d7922cf05cb` и READY. `main` по-прежнему `3035e66dcbc246fc2fc266dd70eae6ca780fd28a`; расхождение — риск повторной публикации устаревшего Turnstile. PR #38 переведён в Ready; `deployment-filter` исправлен для Windows (CMD экранировал `^`). Полный Ready CI для HEAD `c6edeb7634671913475303eda62d7d5a0c33911a` успешен: 61/61 unit, TypeScript, production build, все браузерные проверки, Windows deployment-filter и 10 CI routing tests; https://github.com/DeepDlueIV/shluz/actions/runs/40062703825. Перед merge остаётся получить отдельное разрешение владельца на production-деплой. Серверные ветки не трогать.

**PR-канонизация:** #35 — историческая основа portable, #36 — её Windows/Cloudflare исправления, #37 — текущая проверенная цепочка с отдельным website bridge. Заголовки/описания в GitHub обновлены, ветки и Docker без изменений. Нельзя объединять #37 в `main` ради «чистоты»: сравнение затрагивает основной API, миграции БД и финансы.

**Технический долг, выявленный аудитом:** два Gateway (`:18000` Telegram и `:18001` website) используют разные источники данных и версии API. Provider key оказался в гостевом gateway, а основной использовал старый; права/бюджеты/идентичности двух SQLite не являются единой системой. Это временная изоляция тестирования, **не целевая SaaS-архитектура**. Два туннеля разделяют разные публичные границы (Telegram Mini App и защищённый Vercel bridge), но Quick Tunnel имеет нестабильный URL, а ручное управление неудобно.

**Рекомендация, пока НЕ утверждённая владельцем:** единый локальный Windows Control Center — отдельный host-процесс/супервизор с UI только на 127.0.0.1. Он управляет разрешёнными действиями start/stop/status/backup/update, provider keys через защищённое Windows-хранилище и туннелями. Сам публичный Gateway не получает Docker socket, произвольный shell или host Windows-полномочия; админку не публиковать наружу. Пользователь видит одну панель даже при двух временных ingress. Затем согласованно свести два Gateway к одному логическому источнику пользователей/лимитов/расходов после тестов миграции; не объединять живые базы без отдельного плана восстановления.

**Приоритет:** после полного CI согласовать обновление `main` проверенным frontend PR #38, затем эксплуатационная унификация с защищённым локальным UI без VPS. Не менять лимиты, ключи или финансы без согласования.

Эта секция — результат проверки GitHub/Vercel/CI в облаке, **не** новое подтверждение локального Windows запуска.
