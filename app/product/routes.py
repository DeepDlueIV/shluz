"""Общий read-only контракт каталога тарифов и собственного расхода для клиентов."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, or_, select

from app.api.auth import AuthenticatedPrincipal, require_api_token
from app.api.dependencies import get_database, get_provider_registry
from app.api.routes.me import _personal
from app.db.database import Database
from app.db.models import ModelPolicy, Plan, UsageEvent
from app.product.service import CHANNELS, models_in
from app.providers.registry import ProviderRegistry

router = APIRouter()
Principal = Annotated[AuthenticatedPrincipal, Depends(require_api_token)]
Db = Annotated[Database, Depends(get_database)]
Registry = Annotated[ProviderRegistry, Depends(get_provider_registry)]


@router.get("/plans")
def plans(principal: Principal, database: Db, registry: Registry, response: Response):
    _personal(principal)
    response.headers["Cache-Control"] = "no-store"
    catalog_ids = [model.id for model in registry.list_models()]
    with database.session() as session:
        source = "harness" if principal.source in {"desktop", "api"} else principal.source
        if source == "site":
            source = "web"
        channel = session.get(ModelPolicy, "channel:" + source)
        # None = без явной политики канала доступен весь реестр провайдера.
        available = (
            models_in(channel.models_json)
            if channel
            else (None if source in CHANNELS else frozenset())
        )
        rows = session.scalars(
            select(Plan).where(Plan.active.is_(True)).order_by(Plan.name).limit(100)
        )
        data = []
        for plan in rows:
            rule = session.get(ModelPolicy, "plan:" + plan.id)
            if rule is None:
                permitted = available
            elif available is None:
                permitted = models_in(rule.models_json)
            else:
                permitted = available & models_in(rule.models_json)
            data.append(
                {
                    "id": plan.id,
                    "name": plan.name,
                    "billing_mode": plan.billing_mode,
                    "monthly_price_usd": format(plan.monthly_price_usd, ".2f")
                    if plan.billing_mode == "subscription"
                    else None,
                    "monthly_request_limit": (plan.monthly_request_limit or None)
                    if plan.billing_mode == "subscription"
                    else None,
                    "models": sorted(permitted) if permitted is not None else catalog_ids,
                }
            )
    return {
        "data": data,
        "channel": principal.source,
        "payment": {"enabled": False, "message": "Оплата ещё не подключена"},
    }


@router.get("/me/usage")
def own_usage(
    principal: Principal,
    database: Db,
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
):
    _personal(principal)
    response.headers["Cache-Control"] = "no-store"
    now = datetime.now(UTC)
    month = datetime(now.year, now.month, 1, tzinfo=UTC)
    with database.session() as session:
        scope = (UsageEvent.account_id == principal.account_id, UsageEvent.created_at >= month)
        successful = (*scope, UsageEvent.status.in_(["success", "interrupted"]))
        unknown = (
            session.scalar(
                select(func.count())
                .select_from(UsageEvent)
                .where(
                    *successful, UsageEvent.quota_kind != "demo", UsageEvent.cost_known.is_(False)
                )
            )
            or 0
        )

        def total(column):
            return (
                session.scalar(
                    select(func.coalesce(func.sum(column), 0)).where(
                        *successful, UsageEvent.usage_known.is_(True)
                    )
                )
                or 0
            )

        known_charge = (
            session.scalar(
                select(func.coalesce(func.sum(UsageEvent.billed_usd), 0)).where(
                    *successful,
                    or_(UsageEvent.cost_known.is_(True), UsageEvent.quota_kind == "demo"),
                )
            )
            or 0
        )
        rows = session.scalars(
            select(UsageEvent)
            .where(*scope)
            .order_by(UsageEvent.created_at.desc(), UsageEvent.id)
            .offset(offset)
            .limit(limit)
        )
        data = []
        for event in rows:
            known = event.quota_kind == "demo" or event.cost_known
            data.append(
                {
                    "request_id": event.id,
                    "model": event.model,
                    "channel": event.source,
                    "status": event.status,
                    "prompt_tokens": event.prompt_tokens if event.usage_known else None,
                    "completion_tokens": event.completion_tokens if event.usage_known else None,
                    "usage_known": event.usage_known,
                    "duration_ms": event.duration_ms,
                    "first_token_ms": event.first_token_ms,
                    "charged_usd": format(event.billed_usd, "f") if known else None,
                    "charged_credits": format(event.billed_usd * 100, "f") if known else None,
                    "billing_mode": "payg"
                    if event.quota_kind == "payg"
                    else ("demo" if event.quota_kind == "demo" else "subscription"),
                    "charge_status": "known" if known else "unknown",
                    "created_at": (
                        event.created_at.replace(tzinfo=UTC)
                        if event.created_at.tzinfo is None
                        else event.created_at
                    ).isoformat(),
                }
            )
        return {
            "scope": "account",
            "period_start": month.isoformat(),
            "currency": "USD",
            "summary": {
                "requests": int(
                    session.scalar(select(func.count()).select_from(UsageEvent).where(*successful))
                    or 0
                ),
                "prompt_tokens": int(total(UsageEvent.prompt_tokens)),
                "completion_tokens": int(total(UsageEvent.completion_tokens)),
                "charged_usd": format(Decimal(known_charge), "f") if not unknown else None,
                "known_charged_usd": format(Decimal(known_charge), "f"),
                "unpriced_requests": unknown,
                "usage_unknown": int(
                    session.scalar(
                        select(func.count())
                        .select_from(UsageEvent)
                        .where(
                            *successful,
                            UsageEvent.usage_known.is_(False),
                        )
                    )
                    or 0
                ),
            },
            "data": data,
            "offset": offset,
            "limit": limit,
        }
