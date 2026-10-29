# Передача: Cloudflare Tunnel и Telegram Mini App

Дата проверки: 24.10.2026 (Windows, portable-каталог `D:\NoClip\Shluz`).
Секреты, URL временных туннелей и содержимое пользовательских БД в документ не включены.

## Вывод

Последняя сохранённая Quick Tunnel ссылка устарела и сейчас не разрешается в DNS.
Один контролируемый повторный запуск получил тот же результат. Причина недоступности
на стороне сетевого пути к Cloudflare Tunnel: из Windows и из Docker-контейнера
разрешается DNS `region1/2.v2.argotunnel.com`, TCP к edge на порту 7844 устанавливается,
но TLS handshake завершается ошибкой; QUIC на UDP 7844 истекает по таймауту. Подключение
через настроенный системный HTTP-прокси также дошло до CONNECT 200, но TLS handshake
не завершился. В проверенной конфигурации нет активного VPN-подключения или WinHTTP
proxy. Защита Windows не отключалась, правила и исключения не менялись.

Cloudflare `protocol=auto` выбирает QUIC и должен перейти на HTTP/2, если QUIC недоступен.
В этой сети оба транспорта не завершили соединение к edge, поэтому новый публичный
адрес не подтвердился. Само состояние контейнера `running` не доказывает, что Quick
Tunnel обслуживает адрес: cloudflared может оставаться в цикле повторных подключений.

## Что выполнено

- Проверены код и текущий portable state: предыдущий запуск оставил фазу
  `menu_unverified`; `NoClip.cmd status` подтвердил Gateway и Telegram Mini App healthy,
  а публичную HTTPS-проверку классифицировал как `dns`.
- Один раз выполнен штатный `NoClip.cmd start`. Лаунчер перепроверил URL и меню,
  перезапустил только уже существующий Tunnel, не создавая новый Telegram poller.
  Публичный адрес снова не прошёл DNS-check. Итоговое состояние: `tunnel_unavailable`,
  причина `dns`, адрес не сохранён как рабочий, Tunnel остановлен.
- Gateway и Mini App контейнеры после отказа остались healthy/running; classic-бот
  остался остановленным резервом. Docker volumes и базы не изменялись; команда не
  запускала backup, миграции, compose down или операции с volumes.
- Попытка безопасно убрать старую managed-кнопку не получила подтверждения read-back;
  Telegram меню обозначено `stale`. Не считать ссылку в Telegram исправленной.

## Изменения в launcher

`launcher/miniapp_live.py` повторно проверяет HTTPS на последующих запусках, использует
фактическую дату запуска контейнера для поиска URL текущего процесса, переподнимает
только существующий Tunnel один раз, сверяет ответ Mini App и Telegram menu read-back.
Сбой DNS/TLS/edge сохраняется отдельным статусом, старый адрес не показывается как
рабочий; при проблемах Telegram здоровые Gateway и bot-poller не останавливаются.
Диагностика различает сетевой отказ и отклонение/задержку Telegram API. Ошибки Bot API
не включают URL запроса с токеном.

## Venice — отдельный статус

В portable-конфигурации и запущенном Gateway выбраны `SHLUZ_ACTIVE_PROVIDER=venice`;
`SHLUZ_VENICE_API_KEY` присутствует. Отдельная модель через переменную окружения не
задана. Авторизация текущего ключа этим запуском не перепроверялась; генеративные и
платные запросы не выполнялись. Предыдущая локальная передача фиксировала отказ 401
без генерации для тогдашнего ключа owner Gateway; это не подтверждает состояние
текущего ключа. Путь сайта использует отдельную конфигурацию и не подтверждает
авторизацию Telegram Gateway.

## Проверки

- Windows-набор `tests/launcher`: **126 passed, 2 skipped**.
- Ruff для изменённых Python-файлов: **passed**; `py_compile` и `git diff --check`:
  **passed**.
- GitHub Actions [run 40091601284](https://github.com/DeepDlueIV/shluz/actions/runs/40091601284):
  Windows **127 passed, 1 skipped**; Linux launcher **121 passed, 7 skipped**;
  Telegram Mini App **21 passed, 1 warning**; Ruff passed; isolated Docker
  SQLite/volume smoke passed; ZIP package and SHA256 verification passed.
- CI artifact `NoClip-SHLUZ-Portable-Windows` создан, SHA-256 контейнера артефакта
  `05c27f59078e06e889144d85aaf1724aeb7fec8ba02bd17568ec0f1a9157cdec`, действует
  до 23.11.2026. Его можно скачать со страницы Actions run; это временный CI artifact,
  а не release.
- Живая локальная проверка: Gateway/Mini App health сохранены; публичный Tunnel
  **не прошёл** DNS после одного восстановления; Telegram menu read-back **stale**.
  Поэтому pre-release **не публиковался**: критерий работающего HTTPS Tunnel на ПК
  владельца не выполнен.

## Блокер и следующий безопасный шаг

Для повторного E2E нужен сетевой маршрут, разрешающий Cloudflare Tunnel на UDP или TCP
7844 с успешным TLS handshake. Не отключать защиту и не добавлять исключения. После
изменения сетевой среды сначала повторить только read-only проверки edge connectivity;
затем один раз запустить `NoClip.cmd`, подтвердить свежий HTTPS health и Telegram
read-back. Пока этот шаг не пройден, не выдавать Mini App за готовую и не публиковать
новый prerelease.

Текущий owner Gateway выбирает Venice, но его авторизация и реальный ответ модели
остаются неподтверждёнными. Это отдельный следующий пункт, не связанный с DNS туннеля;
не выполнять платный запрос без отдельно согласованного теста.
