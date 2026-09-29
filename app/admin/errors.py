import re

from fastapi import Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from app.admin.routes import _admin_csrf_token, templates
from app.db.reports import list_plans_overview

_PLAN_FORM = re.compile(r"^/admin/plans(?:/([^/]+))?/?$")


async def admin_http_error(request: Request, exc: HTTPException):
    if not request.url.path.startswith("/admin"):
        return await http_exception_handler(request, exc)
    actor = getattr(request.state, "admin_actor", None)
    match = _PLAN_FORM.fullmatch(request.url.path)
    values = getattr(request.state, "admin_form", None)
    if (
        actor and match and request.method == "POST" and values is not None
        and exc.status_code in {409, 422}
    ):
        with request.app.state.database.session() as session:
            plans = list_plans_overview(session)
        return templates.TemplateResponse(
            request=request, name="admin/plans.html", status_code=exc.status_code,
            context={
                "plans": plans, "form_values": values, "form_error": str(exc.detail),
                "editing_plan_id": match.group(1) or "", "show_archived": False,
                "csrf_token": _admin_csrf_token(request.app.state.settings),
            }, headers={"Cache-Control": "no-store"},
        )
    return templates.TemplateResponse(
        request=request, name="admin/error.html", status_code=exc.status_code,
        context={"error_message": str(exc.detail), "status_code": exc.status_code},
        headers={**(exc.headers or {}), "Cache-Control": "no-store"},
    )


async def admin_validation_error(request: Request, exc: RequestValidationError):
    if not request.url.path.startswith("/admin"):
        return await request_validation_exception_handler(request, exc)
    return await admin_http_error(
        request, HTTPException(status_code=422, detail="Проверьте заполнение полей формы"),
    )
