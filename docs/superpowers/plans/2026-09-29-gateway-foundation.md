# Gateway Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Собрать переносимый, тестируемый фундамент Shluz с OpenAI-совместимым API, mock-провайдером, браузерной админ-панелью и Docker-упаковкой.

**Architecture:** Один модульный FastAPI-сервис. Публичные маршруты работают через независимый интерфейс провайдера; первая реализация — `MockProvider`. Админ-панель рендерится сервером и не требует отдельного frontend-проекта.

**Tech Stack:** Python 3.12, FastAPI, Pydantic Settings, Jinja2, HTMX, pytest, HTTPX, Ruff, Docker, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-29-central-api-gateway-design.md`

## Global Constraints

- Реальные API-ключи и пароли никогда не коммитятся в GitHub.
- Публичные методы по возможности совместимы с OpenAI API.
- Код не зависит от Venice, Supabase или конкретного Harness.
- Все настройки задаются переменными окружения.
- Админ-панель доступна через браузер и защищена авторизацией.
- Один Docker-образ должен запускать весь первый этап.
- Первый этап не подключает Supabase, платежи и реальные AI-провайдеры.
- Локальная разработка должна работать на Windows; production-контейнер — на Linux.

## Review Focus

- Отсутствующий или неверный пользовательский Bearer-токен должен давать `401`, не раскрывая конфигурацию.
- Некорректное тело OpenAI-запроса должно давать предсказуемую ошибку валидации.
- Неизвестная модель должна давать понятную ошибку, а не падение сервера.
- Ошибка провайдера должна превращаться в контролируемый `502` без трассировки и секретов.
- Админ-панель без правильных данных входа должна быть недоступна.
- HTML, JSON, логи и `/docs` не должны содержать значения секретов.

---

### Task 1: Модульный запуск приложения и конфигурация

**Files:**
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `app/factory.py`
- Modify: `app/main.py`
- Create: `tests/conftest.py`
- Create: `tests/test_health.py`
- Create: `pyproject.toml`
- Delete after migration: `requirements.txt`

**Interfaces:**
- Produces: `Settings`, `get_settings()`, `create_app(settings: Settings | None = None) -> FastAPI`.
- Produces: `GET /health` returning service name, version and status.

- [ ] **Step 1: Write the failing health and app-factory tests**

```python
def test_health_returns_service_metadata(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "shluz",
        "version": "0.1.0",
    }


def test_app_factory_uses_injected_settings(test_settings):
    app = create_app(test_settings)
    assert app.state.settings is test_settings
```

- [ ] **Step 2: Run tests to verify RED**

Run: `pytest tests/test_health.py -v`  
Expected: FAIL because `create_app`, `Settings` and metadata response do not exist.

- [ ] **Step 3: Implement settings and app factory**

Implement:

```python
class Settings(BaseSettings): ...
def get_settings() -> Settings: ...
def create_app(settings: Settings | None = None) -> FastAPI: ...
```

Required setting names:

- `service_name="shluz"`
- `service_version="0.1.0"`
- `environment="development"`
- `bootstrap_api_token`
- `admin_username`
- `admin_password`

`app/main.py` must only expose `app = create_app()`.

- [ ] **Step 4: Run the complete suite**

Run: `pytest -v`  
Expected: PASS.

- [ ] **Step 5: Run lint**

Run: `ruff check .`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app tests pyproject.toml requirements.txt
git commit -m "refactor: introduce app factory and typed settings"
```

### Task 2: Независимый слой провайдеров

**Files:**
- Create: `app/providers/__init__.py`
- Create: `app/providers/base.py`
- Create: `app/providers/mock.py`
- Create: `app/providers/registry.py`
- Create: `tests/providers/test_mock_provider.py`
- Create: `tests/providers/test_registry.py`

**Interfaces:**
- Consumes: `Settings` from Task 1.
- Produces: `ChatMessage`, `ChatRequest`, `ChatResult`, `ModelInfo`, `ProviderError`, `ModelNotFoundError`, `Provider` protocol.
- Produces: `MockProvider` and `ProviderRegistry`.

- [ ] **Step 1: Write failing tests for model listing and chat**

Assertions:

```python
assert provider.list_models()[0].id == "mock-chat"
assert provider.chat_completion(request).content == "Shluz mock reply: Привет"
```

- [ ] **Step 2: Run provider tests to verify RED**

Run: `pytest tests/providers -v`  
Expected: FAIL because provider interfaces do not exist.

- [ ] **Step 3: Implement provider value objects and protocol**

Exact public methods:

```python
class Provider(Protocol):
    @property
    def name(self) -> str: ...
    def list_models(self) -> list[ModelInfo]: ...
    def chat_completion(self, request: ChatRequest) -> ChatResult: ...
```

- [ ] **Step 4: Implement `MockProvider` and `ProviderRegistry`**

Registry methods:

```python
def list_models(self) -> list[ModelInfo]: ...
def provider_for_model(self, model_id: str) -> Provider: ...
```

Unknown model raises `ModelNotFoundError`.

- [ ] **Step 5: Run tests and lint**

