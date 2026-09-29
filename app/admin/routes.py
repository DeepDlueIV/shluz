import secrets
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.db.models import (
    Account,
    AccountBalance,
    ApiToken,
    Identity,
    Plan,
    Subscription,
    UsageEvent,
)
from app.db.session import SessionFactory
from app.providers.base import ProviderError
from app.providers.registry import ProviderRegistry

router = APIRouter(prefix="/admin", tags=["admin"])
security = HTTPBasic()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parents[1] / "templates")
)


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _registry(request: Request) -> ProviderRegistry:
    return request.app.state.provider_registry


def _database(request: Request) -> SessionFactory:
    return request.app.state.session_factory


def _require_admin(
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
    settings: Annotated[Settings, Depends(_settings)],
) -> str:
    username_ok = secrets.compare_digest(credentials.username, settings.admin_username)
    password_ok = secrets.compare_digest(
        credentials.password,
        settings.admin_password.get_secret_value(),
    )
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверные данные администратора",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _secret_is_configured(value: Any) -> bool:
    if value is None:
        return False
    getter = getattr(value, "get_secret_value", None)
    resolved = getter() if callable(getter) else str(value)
    return bool(resolved.strip())


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _dashboard_counts(session_factory: SessionFactory) -> dict[str, Any]:
    try:
        with session_factory() as session:
            return {
                "account_count": session.scalar(select(func.count(Account.id))) or 0,
                "plan_count": session.scalar(select(func.count(Plan.id))) or 0,
                "usage_count": session.scalar(select(func.count(UsageEvent.id))) or 0,
                "total_cost": _decimal(
                    session.scalar(select(func.sum(UsageEvent.cost_usd)))
                ),
            }
    except SQLAlchemyError:
        return {
            "account_count": "—",
            "plan_count": "—",
            "usage_count": "—",
            "total_cost": "—",
        }


@router.get("", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    _: Annotated[str, Depends(_require_admin)],
    settings: Annotated[Settings, Depends(_settings)],
    registry: Annotated[ProviderRegistry, Depends(_registry)],
    session_factory: Annotated[SessionFactory, Depends(_database)],
) -> HTMLResponse:
    model_warning = None
    try:
        model_count: int | str = len(registry.list_models())
    except ProviderError:
        model_count = "—"
        model_warning = "Список моделей провайдера временно недоступен"

    return templates.TemplateResponse(
        request=request,
        name="admin/index.html",
        context={
            "environment": settings.environment,
            "version": settings.service_version,
            "model_count": model_count,
            "provider_names": registry.provider_names(),
            "model_warning": model_warning,
            **_dashboard_counts(session_factory),
        },
    )


@router.get("/users", response_class=HTMLResponse)
def users_dashboard(
    request: Request,
    _: Annotated[str, Depends(_require_admin)],
    session_factory: Annotated[SessionFactory, Depends(_database)],
) -> HTMLResponse:
    with session_factory() as session:
        accounts = list(
            session.scalars(select(Account).order_by(Account.created_at.desc()))
        )
        identities = list(session.scalars(select(Identity)))
        balances = list(session.scalars(select(AccountBalance)))
        active_subscriptions = session.execute(
            select(Subscription, Plan)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(Subscription.status == "active")
            .order_by(Subscription.created_at.desc())
        ).all()
        usage_rows = session.execute(
            select(
                UsageEvent.account_id,
                func.count(UsageEvent.id),
                func.sum(UsageEvent.cost_usd),
            )
            .where(UsageEvent.account_id.is_not(None))
            .group_by(UsageEvent.account_id)
        ).all()

    identity_map: dict[Any, list[str]] = {}
    for identity in identities:
        label = identity.label or f"{identity.kind}: {identity.external_id}"
        identity_map.setdefault(identity.account_id, []).append(label)

    plan_map: dict[Any, Plan] = {}
    for subscription, plan in active_subscriptions:
        plan_map.setdefault(subscription.account_id, plan)

    balance_map = {balance.account_id: balance for balance in balances}
    usage_map = {
        account_id: {"requests": count, "cost": _decimal(cost)}
        for account_id, count, cost in usage_rows
    }
    rows = [
        {
            "account": account,
            "identities": identity_map.get(account.id, []),
            "plan": plan_map.get(account.id),
            "balance": balance_map.get(account.id),
            "requests": usage_map.get(account.id, {}).get("requests", 0),
            "cost": usage_map.get(account.id, {}).get("cost", Decimal("0")),
        }
        for account in accounts
    ]
    return templates.TemplateResponse(
        request=request,
        name="admin/users.html",
        context={"rows": rows},
    )


