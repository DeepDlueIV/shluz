> **Обязательное правило владельца с 24.10.2026 — локальная поставка и админка.**
> Перед любыми изменениями локального лаунчера, релизов, путей, конфигурации,
> Vercel/Git-процесса или инфраструктурной админки читать
> [`docs/OWNER_LOCAL_DELIVERY_POLICY.md`](docs/OWNER_LOCAL_DELIVERY_POLICY.md).
> Критерий: скачать проверенный ZIP → распаковать в произвольную временную папку
> → один файл запускает/обновляет существующую установку `D:\NoClip\Shluz`,
> без ручного переноса AppData, ключей, томов или выбора процессов.
> Существующая `/admin` остаётся одной видимой админкой; опасные host-операции
> должны выполняться отдельным закрытым supervisor, а не публичным Gateway.
> `main` — принятая стабильная версия, не автоматическое зеркало feature-веток.
> **Важно:** это правило поставки, не утверждение о готовности автоматического обновления.
> **20.10.2026: main НЕ объединять — владелец отложил живую приёмку.**
> Подписки сохранены; optional PAYG и USD/credits ×100 описаны в docs/wallet-and-access.md.
> Путь сайта: docs/guest-dashboard-journey.md; прайс: docs/venice-pricing-and-wallet.md.
> Старые ключи не оживлять, расход/кошельки/демо архивом не сбрасывать.
> Внешние платежи, provider switch и публичный доступ по-прежнему отложены.

> **Отложенные подключения:** перед обсуждением готовности сайта читать
> `docs/GO_LIVE_CHECKLIST.md` и напоминать о незакрытых пунктах. Владелец не хочет
> заниматься ими сейчас. Не включать внешние сервисы или публичный доступ молча.

> **Дополнение 20.10.2026:** официальный серверный Telegram Login реализован;
> контракт `docs/telegram-web-login.md`, индекс `docs/CLIENT_API.md`. Внешняя настройка
> BotFather/SMTP/Turnstile/домена отдельно; публичный доступ не включать без проверки.
> Владелец разрешил небольшие проверки Venice после оплаты; это не разрешение
> открывать весь каталог или менять тарифы/ключи. В CI всегда mock.

> **Актуализация 19.10.2026 — web-access-v1.** Новый demo-default: **1 запрос**,
> до 3000 символов и 512 выходных токенов. Сайт: гостевое демо без регистрации,
> email-вход через OTP без пароля. Старые выданные пакеты сохраняют свои лимиты.
> Общий контракт: [docs/CLIENT_API.md](docs/CLIENT_API.md).
> Cookie API реализован, но публично выключен до настройки SMTP/Turnstile/HTTPS.
> Исторические упоминания 3 запросов и отсутствующих web routes ниже не описывают
> новое состояние. Оплата, provider switch, frontend и официальный Telegram Login
> браузера этим обновлением не включены.

# Договорённости по Shluz

Эти правила владельца приоритетны над общим профилем ниже.
- Выполнять обратимые изменения самостоятельно до проверенного результата, без промежуточных отчётов.
- Технические проверки API, квот, ключей, миграций, перезапуска и сохранения данных проводит агент
  в изолированном окружении и CI. Не перекладывать их на владельца.
- Владелец оценивает удобство и внешний вид, а свои секреты настраивает у себя.
  Просить диагностику его компьютера только для конкретного невоспроизводимого удалённо сбоя.
- Итог подтверждать свежими проверками и CI, не обещаниями. Не использовать рабочую базу,
  реальные платежи или платные модели для тестов. Не просить секреты в чат.
- Регистрация, привязка Telegram и внутренние доступы — функции приложения, не ручные
  действия владельца. Не предлагать владельцу выпускать ключи на каждого человека,
  редактировать списки ID или диагностировать обычную интеграцию; автоматизировать это.
- При передаче локальной работы Codex передавать точный коммит/команду и проверяемый результат,
  не выдавать изменение GitHub за установку на компьютере владельца.
- Сохранять совместимость и данные. Не сливать в main и не менять рабочий сервер без разрешения.


## Актуальные уточнения владельца — 18.10.2026

