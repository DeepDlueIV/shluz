# Shluz

Очень простой proof of concept центрального API-шлюза.

Сейчас он **не подключён к Venice или Supabase**. Цель этого этапа — проверить саму схему:

```
Harness / сайт / Telegram
          ↓
        Shluz
          ↓
   будущие AI-провайдеры
```

## Что уже есть

- `GET /health` — проверка, что сервер работает.
- `POST /v1/chat/completions` — минимально похожий на OpenAI API endpoint.
- Простая проверка `Bearer`-токена.
- Mock-ответ вместо реального запроса к модели.
- Заготовки переменных окружения для Supabase и Venice.

## Запуск локально

Требуется Python 3.11+.

```bash
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\activate
pip install -r requirements.txt
set SHLUZ_TEST_TOKEN=dev-token
uvicorn app.main:app --reload
```

После запуска:

- http://127.0.0.1:8000/health
- http://127.0.0.1:8000/docs — графическая документация API

## Тест запроса

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "demo-model",
    "messages": [
      {"role": "user", "content": "Привет"}
    ]
  }'
```

Ожидаемый ответ содержит:

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "Shluz mock reply: Привет"
      }
    }
  ]
}
```

## Следующий этап

После проверки каркаса сюда можно добавить:

1. Supabase Auth — определить, кто пользователь.
2. Таблицу тарифов и расхода.
3. Реальный вызов Venice API.
4. Учёт стоимости каждого запроса.
5. Потоковую выдачу ответа.
6. Подключение выбранного Harness как клиента к этому адресу.

Важно: реальные ключи AI-провайдеров должны храниться только на сервере и никогда не попадать в Harness, браузер или Telegram-бота.
