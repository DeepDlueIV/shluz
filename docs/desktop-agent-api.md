# NoClip gateway → OpenWork / OpenCode: Chat Completions agent contract

Версия: `desktop-tools-v1`, 21.10.2026. Это контракт шлюза, не отчёт о проверке
установленного OpenWork. `web/`, `telegram_bot/` и desktop этим блоком не меняются.
Production, main и постоянный preview не обновляются этой feature-веткой.

## Проверенная исходная точка и версии

Разработка начата от `implementation/telegram-beta-complete`, коммит
`f6858bbd201c17f5abbd57ed0d02492c39983134` (дерево
`710dd7102c2f5be2d46fb36e491d8dac11d2cd9b`). В изолированной локальной копии не было
незакоммиченных изменений. `automation/verified-source-transfer-20261020` — старый
транспорт файлов, НЕ исходная ветка приложения; её код не подменял актуальный.

В tag OpenWork **v0.18.58** `constants.json` указывает OpenCode **v1.18.30**, а также
альтернативный `opencodeV2Version: 2.0.26`. Для этого контракта изучен путь v1.18.30:
он использует `@ai-sdk/openai-compatible` **2.0.41**. Реально выбранный движок в
конкретной установленной сборке проверяет разработчик desktop. Совместимость v2
и работа всего приложения не следуют из проверки одного HTTP-контракта.

## Подключение

| Параметр | Значение |
|---|---|
| Local owner gateway origin | `http://127.0.0.1:18000` |
| Base URL клиента | `http://127.0.0.1:18000/v1` |
| Генерация | `POST /v1/chat/completions` |
| Доступные именно этому аккаунту модели | `GET /v1/models` |
| Профиль, расход, кошелёк | `GET /v1/me`, `/v1/me/usage`, `/v1/me/wallet` |
| Авторизация | `Authorization: Bearer <персональный shluz_...>` |
| Канал ключа | `harness` (desktop), назначенный сервером |
| Изолированная модель контракта | `mock-chat`, только `noclip_test_echo` |
| Кандидат для настоящей задачи | `zai-org-glm-5-2`, только после живой проверки |

Шлюз должен быть запущен из версии с этим контрактом: текущий установленный preview
сам по себе не получает feature-ветку. Владелец не переносит файлы вручную; сборку и
изолированный запуск выполняет разработчик. На удалённом сервере вместо localhost
использовать согласованный HTTPS-origin. `/api/v1` — путь Venice, НЕ путь нашего шлюза.

Не использовать bootstrap/админский/служебный Telegram токен. Не передавать
`source` для переключения канала: старое поле принимается ради совместимости, но
игнорируется и upstream не отправляется. Локальные инструменты, доступ к файлам и
MCP выполняет только клиент; сервер не запускает функцию из `tool_calls`.

