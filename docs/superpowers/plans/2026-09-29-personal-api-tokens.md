# Personal API Tokens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single shared technical token with optional personal API tokens that identify the user and channel, while preserving the bootstrap token for local testing.

**Architecture:** Authentication remains inside Shluz. A raw token is shown only once, while the database stores only its SHA-256 hash and a short prefix. The authenticated principal is passed to chat handling so usage is attributed to the correct account and source. Administrative mutations use the existing protected browser panel and a server-generated CSRF token.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, Jinja2, pytest.

**Spec:** `docs/superpowers/specs/2026-09-29-accounts-usage-design.md`

## Global Constraints

- Preserve the bootstrap token for safe local testing.
- Never store or log raw personal tokens.
- Never store prompt or response text in usage accounting.
- Keep SQLite and PostgreSQL/Supabase compatibility.
- Keep the admin workflow visual and Russian-language.
- All changes go through tests, a feature branch, and a pull request.

## Review Focus

- Revoked tokens must stop working immediately.
- Inactive accounts must not authenticate.
- The raw token must appear only in the creation response and never in later pages.
- Usage must be attributed to the personal account and selected source.
- Bootstrap mode must remain functional for local development.

---

### Task 1: Token domain and migration

**Files:**
- Modify: `app/db/models.py`
- Modify: `app/db/repositories.py`
- Create: `migrations/versions/20260929_02_personal_tokens.py`
- Test: `tests/db/test_personal_tokens.py`

**Interfaces:**
- Produces: `AuthenticatedToken`, `authenticate_api_token()`, `generate_api_token()`, `revoke_api_token()`.

- [ ] Write failing tests for generation, hashing, lookup, revocation and inactive accounts.
- [ ] Run the focused tests and confirm they fail for missing behavior.
- [ ] Add token source, secure generation, authentication lookup and revocation.
- [ ] Add the Alembic migration.
- [ ] Run focused tests and migration verification.
- [ ] Commit.

### Task 2: API principal and usage attribution

**Files:**
- Modify: `app/api/auth.py`
- Modify: `app/api/router.py`
- Modify: `app/api/routes/chat.py`
- Test: `tests/api/test_personal_auth.py`

**Interfaces:**
- Consumes: `authenticate_api_token()` from Task 1.
- Produces: `AuthenticatedPrincipal(account_id, source, token_id, bootstrap)`.

- [ ] Write failing tests for valid, invalid, revoked and bootstrap tokens.
- [ ] Write a failing test proving personal usage is stored under the correct account/source.
- [ ] Implement principal resolution and pass it to the chat route.
- [ ] Run focused and full tests.
- [ ] Commit.

### Task 3: Visual account and token management

**Files:**
- Modify: `app/admin/routes.py`
- Modify: `app/templates/admin/users.html`
- Create: `app/templates/admin/token_created.html`
- Modify: `app/static/admin.css`
- Test: `tests/admin/test_token_management.py`

**Interfaces:**
- Consumes: token functions from Task 1.
- Produces: visual account creation, one-time token issuance and revocation.

- [ ] Write failing tests for CSRF protection, account creation, token creation and revocation.
- [ ] Implement a deterministic HMAC CSRF token derived from the protected admin secret.
- [ ] Add forms and a one-time token page.
- [ ] Ensure later pages show only prefix/status, never raw token.
- [ ] Run focused and full tests.
- [ ] Commit.

### Task 4: Documentation and verification

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/operations.md`

- [ ] Document personal tokens, sources and one-time display.
- [ ] Run `ruff check .`.
- [ ] Run `alembic upgrade head` against a clean SQLite database.
- [ ] Run `pytest -v`.
- [ ] Confirm Docker image builds in GitHub Actions.
- [ ] Open a pull request and merge only after all checks pass.
