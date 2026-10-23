"""Не отражаем содержимое файлов, инструментов и prompt в ошибке контракта."""

from fastapi.responses import JSONResponse


async def api_validation_error(request, exc):
    if request.url.path.rstrip("/") == "/v1/chat/completions":
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "message": "Unsupported or invalid chat request; see desktop-agent-api.md",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "invalid_request",
                }
            },
            headers={"Cache-Control": "no-store"},
        )
    from app.webaccess.routes import web_validation_error

    return await web_validation_error(request, exc)