Пример **для разработчика desktop**, не готовая настройка публичного провайдера:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "enabled_providers": ["noclip"],
  "provider": {
    "noclip": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "NoClip",
      "options": {
        "baseURL": "http://127.0.0.1:18000/v1",
        "apiKey": "{env:NOCLIP_API_TOKEN}",
        "includeUsage": true
      },
      "models": {
        "zai-org-glm-5-2": {
          "name": "GLM 5.2 через NoClip — требуется live-проверка",
          "limit": {"context": 8192, "output": 1024}
        }
      }
    }
  },
  "model": "noclip/zai-org-glm-5-2",
  "small_model": "noclip/zai-org-glm-5-2"
}
```

Это консервативные клиентские ограничения первоначальной проверки, не размер
контекста GLM. Обе модели (основная и вспомогательная) направлены в NoClip, а не
напрямую к Venice. Не выбирать пакет OpenAI Responses вместо Chat Completions.
Проверить, что desktop не подмешивает другой provider, endpoint или секрет для
заголовков/суммаризации. Наличие model в этом JSON не выдаёт доступ к нему в шлюзе.

## Поддерживаемый минимум

- `model`, `messages`, `stream`, `stream_options.include_usage`, `max_tokens` либо
  `max_completion_tokens` (разные одновременные значения запрещены), `n=1`.
- Роли `system`, `user`, `assistant`, `tool`. Content — строка или массив текстовых
  частей `{type:"text",text:"..."}`; `null`/отсутствие content — только assistant
  с непустым `tool_calls`. Пустая строка допустима. `name` сохраняется.
- `tools`: до 128 function definitions с `name`, `description`, `parameters`,
  опциональным `strict`. Schema сохраняется, но шлюз не исполняет и не интерпретирует
  её как программу. Имена до 64 символов, уникальны в массиве.
- `tool_choice`: `auto`, `none`, `required` или `{type:"function",function:{name}}`.
  Выбранная функция должна присутствовать в tools. `parallel_tool_calls` передаётся.
- `assistant.tool_calls`: `id`, `type:function`, `function.name`, `function.arguments`
  (строка); `role:tool` обязательно содержит связанный `tool_call_id`. Повторные,
  потерянные и неразрешённые пары в переданной истории отклоняются до генерации.
- `temperature`, `top_p`, `frequency_penalty`, `presence_penalty`, `seed`, `stop`,
  `user`, `reasoning_effort` (`none|minimal|low|medium|high|xhigh|max`) передаются.
  Поддержка конкретного effort/strict/parallel зависит и от выбранной модели.
- `reasoning_content` assistant сохраняется в JSON, SSE и истории следующего шага.
  Ответный `reasoning` alias нормализуется в `reasoning_content`.

Не поддержаны в этом минимуме: старые `functions/function_call`, `n>1`, мультимедиа,
файловые вложения API, `response_format`, provider-executed tools, Responses API,
`extra_content`/Gemini signatures, произвольные `venice_parameters`/web-search и
другие неописанные поля. Они дают **422**, а не молча исчезают. Содержимое файлов
локальный инструмент может вернуть как ограниченный текст; это не upload endpoint.
Модель с явно отрицательным `supportsFunctionCalling` даёт **400 tools_unsupported**.

`finish_reason` сохраняется: `stop`, `tool_calls`, `length`, `content_filter`.
В агентском запросе отсутствие корректного окончания — ошибка, не выдуманный stop.
Для старого текстового потока без tools сохранена прежняя совместимость: успешный
`[DONE]` без явного finish может означать stop. Любой EOF без `[DONE]` — ошибка.

## Полный цикл (контролируемый mock, без настоящих файлов)

Первый `POST /v1/chat/completions` с персональным токеном и недемо-тарифом:

```json
{
  "model": "mock-chat",
  "max_tokens": 64,
  "messages": [{"role": "user", "content": "Echo hello"}],
  "tools": [{
    "type": "function",
    "function": {
      "name": "noclip_test_echo",
      "description": "Return the text without side effects",
      "parameters": {
        "type": "object", "properties": {"text": {"type": "string"}},
        "required": ["text"], "additionalProperties": false
      }
    }
  }],
  "tool_choice": "auto"
}
```

Существенная часть ответа (id/created здесь условные):

```json
{
  "id": "chatcmpl-example", "object": "chat.completion", "created": 0,
  "model": "mock-chat",
  "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
    "role": "assistant", "content": null,
    "tool_calls": [{"id": "call_noclip_echo_1", "type": "function", "function": {
      "name": "noclip_test_echo", "arguments": "{\"text\":\"hello\"}"
    }}]
  }}],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

Клиент проверяет имя по локальному allowlist, разрешения и arguments; затем сам
исполняет echo. Следующий **отдельный HTTP-запрос** содержит всю связанную историю:

```json
{
  "model": "mock-chat", "max_tokens": 64,
  "messages": [
    {"role": "user", "content": "Echo hello"},
    {"role": "assistant", "content": null, "tool_calls": [{
      "id": "call_noclip_echo_1", "type": "function",
      "function": {"name": "noclip_test_echo", "arguments": "{\"text\":\"hello\"}"}
    }]},
    {"role": "tool", "tool_call_id": "call_noclip_echo_1", "content": "hello"}
  ]
}
```

Итог — assistant content `Shluz mock tool result: hello`, finish_reason `stop`.
Mock гарантирует только этот сценарий: не настоящий интеллект и не выполнение
произвольных инструментов OpenWork. Для другого имени функция явно отклоняется.

## SSE: те же инструменты, аргументы по частям

Добавить `stream:true`, `stream_options:{include_usage:true}`. Пример wire-кадров:

```text
data: {"id":"chatcmpl-example","object":"chat.completion.chunk","created":0,"model":"mock-chat","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_noclip_echo_1","type":"function","function":{"name":"noclip_test_echo","arguments":"{\"text\":"}}]},"finish_reason":null}]}

data: {"id":"chatcmpl-example","object":"chat.completion.chunk","created":0,"model":"mock-chat","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"hello\"}"}}]},"finish_reason":null}]}

data: {"id":"chatcmpl-example","object":"chat.completion.chunk","created":0,"model":"mock-chat","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}

data: {"id":"chatcmpl-example","object":"chat.completion.chunk","created":0,"model":"mock-chat","choices":[],"usage":{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}}

data: [DONE]
```

Индекс относится к инструменту внутри choice 0. В первом фрагменте каждого индекса
должны быть полный id и полное имя — это требование читающего кода SDK 2.0.41.
Последующие фрагменты могут содержать только index и function.arguments. Разные
индексы могут чередоваться. Меняющийся ID/имя, пропущенный начальный заголовок,
неподдержанный delta или лишний choice — явная ошибка. Имена по частям не склеиваем.

Arguments — **не обязательно валидный JSON в отдельном фрагменте**. Сборка идёт по
index без промежуточного JSON.parse. Даже итоговые аргументы проверяет клиент по
своей схеме. `length` может оставить незавершённые аргументы: их нельзя выполнять
как законченную функцию. При ошибке/отмене не выполнять оставшийся частичный вызов.
SDK может обнаружить полный JSON раньше окончания потока; desktop должен отдельно
проверить своё поведение при поздней ошибке и не повторять инструменты автоматически.

