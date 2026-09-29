# Tariffs, Quotas, and Supabase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make tariffs enforce requests, credits, provider cost, pricing, and margin, then deploy the same schema safely to the confirmed Supabase project.

**Architecture:** Extend the existing SQLAlchemy domain and `UsageService`. Personal requests create a short-lived pending usage reservation before provider execution, then settle to exact cost and billable values. Bootstrap access remains unrestricted for local setup. Admin forms manage plans and subscriptions visually.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Jinja2, pytest, PostgreSQL/Supabase, SQLite, Docker.

**Spec:** `docs/superpowers/specs/2026-09-29-tariffs-quotas-supabase-design.md`

## Global Constraints

- Keep SQLite and PostgreSQL compatibility.
- Keep the OpenAI-like API shape.
- Do not persist prompt or response text.
- Do not expose provider cost or secrets through the public API.
- Keep bootstrap access for local setup.
- Calendar periods use UTC.
- One internal credit defaults to `0.001 USD` and is configurable.
- Administrative mutations remain protected by HTTP Basic and CSRF.

## Review Focus

- Two near-simultaneous requests must not trivially bypass request reservations.
- Expired pending reservations must stop consuming allowance.
- Provider failures must release reservations and preserve an error event.
- A disabled plan or expired subscription must block access immediately.
- Historical billed amounts must not change when plan settings change later.

---

### Task 1: Billing schema and migration

**Files:**
- Modify: `app/config.py`
- Modify: `app/db/models.py`
- Create: `migrations/versions/20260929_03_tariff_enforcement.py`
- Create: `migrations/versions/20260929_04_remove_duplicate_indexes.py`
- Test: `tests/db/test_tariff_schema.py`

**Interfaces:**
- Produces new `Plan` pricing/limit fields and `UsageEvent` reservation/billing fields.

- [x] Write failing schema tests for plan price, cost limit, markup, request reserve, billed USD, plan snapshot, and reservation expiry.
- [x] Run the focused test and confirm failure.
- [x] Add model fields and configurable credit unit/reservation TTL.
- [x] Add the Alembic migrations.
- [x] Remove indexes duplicated by unique constraints after the Supabase advisor identified them.
- [x] Run schema tests and migration verification.
- [x] Commit.

### Task 2: Reservation and settlement service

**Files:**
- Modify: `app/usage/service.py`
- Test: `tests/usage/test_allowance_enforcement.py`

**Interfaces:**
- Produces `UsageReservation`.
- Produces `SubscriptionRequiredError`, `RequestLimitExceededError`, `CreditLimitExceededError`, and `SpendLimitExceededError`.
- Produces `authorize_request()`, `record_success()`, and `record_failure()`.

- [x] Write failing tests for active subscriptions, each limit, expired reservations, billing calculation, and provider failure release.
- [x] Run focused tests and confirm failure.
- [x] Implement active plan lookup and UTC month boundaries.
- [x] Implement pending reservations with account locking and expiry.
- [x] Implement exact settlement, credits, billed USD, and failure release.
- [x] Run focused tests.
- [x] Commit.

### Task 3: Enforce limits in the chat API

**Files:**
- Modify: `app/api/routes/chat.py`
- Test: `tests/api/test_tariff_enforcement.py`

**Interfaces:**
- Consumes the usage service from Task 2.
- Produces OpenAI-like 402/429 billing errors.

- [x] Write failing endpoint tests for no plan, exhausted requests, exhausted credits, exhausted spend, success attribution, and provider failure cleanup.
- [x] Run focused tests and confirm failure.
- [x] Authorize before the provider call and settle after it.
- [x] Map billing exceptions to safe public error codes.
- [x] Run focused and regression tests.
- [x] Commit.

### Task 4: Visual plan and subscription management

**Files:**
- Modify: `app/admin/routes.py`
- Modify: `app/db/reports.py`
- Modify: `app/templates/admin/plans.html`
- Modify: `app/templates/admin/users.html`
- Modify: `app/templates/admin/usage.html`
- Modify: `app/static/admin.css`
- Test: `tests/admin/test_tariff_management.py`

**Interfaces:**
- Produces visual plan creation/update and user plan assignment.
- Produces current-month cost, billed amount, margin, limits, and remaining allowance.

- [x] Write failing tests for plan creation/update, validation, assignment/removal, dashboard totals, and secret safety.
- [x] Run focused tests and confirm failure.
- [x] Add safe numeric form parsing and CSRF-protected routes.
- [x] Extend reports with current-month allowance and margin data.
- [x] Add visual forms and tables.
- [x] Run focused and regression tests.
- [x] Commit.

### Task 5: Documentation and automated verification

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/operations.md`
- Modify: `docs/superpowers/plans/2026-09-29-tariffs-quotas-supabase.md`

- [x] Document tariff semantics, soft overrun caveat, Supabase connection, and admin workflow.
- [x] Run Ruff.
- [x] Run Alembic against a clean SQLite database through `20260929_04`.
- [x] Run the full pytest suite.
- [x] Confirm Docker build and runtime smoke test pass in GitHub Actions.
- [x] Mark all completed plan items.
- [ ] Merge the pull request only after the final branch check passes.

### Task 6: Apply the verified schema to Supabase

**External system:** confirmed project `jyipgxceyvzjziqajatn`.

- [x] Confirm the public schema is empty before changes.
- [x] Apply base schema, personal-token schema, tariff-enforcement schema, and duplicate-index cleanup through Supabase migrations.
- [x] Create/synchronize `alembic_version` with `20260929_04`.
- [x] Enable RLS and revoke `anon`/`authenticated` access on Shluz application tables.
- [x] Verify tables, constraints, indexes, and migration state.
- [x] Run Supabase security and performance advisors.
- [x] Do not store the database password or connection string in GitHub.

## Verification Evidence

- GitHub Actions run `36572016257`: Ruff, Alembic, full pytest suite, Docker build, container start, health check, mock chat request, and cleanup completed successfully.
- Supabase migration history contains five applied Shluz migrations.
- `public.alembic_version` contains `20260929_04`.
- Duplicate unique-index advisory was removed after migration `20260929_04`.
- Unused-index notices are expected on the empty new database and must be reassessed after real traffic.
- RLS without public policies is intentional for the six server-only application tables.
