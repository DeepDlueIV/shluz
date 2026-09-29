# Venice Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Подключить Venice как реального текстового провайдера и показать администратору баланс, лимиты, модели, цены и расход в браузере.

**Architecture:** Синхронный `VeniceProvider` использует внедряемый `httpx.Client`, чтобы production работал через HTTPS, а тесты — через `MockTransport`. Диагностика провайдера отделена от публичного API и отображается существующей Jinja2-админкой. MockProvider сохраняется как безопасный режим по умолчанию.

**Tech Stack:** Python 3.12, FastAPI, httpx, Pydantic Settings, Jinja2, pytest, Ruff, Docker.

**Spec:** `docs/superpowers/specs/2026-09-29-venice-observability-design.md`

## Global Constraints

- Секрет Venice никогда не отображается и не логируется.
- Платные POST-запросы автоматически не повторяются.
- Отказ beta-аналитики не ломает чат и всю админ-панель.
- Публичные `/v1/models` и `/v1/chat/completions` остаются OpenAI-совместимыми.
- Все сетевые тесты используют `httpx.MockTransport`.
- MockProvider остаётся рабочим без внешнего API-ключа.

## Review Focus

- Неверный/просроченный ключ должен давать безопасную ошибку без секрета.
- Нулевой баланс должен отличаться от сетевой ошибки.
- Ответ Venice без необязательных полей не должен падать при разборе.
- Недоступная usage analytics должна оставлять остальные карточки админки рабочими.
- Модель с одинаковым id у двух провайдеров должна по-прежнему обнаруживаться как конфликт.

---

### Task 1: Venice client and configuration

**Files:**
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Modify: `app/config.py`
- Create: `app/providers/venice.py`
- Modify: `app/providers/base.py`
- Test: `tests/providers/test_venice.py`

**Interfaces:**
- Produces: `VeniceProvider(settings: Settings, client: httpx.Client | None = None)`.
- Produces: `ProviderAccountSnapshot`, `ProviderUsageAnalytics`, extended `ChatResult` cost/request metadata.

- [ ] Write failing provider tests for model parsing, chat usage/cost parsing, balance/rate-limit/analytics parsing, missing optional fields, and safe error mapping.
- [ ] Run provider tests and verify failure because `VeniceProvider` does not exist.
- [ ] Add httpx runtime dependency and Venice settings.
- [ ] Implement the minimal Venice provider/client and typed result objects.
- [ ] Run provider tests and the full suite.
- [ ] Commit `feat: add Venice provider client`.

### Task 2: Provider selection and public API

**Files:**
- Modify: `app/factory.py`
- Modify: `app/providers/registry.py`
- Modify: `app/api/routes/chat.py`
- Modify: `app/api/schemas.py`
- Test: `tests/api/test_venice_chat.py`
- Test: `tests/providers/test_registry.py`

**Interfaces:**
- Consumes: `VeniceProvider`, extended `ChatResult`.
- Produces: settings-driven registry with Mock and/or Venice models.

- [ ] Write failing tests for registering Venice when configured, keeping Mock mode without a key, and returning normalized Venice chat responses.
- [ ] Run tests and verify expected failures.
- [ ] Implement settings-driven provider construction without changing client-facing endpoints.
- [ ] Map controlled provider failures to safe HTTP responses.
- [ ] Run focused tests and the full suite.
- [ ] Commit `feat: route chat through configured provider`.

### Task 3: Visual provider control panel

**Files:**
- Modify: `app/admin/routes.py`
- Modify: `app/templates/admin/index.html`
- Modify: `app/static/admin.css`
- Create: `app/templates/admin/provider.html`
- Test: `tests/admin/test_provider_dashboard.py`

**Interfaces:**
- Consumes: Venice diagnostic methods and settings-driven registry.
- Produces: `/admin/providers/venice` browser page.

- [ ] Write failing tests for configured/unconfigured Venice, balance display, model pricing, rate limits, analytics, and partial failure behavior.
- [ ] Run tests and verify expected failures.
- [ ] Implement a provider overview link on `/admin` and a detailed Venice page.
- [ ] Ensure the page never receives the raw API key in its template context.
- [ ] Run focused tests and the full suite.
- [ ] Commit `feat: add Venice admin observability`.

### Task 4: Operations documentation and verification

**Files:**
- Modify: `README.md`
- Modify: `docs/operations.md`
- Modify: `Dockerfile` only if dependency installation requires it.
- Modify: `.github/workflows/ci.yml` only if the existing workflow needs no-network test guards.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: novice-friendly local setup and safe Venice configuration instructions.

- [ ] Document how to run Mock mode and how the owner privately enters `SHLUZ_VENICE_API_KEY`.
- [ ] Document `/admin/providers/venice`, `/docs`, and the meaning of provider-level versus future user-level analytics.
- [ ] Run Ruff, pytest, and Docker build through GitHub Actions.
- [ ] Fix any CI failures and rerun.
- [ ] Commit `docs: explain Venice setup and monitoring`.