Run: `pytest tests/providers -v && pytest -v && ruff check .`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/providers tests/providers
git commit -m "feat: add provider abstraction and mock provider"
```

### Task 3: OpenAI-совместимый API и временная авторизация

**Files:**
- Create: `app/api/__init__.py`
- Create: `app/api/auth.py`
- Create: `app/api/dependencies.py`
- Create: `app/api/schemas.py`
- Create: `app/api/router.py`
- Create: `app/api/routes/__init__.py`
- Create: `app/api/routes/chat.py`
- Create: `app/api/routes/models.py`
- Modify: `app/factory.py`
- Create: `tests/api/test_auth.py`
- Create: `tests/api/test_models.py`
- Create: `tests/api/test_chat.py`

**Interfaces:**
- Consumes: `Settings`, `ProviderRegistry`, `ChatRequest`, `ModelNotFoundError`, `ProviderError`.
- Produces: authenticated `GET /v1/models` and `POST /v1/chat/completions`.

- [ ] **Step 1: Write failing authentication tests**

Required behavior:

- no `Authorization` header → `401`;
- wrong token → `401`;
- `Bearer <bootstrap_api_token>` → request proceeds.

- [ ] **Step 2: Write failing model and chat tests**

Required behavior:

- `/v1/models` returns OpenAI-style `{object: "list", data: [...]}`;
- valid chat returns `chat.completion` with mock reply;
- unknown model returns `404` with an `error` object;
- malformed payload returns `422`;
- `ProviderError` returns `502` with an `error` object and no traceback.

- [ ] **Step 3: Run API tests to verify RED**

Run: `pytest tests/api -v`  
Expected: FAIL because routes and auth do not exist.

- [ ] **Step 4: Implement Bearer-token dependency and API schemas**

Public dependency:

```python
def require_api_token(...) -> None: ...
```

Never include configured token in response details or logs.

- [ ] **Step 5: Implement model and chat routes**

Responses must follow the subset of OpenAI shapes pinned by tests. Generate a unique `chatcmpl-...` id and Unix `created` timestamp.

- [ ] **Step 6: Implement controlled exception mapping**

Map:

- `ModelNotFoundError` → `404` / `model_not_found`;
- `ProviderError` → `502` / `provider_error`.

- [ ] **Step 7: Run tests and lint**

Run: `pytest tests/api -v && pytest -v && ruff check .`  
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/api app/factory.py tests/api
git commit -m "feat: expose authenticated OpenAI-compatible API"
```

### Task 4: Лёгкая браузерная админ-панель

**Files:**
- Create: `app/admin/__init__.py`
- Create: `app/admin/auth.py`
- Create: `app/admin/routes.py`
- Create: `app/templates/admin/index.html`
- Create: `app/static/admin.css`
- Modify: `app/factory.py`
- Create: `tests/admin/test_admin.py`

**Interfaces:**
- Consumes: `Settings`, `ProviderRegistry`.
- Produces: protected `GET /admin` HTML page.

- [ ] **Step 1: Write failing admin authentication tests**

Required behavior:

- no HTTP Basic credentials → `401` and `WWW-Authenticate: Basic`;
- wrong credentials → `401`;
- correct credentials → `200`.

- [ ] **Step 2: Write failing dashboard-content test**

Page must show:

- `Shluz`;
- environment name;
- service version;
- provider name `mock`;
- model count;
- links to `/docs` and `/health`.

It must not contain configured API token or admin password.

- [ ] **Step 3: Run admin tests to verify RED**

Run: `pytest tests/admin -v`  
Expected: FAIL because admin UI does not exist.

- [ ] **Step 4: Implement constant-time HTTP Basic verification**

Use `secrets.compare_digest` for username and password comparisons.

- [ ] **Step 5: Implement server-rendered dashboard**

Use Jinja2 templates and plain CSS. HTMX may be loaded from a pinned CDN URL, but the page must remain useful without JavaScript.

- [ ] **Step 6: Run tests and lint**

Run: `pytest tests/admin -v && pytest -v && ruff check .`  
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/admin app/templates app/static app/factory.py tests/admin
git commit -m "feat: add protected visual admin dashboard"
```

### Task 5: Docker, CI и понятная эксплуатация

**Files:**
- Create: `Dockerfile`
- Create: `compose.yaml`
- Create: `.dockerignore`
- Create: `.github/workflows/ci.yml`
- Create: `docs/operations.md`
- Modify: `.env.example`
- Modify: `.gitignore`
- Modify: `README.md`
- Delete: `CONNECTION_TEST.md`

**Interfaces:**
- Consumes: complete application from Tasks 1–4.
- Produces: reproducible local and container start commands plus CI verification.

- [ ] **Step 1: Add a smoke test for the production app import**

```python
def test_production_app_imports():
    from app.main import app
    assert app.title == "Shluz"
```

- [ ] **Step 2: Run smoke test to verify current state**

Run: `pytest tests/test_smoke.py -v`  
Expected: PASS only after the application factory work is complete.

- [ ] **Step 3: Add Docker packaging**

Requirements:

- Python 3.12 slim base;
- non-root application user;
- port `8000`;
- command `uvicorn app.main:app --host 0.0.0.0 --port 8000`;
- Docker healthcheck calls `/health`;
- `.env` is never copied into image.

- [ ] **Step 4: Add `compose.yaml` for one-command local start**

The service reads `.env`, exposes `8000:8000`, restarts unless stopped, and contains no real secrets.

- [ ] **Step 5: Add GitHub Actions CI**

On pull requests and pushes to `main`, run:

```bash
pip install -e ".[dev]"
ruff check .
pytest -v
```

Also build the Docker image without publishing it.

- [ ] **Step 6: Update documentation**

README must contain:

- non-technical architecture diagram;
- Windows local start;
- Docker start;
- addresses `/admin`, `/docs`, `/health`;
- clear warning not to commit `.env`;
- current limitations and next phase.

`docs/operations.md` must explain environment variables, backup expectations, deployment checklist and secret rotation in plain language.

- [ ] **Step 7: Run final verification**

Run:

```bash
ruff check .
pytest -v
docker build -t shluz:test .
```

Expected: all commands succeed.

- [ ] **Step 8: Commit**

```bash
git add .
git commit -m "build: add portable Docker and CI foundation"
```
