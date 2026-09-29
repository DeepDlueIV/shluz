# Accounting Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Создать переносимый слой пользователей, тарифов и учёта расходов с наглядной админ-панелью.

**Architecture:** SQLAlchemy хранит данные в SQLite локально или PostgreSQL/Supabase на сервере. Каждый запрос регистрируется до обращения к провайдеру и завершается фактическими токенами/стоимостью. Админ-панель читает ту же базу и не отображает секреты.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, SQLite/PostgreSQL, Jinja2, pytest.

**Spec:** `docs/superpowers/specs/2026-09-29-accounting-foundation-design.md`

## Global Constraints

- Не применять изменения к неактивному Supabase-проекту без отдельного подтверждения владельца.
- Локальный запуск должен работать без Supabase и без отдельного сервера PostgreSQL.
- Денежные значения используют Decimal/Numeric.
- Секреты не попадают в HTML, логи, GitHub и тестовые фикстуры production-конфигурации.
- Платный вызов провайдера не выполняется, если событие расхода нельзя создать.

## Review Focus

- Недоступная база до вызова модели должна давать безопасную ошибку и не вызывать провайдера.
- Ошибка модели должна оставлять завершённое событие `failed` без внутренних деталей.
- Параллельные запросы не должны делить SQLAlchemy Session.
- SQLite и PostgreSQL должны использовать одинаковые публичные модели данных.
- Админ-страницы не должны показывать token_hash или секреты провайдеров.

---

### Task 1: Data model and portable database

**Files:**
- Create: `app/db/base.py`
- Create: `app/db/models.py`
- Create: `app/db/session.py`
- Create: `app/db/__init__.py`
- Modify: `app/config.py`
- Modify: `app/factory.py`
- Modify: `pyproject.toml`
- Test: `tests/db/test_models.py`

**Interfaces:**
- Produces: `SessionFactory`, `create_database_engine(url)`, `initialize_database(engine)` and ORM models.

- [ ] Write failing tests that persist Account, Identity, Plan, Subscription, ApiToken, UsageEvent and AuditEvent in SQLite memory.
- [ ] Verify tests fail because database modules do not exist.
- [ ] Implement the minimal SQLAlchemy models and session factory.
- [ ] Run the database tests and full suite.
- [ ] Commit.

### Task 2: Usage accounting around model calls

**Files:**
- Create: `app/services/usage.py`
- Modify: `app/api/dependencies.py`
- Modify: `app/api/routes/chat.py`
- Modify: `app/factory.py`
- Test: `tests/api/test_usage_accounting.py`

**Interfaces:**
- Consumes: `SessionFactory`, `UsageEvent`.
- Produces: `UsageService.begin()`, `succeed()`, `fail()`.

- [ ] Write failing tests for pending, succeeded and failed events and for fail-closed behavior when storage is unavailable.
- [ ] Verify the tests fail for the intended missing behavior.
- [ ] Implement usage accounting without automatically retrying paid POST calls.
- [ ] Run focused and full tests.
- [ ] Commit.

### Task 3: Visual administration pages

**Files:**
- Modify: `app/admin/routes.py`
- Modify: `app/templates/admin/index.html`
- Create: `app/templates/admin/users.html`
- Create: `app/templates/admin/plans.html`
- Create: `app/templates/admin/usage.html`
- Create: `app/templates/admin/tokens.html`
- Modify: `app/static/admin.css`
- Test: `tests/admin/test_accounting_dashboard.py`

**Interfaces:**
- Consumes: ORM models and `SessionFactory`.
- Produces: read-only pages `/admin/users`, `/admin/plans`, `/admin/usage`, `/admin/tokens`.

- [ ] Write failing tests for summaries, lists, empty states and secret redaction.
- [ ] Verify 404/missing-content failures.
- [ ] Implement focused SQL queries and server-rendered pages.
- [ ] Run admin tests and full suite.
- [ ] Commit.

### Task 4: Portable storage and Supabase migration

**Files:**
- Modify: `.env.example`
- Modify: `compose.yaml`
- Modify: `Dockerfile`
- Create: `supabase/migrations/20260929_01_accounting_foundation.sql`
- Modify: `README.md`
- Modify: `docs/operations.md`
- Test: CI Docker build and full pytest suite.

**Interfaces:**
- Produces: one-command local persistent SQLite and a PostgreSQL/Supabase DDL migration with RLS enabled.

- [ ] Add configuration and persistent Docker volume.
- [ ] Add SQL migration matching ORM tables and denying direct public access by default.
- [ ] Document local mode, Supabase mode and the inactive-project limitation.
- [ ] Run lint, full tests and Docker build in GitHub Actions.
- [ ] Commit and open a Pull Request.
