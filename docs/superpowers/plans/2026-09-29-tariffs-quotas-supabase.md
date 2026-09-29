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
- Test: `tests/db/test_tariff_schema.py`

**Interfaces:**
- Produces new `Plan` pricing/limit fields and `UsageEvent` reservation/billing fields.

- [ ] Write failing schema tests for plan price, cost limit, markup, request reserve, billed USD, plan snapshot, and reservation expiry.
- [ ] Run the focused test and confirm failure.
- [ ] Add model fields and configurable credit unit/reservation TTL.
- [ ] Add the Alembic migration.
- [ ] Run schema tests and migration verification.
- [ ] Commit.

### Task 2: Reservation and settlement service

**Files:**
- Modify: `app/usage/service.py`
- Test: `tests/usage/test_allowance_enforcement.py`

**Interfaces:**
- Produces `UsageReservation`.
- Produces `SubscriptionRequiredError`, `RequestLimitExceededError`, `CreditLimitExceededError`, and `SpendLimitExceededError`.
- Produces `authorize_request()`, `record_success()`, and `record_failure()`.

- [ ] Write failing tests for active subscriptions, each limit, expired reservations, billing calculation, and provider failure release.
- [ ] Run focused tests and confirm failure.
- [ ] Implement active plan lookup and UTC month boundaries.
- [ ] Implement pending reservations with account locking and expiry.
- [ ] Implement exact settlement, credits, billed USD, and failure release.
- [ ] Run focused tests.
- [ ] Commit.

### Task 3: Enforce limits in the chat API

**Files:**
- Modify: `app/api/routes/chat.py`
- Test: `tests/api/test_tariff_enforcement.py`

**Interfaces:**
- Consumes the usage service from Task 2.
- Produces OpenAI-like 402/429 billing errors.

- [ ] Write failing endpoint tests for no plan, exhausted requests, exhausted credits, exhausted spend, success attribution, and provider failure cleanup.
- [ ] Run focused tests and confirm failure.
- [ ] Authorize before the provider call and settle after it.
- [ ] Map billing exceptions to safe public error codes.
- [ ] Run focused and regression tests.
- [ ] Commit.

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

- [ ] Write failing tests for plan creation/update, validation, assignment/removal, dashboard totals, and secret safety.
- [ ] Run focused tests and confirm failure.
- [ ] Add safe numeric form parsing and CSRF-protected routes.
- [ ] Extend reports with current-month allowance and margin data.
- [ ] Add visual forms and tables.
- [ ] Run focused and regression tests.
- [ ] Commit.

### Task 5: Documentation and automated verification

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/operations.md`
- Modify: `docs/superpowers/plans/2026-09-29-tariffs-quotas-supabase.md`

- [ ] Document tariff semantics, soft overrun caveat, Supabase connection, and admin workflow.
- [ ] Run Ruff.
- [ ] Run Alembic against a clean SQLite database.
- [ ] Run the full pytest suite.
- [ ] Confirm Docker build and runtime smoke test pass in GitHub Actions.
- [ ] Mark all completed plan items.
- [ ] Open and merge a pull request only after all checks pass.

### Task 6: Apply the verified schema to Supabase

**External system:** confirmed project `jyipgxceyvzjziqajatn`.

- [ ] Confirm the public schema is empty before changes.
- [ ] Apply base schema, personal-token schema, and tariff-enforcement schema through Supabase migrations.
- [ ] Create/synchronize `alembic_version` with `20260929_03`.
- [ ] Enable RLS and revoke `anon`/`authenticated` access on Shluz tables.
- [ ] Verify tables, constraints, and migration state.
- [ ] Run Supabase security and performance advisors.
- [ ] Do not store the database password or connection string in GitHub.
