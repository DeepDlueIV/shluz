# Personal API Tokens Implementation Plan

> **Status:** completed and verified in GitHub Actions.

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

- [x] Write failing tests for generation, hashing, lookup, revocation and inactive accounts.
- [x] Run the focused tests and confirm they fail for missing behavior.
- [x] Add token source, secure generation, authentication lookup and revocation.
- [x] Add the Alembic migration.
- [x] Run focused tests and migration verification.
- [x] Commit.

### Task 2: API principal and usage attribution

**Files:**
- Modify: `app/api/auth.py`
- Modify: `app/api/routes/chat.py`
- Test: `tests/api/test_personal_auth.py`

- [x] Write failing tests for valid, invalid, revoked and bootstrap tokens.
- [x] Write a failing test proving personal usage is stored under the correct account/source.
- [x] Implement principal resolution and pass it to the chat route.
- [x] Run focused and full tests.
- [x] Commit.

### Task 3: Visual account and token management

**Files:**
- Modify: `app/admin/routes.py`
- Modify: `app/templates/admin/users.html`
- Create: `app/templates/admin/token_created.html`
- Modify: `app/static/admin.css`
- Test: `tests/admin/test_token_management.py`

- [x] Write failing tests for CSRF protection, account creation, token creation and revocation.
- [x] Implement a deterministic HMAC CSRF token derived from the protected admin secret.
- [x] Add forms and a one-time token page.
- [x] Ensure later pages show only prefix/status, never raw token.
- [x] Prevent browsers and proxies from caching the one-time token page.
- [x] Run focused and full tests.
- [x] Commit.

### Task 4: Documentation and verification

**Files:**
- Modify: `README.md`
- Modify: `docs/operations.md`

- [x] Document personal tokens, sources, revocation and one-time display.
- [x] Confirm no new environment variable is required, so `.env.example` remains unchanged.
- [x] Run `ruff check .`.
- [x] Run `alembic upgrade head` against a clean SQLite database.
- [x] Run `pytest -v`.
- [x] Confirm Docker image builds in GitHub Actions.
- [x] Open a pull request and merge only after all checks pass.

## Verification record

GitHub Actions verified linting, a clean Alembic migration, the full pytest suite and Docker image assembly. The no-cache protection was added through a separate red-green regression test.
