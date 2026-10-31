#!/usr/bin/env bash
# Пересоздание site-gateway с SMTP Resend. Заполни RESEND_KEY перед запуском.
set -e
export MSYS_NO_PATHCONV=1
RESEND_KEY="${1:?Укажи API-ключ Resend как первый аргумент}"
SHA=$(git -C D:/NoClip/Shluz rev-parse --short HEAD)
docker rm -f noclip-site-gateway >/dev/null 2>&1 || true
docker run -d --name noclip-site-gateway \
  -p 127.0.0.1:18001:8000 \
  -v "D:/NoClip/site-gateway-data:/app/data" \
  -e SHLUZ_BUILD_SHA=$(git -C D:/NoClip/Shluz rev-parse HEAD) \
  -e SHLUZ_ENVIRONMENT=development \
  -e "SHLUZ_DATABASE_URL=sqlite+pysqlite:///./data/guest-test.sqlite" \
  -e SHLUZ_ADMIN_USERNAME=noclip-local-test \
  -e SHLUZ_ADMIN_PASSWORD=73f2716c287274b22a06b32823c52965a9d0fa0c423a321491b89e0e596550c2 \
  -e SHLUZ_BOOTSTRAP_API_TOKEN=8b76e3469f5a1b1a96f7beaf0c88cdd76873ffbd96bdac1fb92c3292a19b1085 \
  -e SHLUZ_WEB_ENABLED=true \
  -e SHLUZ_WEB_SECRET=16ea20cc79d4decadd1cf913991e4927f7fe1f63a3243c0eac95ea9f9a252c38 \
  -e SHLUZ_WEB_SECURE_COOKIE=true \
  -e "SHLUZ_WEB_TRUSTED_PROXIES=[]" \
  -e "SHLUZ_WEB_CAPTCHA_SITEKEY=0x4AAAAAAFjZZL9i56Yoea05" \
  -e SHLUZ_WEB_CAPTCHA_SECRET=0x4AAAAAAFjZZMOqD4Ll0cGCQAzumNBNjn4 \
  -e 'SHLUZ_WEB_ORIGINS=["https://noclip-web.vercel.app","https://noclip-web-git-test-site-bridg-055918-deepdlueiv-7467s-projects.vercel.app"]' \
  -e 'SHLUZ_WEB_CAPTCHA_HOSTNAMES=["noclip-web.vercel.app","noclip-web-git-test-site-bridg-055918-deepdlueiv-7467s-projects.vercel.app"]' \
  -e SHLUZ_ACTIVE_PROVIDER=venice \
  -e SHLUZ_VENICE_API_KEY=VENICE_INFERENCE_KEY_zl6jkSrey9A9rrSyX8UgmwUzEq8FSZflK76pyZtOkH \
  -e SHLUZ_WEB_SMTP_HOST=smtp.resend.com \
  -e SHLUZ_WEB_SMTP_PORT=465 \
  -e SHLUZ_WEB_SMTP_SSL=true \
  -e SHLUZ_WEB_SMTP_USERNAME=resend \
  -e "SHLUZ_WEB_SMTP_PASSWORD=$RESEND_KEY" \
  -e "SHLUZ_WEB_SMTP_SENDER=NoClip <no-reply@toscanopersonainc.online>" \
  noclip-web-guest-test:$SHA \
  sh -c "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log --no-proxy-headers"
sleep 20
docker ps --filter name=noclip-site-gateway --format "{{.Status}}"
curl -s --max-time 8 -o /dev/null -w "health: %{http_code}\n" http://127.0.0.1:18001/health
