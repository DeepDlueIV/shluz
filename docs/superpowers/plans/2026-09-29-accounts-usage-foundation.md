# Accounts and Usage Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить переносимую базу пользователей, тарифов и событий расхода с визуальными административными разделами.

**Architecture:** SQLAlchemy скрывает различия между локальным SQLite и PostgreSQL/Supabase. Текущий общий API-токен связывается с технической учётной записью `bootstrap`, а каждый успешный запрос записывает токены и себестоимость без сохранения текста запроса.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, SQLite/PostgreSQL, Jinja2, pytest.

**Spec:** `docs/superpowers/specs/2026-09-29-accounts-usage-design.md`

## Global Constraints

- Не хранить открытые пользовательские токены или ключи поставщиков.
- Не сохранять текст пользовательских запросов.
- Сохранить OpenAI-совместимый публичный API.
- Локальный запуск должен работать без Supabase.
- PostgreSQL/Supabase должен подключаться заменой одной переменной окружения.
- Все административные страницы защищены существующей авторизацией.

## Review Focus

- Пустая или недоступная база должна давать понятную ошибку, а не раскрывать строку подключения.
- Повторный запуск инициализации не должен создавать дублирующую учётную запись `bootstrap`.
- Себестоимость и токены должны записываться ровно один раз на успешный запрос.
- Секретные токены и содержимое сообщений не должны попадать в таблицы или HTML.
- Административные сводки должны корректно работать при пустой базе.

---

### Task 1: Database foundation and schema

**Files:**
- Modify: `pyproject.toml`
- Modify: `app/config.py`
- Create: `app/db/base.py`
- Create: `app/db/models.py`
- Create: `app/db/database.py`
- Create: `app/db/repositories.py`
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/versions/20260929_01_accounts_usage.py`
- Test: `tests/db/test_database.py`

**Interfaces:**
- Produces: `Database(settings)`, `Database.session()`, `ensure_bootstrap_account(session)`, ORM models for accounts, identities, plans, subscriptions, api_tokens and usage_events.

- [ ] Write failing tests for schema creation, bootstrap idempotency and token hash-only storage.
- [ ] Run `pytest tests/db/test_database.py -v` and verify failure because the database package does not exist.
- [ ] Implement the minimal database layer and first migration.
- [ ] Run the focused test and then the full suite.
- [ ] Commit the task.

### Task 2: Usage ledger integration

**Files:**
- Modify: `app/factory.py`
- Modify: `app/api/dependencies.py`
- Modify: `app/api/routes/chat.py`
- Create: `app/usage/service.py`
- Test: `tests/usage/test_usage_recording.py`

**Interfaces:**
- Consumes: `Database.session()` and `usage_events` from Task 1.
- Produces: `UsageService.record_success(...)` and one persisted event per successful completion.

- [ ] Write failing tests asserting provider, model, token counts, costs and `bootstrap` account attribution.
- [ ] Run the focused test and verify failure.
- [ ] Implement usage recording without persisting message text.
- [ ] Run focused and full tests.
- [ ] Commit the task.

### Task 3: Visual administrative overview

**Files:**
- Modify: `app/admin/routes.py`
- Modify: `app/templates/admin/index.html`
- Create: `app/templates/admin/users.html`
- Create: `app/templates/admin/plans.html`
- Create: `app/templates/admin/usage.html`
- Modify: `app/static/admin.css`
- Test: `tests/admin/test_accounts_usage_dashboard.py`

**Interfaces:**
- Consumes: repository summary methods from Task 1.
- Produces: protected `/admin/users`, `/admin/plans` and `/admin/usage` pages.

- [ ] Write failing tests for empty states, bootstrap user display, cost totals and secret exclusion.
- [ ] Run the focused test and verify failure.
- [ ] Implement read-only pages and main-dashboard links.
- [ ] Run focused and full tests.
- [ ] Commit the task.

### Task 4: Portable operation and documentation

**Files:**
- Modify: `.env.example`
- Modify: `Dockerfile`
- Modify: `compose.yaml`
- Modify: `README.md`
- Modify: `docs/operations.md`
- Test: existing full suite and Docker build.

**Interfaces:**
- Produces: one-command local SQLite startup and configurable PostgreSQL/Supabase deployment.

- [ ] Add database settings, persistent local volume and migration startup.
- [ ] Document local inspection and the later Supabase activation step.
- [ ] Run `ruff check .`, `pytest -v` and Docker build in GitHub Actions.
- [ ] Commit and prepare the pull request.
