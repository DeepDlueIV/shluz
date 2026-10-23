# Контракт агентского цикла — план исполнения

База: f6858bbd201c17f5abbd57ed0d02492c39983134, дерево
710dd7102c2f5be2d46fb36e491d8dac11d2cd9b. Источник проверен по GitHub PR #10
и побайтному git tree CI-архива. Временная рабочая копия создана из этого архива;
чужих незакоммиченных изменений в ней не было. automation/* — транспортная ветка,
не последняя версия приложения. Работа: feat/opencode-tool-calling; без main,
публикации preview, смены провайдера, реальных данных владельца и изменений клиентов.

Требования пользователя 21.10.2026 — исполняемый scope. OpenWork v0.18.58 содержит
constants.json: OpenCode v1.18.30 и альтернативный v2 2.0.26. Здесь контракт
Chat Completions для v1.18.30/@ai-sdk/openai-compatible 2.0.41; выбор реального
движка установленного desktop проверяется отдельно. Инструменты исполняет клиент.

- [ ] Зафиксировать исходные схемы и дефекты: tools теряются, tool result лишается ID,
      content=null отклоняется, tool_calls finish считается ошибкой, JSON всегда stop.
- [ ] RED→GREEN: строгий текстовый/function контракт с непрерывной передачей полей,
      nullable assistant, text parts, связями call/result, безопасными ошибками.
- [ ] Расширить доменные данные, JSON и SSE Venice без разбора неполных arguments.
- [ ] Передавать delta индексы/ID/function/arguments, reasoning_content и finish;
      не вызывать инструменты на сервере. Не скрывать неподдерживаемые возможности.
- [ ] Те же Bearer/source/model/quota/PAYG проверки; считать tool schemas/history
      во входном лимите. Запретить инструменты демо, как согласовано ранее.
- [ ] Полный цикл и ошибки/таймаут/отмена/неизвестный usage; один reservation на HTTP.
- [ ] Документация подключения/источники/границы и текущий доступ модели.
- [ ] Связанные проверки, полный доступный набор, затем ветка/PR/CI без деплоя.

Допущения: только текст и локальные function tools, n=1. Vision/audio/Responses,
встроенные инструменты Venice, смена авторизации и финальный desktop UI вне задачи.
Не включать их молча. Старое поле source оставляем совместимым, но оно не меняет
канал персонального токена. JSON Schema parameters и arguments — данные клиента,
не код для выполнения и не логи. Повторный HTTP — новый оплачиваемый вызов, а не
серверный auto-retry. Проверка настоящего ключа только из доступных локальных
разрешённых секретов; чужой компьютер и его ключ из чата не используются.

## Execution ledger

- Reproduced silent schema loss and null-content failures before implementation;
  10 contract tests failed, then 14 passed after typed wire schema.
- Venice JSON and SSE integration initially failed before mapping tool fields;
  now two-step calls preserve headers, arguments, identity and accounting.
- Found cancellation watcher cancellation swallowed by Request.is_disconnected's
  internal CancelScope. Reproduced hung JSON response; direct ASGI receive watcher
  now cancels deterministically. Real localhost HTTP tests cover JSON and SSE Stop.
- Missing JSON usage was previously rendered zero; now marked unknown/null.
- Added explicit no-tools demo guard without changing issued grants or tariffs.
- Existing backend regression run: 425 pass, 2 platform skips, 12 fail solely due
  to unavailable aiogram in this offline sandbox. Full dependency-installed suite
  is required in GitHub CI; do not call this local run fully green.
- 44 new targeted tests pass locally; real JS SDK adapter test is CI-only.
- Local runtime lacks configured authorized Venice test key; DNS resolution fails.
  No live generations, deployment, payments, or writes to owner databases.
- Feature branch CI may build/test but preview job excludes feature refs and PRs.