Перед планированием читать `docs/product-decisions-2026-10-18.md`, перед работой с Venice —
`docs/venice-api-notes.md`. Они уточняют старые планы: demo при Start, независимые
email/Telegram-аккаунты возможны; модель и платёжный API владелец выберет позже.
Для обсуждения развилок использовать Grill Me / grilling с вариантами и рекомендацией;
факты выяснять по коду самостоятельно. Не внедрять новую бизнес-логику без согласования.
Не реализовывать Telegram Stars. Сейчас в объёме только будущая кнопка тарифов/оплаты,
не сам процессинг. Публичные правила платформы не скрывать и не объявлять обойдёнными.
Тестирование со сторонним платным API допускается только в явно разрешённом владельцем
объёме: в этом запросе одна короткая проверка Venice, а не общий допуск всем пользователям.
В обычном CI — mock. Никаких настоящих ключей в исходниках, публичных workflow или отчётах.
На итерации — связанные тесты; перед публикацией — полный набор. В итогах разделять
количество тестов, длительность и живую/синтетическую проверку.

---

# Global Codex Agent Profile

## General Principles

- Complete the task before stopping whenever reasonably possible.
- Clarify before acting when the answer would materially change the approach, workflow, scope, architecture, or an irreversible decision.
- When clarification is needed, ask one short question at a time and provide 2–4 ready-made answer options when practical.
- Do not ask about minor, reversible, or implementation-level choices when a sensible default is clear.
- Once sufficiently aligned, proceed autonomously and persist until the task is solved or a real blocker is reached.
- Never guess facts; explicitly state assumptions that cannot be verified.
- Prefer the simplest correct solution and reuse existing project patterns.
- Prefer modifying existing code over introducing new abstractions.
- Avoid overengineering and keep diffs focused and minimal.

---

## Task Planning

Before implementation:

- Estimate task complexity as light, medium, or complex.
- For medium and complex tasks, briefly describe the implementation plan before writing code.
- Identify meaningful decisions that could materially change the approach.
- If such a decision exists, involve the user early; present 2–3 clear options with concise trade-offs and a recommendation when useful.
- Do not delay implementation for choices that do not materially affect the outcome.

---

## Skills

For every task, check which Superpowers Skills apply before taking action.

- Use every relevant Skill and follow its workflow.
- Prefer `Superpowers:brainstorming` when meaningful implementation choices require user alignment.
- Do not skip a relevant Skill because the task appears small or familiar.
- If no Skill applies, state that it was checked and skipped before continuing normally.
- Do not use a Skill when it would increase complexity without meaningful benefit.

When independent subtasks exist, execute them in parallel using subagents whenever beneficial.

---

## Code Changes

Before introducing a new dependency, helper, utility, or abstraction, first check whether an existing solution already exists.

Prefer:

- deleting code over adding code;
- local changes over large refactors;
- composition over duplication.

Do not perform drive-by refactors.
Only modify unrelated code when it directly improves correctness.

---

## Verification

Before considering the task complete:

- run the smallest relevant verification;
- run only relevant tests when possible;
- fix obvious failures before finishing.

Never claim success without verification.
If verification cannot be performed, explain why.

---

## Git Workflow

Treat Git history as documentation.

Create commits automatically after every logically complete unit of work. Each commit should:

- solve one problem;
- be reversible;
- contain a descriptive commit message.

Before committing, ensure relevant validation succeeds whenever practical.
Never push without explicit permission.
Never combine unrelated work into a single commit.

---

## Logging

When creating logs, make them detailed enough for debugging, handoff, reproducing the work, and understanding decisions.

Include:

- action;
- reason;
- result;
- timestamp;
- next step.

---

## Documentation

Document:

- non-obvious decisions;
- complex algorithms;
- important assumptions.

Do not document obvious code.
Add concise comments or docstrings for non-obvious functions and blocks.

---

## Communication

When communicating with me in Russian:

- write logs in Russian;
- write explanations in Russian;
- write internal code comments in Russian;

unless doing so would reduce code quality or maintainability.

---

## Context Efficiency

Avoid wasting context.

Do not:

- dump large files unnecessarily;
- repeat previous information;
- explain obvious code;
- produce excessively verbose output.

Read only the files needed to solve the task.
Prefer targeted inspection over broad exploration.

---

## Engineering Mindset

Optimize for:

- correctness;
- maintainability;
- readability;
- simplicity.

Not for:

- cleverness;
- unnecessary flexibility;
- speculative future requirements.

If two solutions are equally good, choose the simpler one.

---

## Next Steps

When proposing a next task or step after completing work:

- briefly assess its complexity;
- outline the key steps;
- recommend an appropriate reasoning effort: low, medium, high, xhigh, max, or ultra;
- state which Superpowers Skills were used, if any.