@router.get("/plans", response_class=HTMLResponse)
def plans_dashboard(
    request: Request,
    _: Annotated[str, Depends(_require_admin)],
    session_factory: Annotated[SessionFactory, Depends(_database)],
) -> HTMLResponse:
    with session_factory() as session:
        plans = list(session.scalars(select(Plan).order_by(Plan.created_at.desc())))
        counts = dict(
            session.execute(
                select(Subscription.plan_id, func.count(Subscription.id)).group_by(
                    Subscription.plan_id
                )
            ).all()
        )

    rows = [
        {
            "plan": plan,
            "monthly_price": f"{Decimal(plan.monthly_price_minor) / Decimal('100'):.2f}",
            "subscriptions": counts.get(plan.id, 0),
        }
        for plan in plans
    ]
    return templates.TemplateResponse(
        request=request,
        name="admin/plans.html",
        context={"rows": rows},
    )


@router.get("/usage", response_class=HTMLResponse)
def usage_dashboard(
    request: Request,
    _: Annotated[str, Depends(_require_admin)],
    session_factory: Annotated[SessionFactory, Depends(_database)],
) -> HTMLResponse:
    with session_factory() as session:
        events = list(
            session.scalars(
                select(UsageEvent).order_by(UsageEvent.created_at.desc()).limit(100)
            )
        )
        request_count = session.scalar(select(func.count(UsageEvent.id))) or 0
        failed_count = (
            session.scalar(
                select(func.count(UsageEvent.id)).where(UsageEvent.status == "failed")
            )
            or 0
        )
        total_cost = _decimal(
            session.scalar(select(func.sum(UsageEvent.cost_usd)))
        )
        total_tokens = (
            session.scalar(
                select(
                    func.sum(
                        UsageEvent.prompt_tokens + UsageEvent.completion_tokens
                    )
                )
            )
            or 0
        )

    return templates.TemplateResponse(
        request=request,
        name="admin/usage.html",
        context={
            "events": events,
            "request_count": request_count,
            "failed_count": failed_count,
            "total_cost": total_cost,
            "total_tokens": total_tokens,
        },
    )


@router.get("/tokens", response_class=HTMLResponse)
def tokens_dashboard(
    request: Request,
    _: Annotated[str, Depends(_require_admin)],
    session_factory: Annotated[SessionFactory, Depends(_database)],
) -> HTMLResponse:
    with session_factory() as session:
        tokens = list(
            session.scalars(select(ApiToken).order_by(ApiToken.created_at.desc()))
        )
        account_ids = {token.account_id for token in tokens}
        accounts = (
            list(session.scalars(select(Account).where(Account.id.in_(account_ids))))
            if account_ids
            else []
        )

    account_map = {account.id: account.display_name for account in accounts}
    rows = [
        {
            "account_name": account_map.get(
                token.account_id,
                "Неизвестный пользователь",
            ),
            "name": token.name,
            "prefix": token.token_prefix,
            "scopes": tuple(token.scopes),
            "status": token.status,
            "expires_at": token.expires_at,
            "last_used_at": token.last_used_at,
        }
        for token in tokens
    ]
    return templates.TemplateResponse(
        request=request,
        name="admin/tokens.html",
        context={"rows": rows},
    )


@router.get("/providers/venice", response_class=HTMLResponse)
def venice_dashboard(
    request: Request,
    _: Annotated[str, Depends(_require_admin)],
    settings: Annotated[Settings, Depends(_settings)],
    registry: Annotated[ProviderRegistry, Depends(_registry)],
) -> HTMLResponse:
    provider = registry.provider_by_name("venice")
    models = []
    snapshot = None
    warnings: list[str] = []

    if provider is not None:
        try:
            catalog = getattr(provider, "list_model_catalog", provider.list_models)
            models = catalog()
        except ProviderError:
            warnings.append("Каталог моделей Venice временно недоступен")

        try:
            get_snapshot = provider.get_account_snapshot
            snapshot = get_snapshot(lookback=settings.venice_analytics_lookback)
        except (AttributeError, ProviderError):
            warnings.append("Состояние аккаунта Venice временно недоступно")

    return templates.TemplateResponse(
        request=request,
        name="admin/provider.html",
        context={
            "connected": provider is not None,
            "key_configured": _secret_is_configured(settings.venice_api_key),
            "lookback": settings.venice_analytics_lookback,
            "models": models,
            "snapshot": snapshot,
            "warnings": tuple(warnings),
        },
    )
