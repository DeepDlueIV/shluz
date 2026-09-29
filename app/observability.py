import json
import logging
import sys
import time
import uuid
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse


class _QueryFilter(logging.Filter):
    """Убирает query string из стандартного access-журнала Uvicorn."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            args[2] = str(args[2]).partition("?")[0]
            record.args = tuple(args)
        return True


def install_observability(app: FastAPI) -> None:
    logger = logging.getLogger("shluz.access")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    uvicorn_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, _QueryFilter) for item in uvicorn_logger.filters):
        uvicorn_logger.addFilter(_QueryFilter())

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        started = time.perf_counter()
        request_id = uuid.uuid4().hex
        error_type = None
        try:
            response = await call_next(request)
        except Exception as exc:
            # Не записываем str(exc), тело запроса, SQL-параметры или секреты.
            error_type = type(exc).__name__
            if request.url.path.startswith("/admin"):
                response = HTMLResponse(
                    '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
                    '<link rel="stylesheet" href="/static/admin.css"></head>'
                    '<body><main class="shell"><h1>Не удалось выполнить действие</h1>'
                    '<p>Вернитесь в панель и проверьте состояние. '
                    'Не повторяйте платный запрос автоматически.</p>'
                    f'<p>Номер ошибки: <code>{request_id}</code></p>'
                    '<a href="/admin">В панель</a></main></body></html>',
                    status_code=500,
                )
            else:
                response = JSONResponse(status_code=500, content={
                    "error": {"message": "Internal server error", "type": "server_error",
                              "code": "internal_error", "request_id": request_id},
                })
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith("/admin"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "same-origin"
        route = request.scope.get("route")
        entry = {
            "time": datetime.now(UTC).isoformat(), "event": "http_request",
            "request_id": request_id, "method": request.method,
            "route": getattr(route, "path", "unmatched"),
            "status": response.status_code,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }
        if error_type:
            entry["error_type"] = error_type
        logger.log(
            logging.ERROR if response.status_code >= 500 else logging.INFO,
            json.dumps(entry, ensure_ascii=False),
        )
        return response
