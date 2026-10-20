"""Закрытая диагностика принятых запросов: из БД, без текста диалогов."""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.admin.routes import _database, _require_admin, templates
from app.db.models import UsageEvent

router = APIRouter(prefix="/admin/diagnostics", tags=["admin"])
Admin = Annotated[str, Depends(_require_admin)]


def average(values):
    rows = [v for v in values if v is not None]
    return round(sum(rows) / len(rows), 1) if rows else None


def report(database):
    since = datetime.now(UTC) - timedelta(hours=24)
    with database.session() as session:
        rows = list(
            session.scalars(
                select(UsageEvent)
                .where(
                    UsageEvent.created_at >= since,
                )
                .order_by(UsageEvent.created_at.desc())
                .limit(2000)
            )
        )
    groups = defaultdict(list)
    for row in rows:
        groups[(row.provider, row.model, row.source)].append(row)
    models = []
    for (provider, model, channel), items in groups.items():
        durations = sorted(r.duration_ms for r in items if r.duration_ms is not None)
        models.append(
            {
                "provider": provider,
                "model": model,
                "channel": channel,
                "requests": len(items),
                "errors": sum(r.status in {"failed", "interrupted"} for r in items),
                "duration_ms": average(durations),
                "p95_ms": durations[min(len(durations) - 1, int(len(durations) * 0.95))]
                if durations
                else None,
                "first_token_ms": average([r.first_token_ms for r in items]),
                "input_tokens": sum(r.prompt_tokens for r in items if r.usage_known),
                "output_tokens": sum(r.completion_tokens for r in items if r.usage_known),
                "usage_unknown": sum(not r.usage_known for r in items),
                "provider_cost_usd": str(
                    sum((r.cost_usd for r in items if r.cost_known), Decimal(0))
                ),
                "cost_unknown": sum(not r.cost_known for r in items),
            }
        )
    errors = defaultdict(int)
    for row in rows:
        if row.error_code:
            errors[row.error_code] += 1
    return {
        "window_hours": 24,
        "sample_limit": 2000,
        "limited": len(rows) == 2000,
        "summary": {
            "requests": len(rows),
            "errors": sum(errors.values()),
            "streamed": sum(r.streamed for r in rows),
            "pending": sum(r.status == "pending" for r in rows),
        },
        "models": models,
        "errors": dict(errors),
    }


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, _: Admin):
    return templates.TemplateResponse(
        request=request,
        name="admin/diagnostics.html",
        context={
            "report": report(_database(request)),
            "export_enabled": bool(request.app.state.settings.otlp_endpoint),
        },
    )


@router.get("/data")
def data(request: Request, _: Admin):
    return report(_database(request))