## Доступ, деньги, отмена и ошибки

Источником канала остаётся персональный токен. Тариф × канал, активность пользователя,
месячные квоты, кошелёк PAYG, защита параллельных запросов и неизвестного расхода
применяются к **каждому HTTP-запросу цикла**, включая вспомогательные вызовы клиента.
Два шага — две записи использования; одно завершение/отмена не списывает дважды.
POST к Venice не повторяется автоматически. Повтор нового POST клиентом — новый
запрос, не гарантия идемпотентности по одинаковому prompt или call_id.

Tools недоступны в публичном текстовом демо (`403 demo_tools_not_allowed`), бесплатный
пакет не расширяется. Для проверки назначить существующий недемо-тариф тестовому
аккаунту. Весь tool schema, arguments, результаты, reasoning и история входят во
входной лимит (сейчас 200000 символов для недемо). Выход ограничен действующим
серверным пределом до 4096 токенов. WalletModelBound может быть строже; цены/пределы
внешней модели должны быть проверены отдельно, без автоматического открытия каталога.

| Ситуация | Результат |
|---|---|
| Неверный личный ключ | 401, без генерации |
| Неверный контракт/неподдержанное поле | 422 `invalid_request`, без отражения input |
| Известное отсутствие tool capability | 400 `tools_unsupported` |
| Запрет модели/демо tools/исчерпанная квота | Существующие 400/402/403/429 policy-коды |
| Ошибка ключа upstream до потока | 502 `provider_authentication_error` |
| Отсутствие бюджета upstream | 503 `provider_balance_exhausted` |
| Rate limit upstream | 429 `provider_rate_limit` |
| Ошибка/таймаут до начала ответа | JSON error 502; POST не повторяется |
| Ошибка после начала SSE | HTTP уже 200; кадр `{error:{code:"stream_interrupted",...}}`, без `[DONE]` |
| Клиент закрыл запрос | Upstream закрывается; один interrupted event, неизвестный расход сохраняется |

JSON-вызов Venice теперь тоже async и отменяемый. Общий deadline и idle timeout
потока сохранены; период между фрагментами не продлевает общий срок. Закрытие сокета
не гарантирует отсутствие списания у внешнего поставщика. Неизвестный usage отдаётся
как `null`, не как ноль; `/me/usage` также не рисует выдуманный бесплатный расход.
JSON/SSE ошибки после возможной генерации не освобождают неопределённый денежный
резерв. Логи не получают тела сообщений, аргументы, результаты инструментов или ключи.

## Реальная модель и границы проверки

Документация Venice помечает `zai-org-glm-5-2` как Function calling / Reasoning / Code.
Это кандидат, не доказанный вызов из этой среды. Проверить живой `/models`,
`supportsFunctionCalling`, разрешения `harness`, бюджет и свежие ценовые bounds.
В текущем окружении отсутствует настроенный тестовый Venice-ключ и не разрешается
DNS `api.venice.ai`: платных запросов этого этапа **0**. Старый секрет из чата не
перепубликуется и не внедряется в код, workflow или desktop.

Тесты шлюза: JSON и SSE цикл через настоящий Venice adapter с изолированным HTTP
transport, аргументы по фрагментам и UTF-8, несколько индексов, отрицательные
контракты, ошибки до/после headers, неизвестный usage, ограничения модели/демо,
настоящий localhost HTTP disconnect и таймаут, защита однократного учёта.
Отдельный CI job использует настоящий JS SDK **2.0.41** с mock gateway, не OpenWork GUI.

Остаётся реальная приёмка: версия фактически запущенного OpenCode; авторизация
персональным ключом; один разрешённый безопасный локальный инструмент OpenWork;
результат → финальный ответ; Stop в середине arguments; лимит/ошибка/отказ
разрешения инструмента; все основные и вспомогательные обращения только через
шлюз; корректные токены/списания по двум шагам. MCP и реальные файлы здесь не запускались.

### Первичные источники

- OpenWork v0.18.58: https://github.com/different-ai/openwork/blob/v0.18.58/constants.json
- OpenCode v1.18.30: https://github.com/anomalyco/opencode/blob/v1.18.30/packages/opencode/package.json
- Provider options: https://github.com/anomalyco/opencode/blob/v1.18.30/packages/opencode/src/provider/transform.ts
- SDK converter: https://github.com/vercel/ai/blob/@ai-sdk/openai-compatible@2.0.41/packages/openai-compatible/src/chat/convert-to-openai-compatible-chat-messages.ts
- SDK JSON/SSE: https://github.com/vercel/ai/blob/@ai-sdk/openai-compatible@2.0.41/packages/openai-compatible/src/chat/openai-compatible-chat-language-model.ts
- Custom provider: https://opencode.ai/docs/providers/#custom-provider
- Venice wire: https://docs.venice.ai/api-reference/endpoint/chat/completions
- Venice capabilities: https://docs.venice.ai/models/overview
