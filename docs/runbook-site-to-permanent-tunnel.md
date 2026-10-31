# Runbook: перенос сайта с ngrok на постоянный Cloudflare-туннель

Выполняет владелец (или агент) днём. Время: ~10 минут + окно недоступности ~30 сек.
Откат — 2 минуты. Механика проверена на Mini App (30.10).

## Шаг 1. Hostname в конфиге туннеля

`D:/NoClip/Shluz/config/cloudflared-permanent/config.yml` — добавить ingress ПЕРЕД
catch-all (порядок важен):

```yaml
ingress:
  - hostname: miniapp.toscanopersonainc.online
    service: http://miniapp:8081
  - hostname: app.toscanopersonainc.online
    service: http://noclip-site-gateway:18001
  - service: http_status:404
```

## Шаг 2. Сеть

Туннель-контейнер живёт в `noclip-miniapp-https-net`; сайт-контейнер — в своей сети.
Подключить: `docker network connect <сеть-сайта> noclip-miniapp-https` (имя сети:
`docker inspect noclip-site-gateway --format '{{range $k,$_ := .NetworkSettings.Networks}}{{$k}}{{end}}'`).

ВАЖНО: basic-auth сайта сейчас живёт в ngrok traffic-policy — cloudflared её не умеет;
перенести в WAF-правило зоны Cloudflare или в сам сайт-контейнер ДО переключения.

## Шаг 3. DNS

`cloudflared tunnel route dns -f noclip-permanent app.toscanopersonainc.online`

## Шаг 4. Перезапуск туннеля

`docker restart noclip-miniapp-https`; ждать `Registered tunnel connection`
(`docker logs noclip-miniapp-https 2>&1 | grep -c Registered` ≥ 2; логи в stderr).

## Шаг 5. Vercel

Проект noclip-web → Environment Variables: заменить URL туннеля на
`https://app.toscanopersonainc.online` → Redeploy (прод собирается из main).
Проверить: сайт открывается, гостевое демо даёт ответ модели, usage_event появился в БД.

## Шаг 6. Проверки

- `curl -A "Mozilla/5.0" https://app.toscanopersonainc.online/` → 200
  (браузерный UA обязателен: Bot Fight Mode даёт 403 на python-urllib);
- `/v1/web/me` без сессии → 401 (норма);
- демо-запрос с сайта → ответ модели.

## Откат (2 минуты)

Vercel: вернуть ngrok URL → Redeploy. Туннель: убрать hostname из config.yml,
`docker restart noclip-miniapp-https`. ngrok не выключали — сайт всё это время жив.
