"""Серверные права и пожизненное демо; сетевой вызов всегда вне транзакции."""
import json
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import func, or_, select, text

from app.db.models import DemoGrant, ModelPolicy, Plan, ProductPolicy, Subscription, UsageEvent
from app.webaccess.models import DemoLimit

CHANNELS = ('telegram', 'web', 'harness')
DEFAULT_MODELS = frozenset({'mock-chat'})


class PolicyError(RuntimeError):
    code = 'model_not_allowed'
    status = 403
    message = 'This model is not allowed for this account and channel'


class DemoExhausted(PolicyError):
    code = 'demo_exhausted'
    status = 429
    message = 'The one-time demo allowance is exhausted'


class InputTooLarge(PolicyError):
    code = 'input_too_large'
    status = 400
    message = 'The request exceeds the configured input or output limit'


class TemporarilyLimited(PolicyError):
    code = 'request_rate_limited'
    status = 429
    message = 'Too many requests; try again later'


class DemoDailyLimit(PolicyError):
    code = 'demo_temporarily_unavailable'
    status = 429
    message = 'Demo is temporarily paused by the daily protection limit'


@contextmanager
def locked_session(database):
    with database.session() as session:
        if database.engine.dialect.name == 'sqlite':
            session.connection().exec_driver_sql('BEGIN IMMEDIATE')
        elif database.engine.dialect.name == 'postgresql':
            session.execute(text('SELECT pg_advisory_xact_lock(1397247035)'))
        yield session


def policy(session):
    return session.get(ProductPolicy, 'default') or ProductPolicy(
        id='default', mode='demo', demo_requests=1, demo_models_json='["mock-chat"]',
        demo_max_input_chars=3000, demo_max_output_tokens=512,
        demo_daily_requests=100, requests_per_minute=30,
    )


def grant_demo(session, account_id):
    existing = session.get(DemoGrant, account_id)
    if existing is None:
        existing = DemoGrant(account_id=account_id, requests_used=0)
        session.add(existing)
        session.flush()
    if session.get(DemoLimit, account_id) is None:
        session.add(DemoLimit(account_id=account_id, requests_limit=policy(session).demo_requests))
        session.flush()
    return existing


def grant_limit(session, account_id):
    snapshot = session.get(DemoLimit, account_id)
    return snapshot.requests_limit if snapshot else policy(session).demo_requests


def active_subscription(session, account_id, now=None):
    now = now or datetime.now(UTC)
    return session.execute(
        select(Subscription, Plan).join(Plan, Plan.id == Subscription.plan_id).where(
            Subscription.account_id == account_id, Subscription.status == 'active',
            Subscription.starts_at <= now,
            or_(Subscription.ends_at.is_(None), Subscription.ends_at > now),
            Plan.active.is_(True),
        ).order_by(Subscription.starts_at.desc(), Subscription.created_at.desc())
    ).first()


def models_in(raw):
    try:
        data = json.loads(raw)
        if isinstance(data, list) and all(isinstance(v, str) for v in data):
            return frozenset(data)
    except (TypeError, ValueError):
        pass
    return frozenset()


def allowed_models(session, account_id, source, now=None):
    if account_id == 'bootstrap':
        return None
    source = {'desktop': 'harness', 'api': 'harness', 'site': 'web'}.get(source, source)
    if source not in CHANNELS:
        return frozenset()
    channel = session.get(ModelPolicy, 'channel:' + source)
    models = models_in(channel.models_json) if channel else DEFAULT_MODELS
    current = active_subscription(session, account_id, now)
    if current:
        rule = session.get(ModelPolicy, 'plan:' + current[1].id)
        return models & models_in(rule.models_json) if rule else models
    if session.get(DemoGrant, account_id):
        return models & models_in(policy(session).demo_models_json)
    return frozenset()


def reserve_demo(session, account_id, config, now):
    grant = session.get(DemoGrant, account_id)
    if grant is None:
        return False
    if grant.requests_used >= grant_limit(session, account_id):
        raise DemoExhausted()
    day = datetime(now.year, now.month, now.day, tzinfo=UTC)
    attempted = session.scalar(select(func.count()).select_from(UsageEvent).where(
        UsageEvent.quota_kind == 'demo', UsageEvent.created_at >= day)) or 0
    if attempted >= config.demo_daily_requests:
        raise DemoDailyLimit()
    grant.requests_used += 1
    return True


def demo_allowance(session, account_id):
    grant = session.get(DemoGrant, account_id)
    if grant is None:
        return None
    limit = grant_limit(session, account_id)
    return {'limit': limit, 'used': grant.requests_used, 'pending': 0,
            'remaining': max(0, limit - grant.requests_used)}


def limit_rate(session, account_id, config, now):
    start = now.replace(second=0, microsecond=0)
    count = session.scalar(select(func.count()).select_from(UsageEvent).where(
        UsageEvent.account_id == account_id, UsageEvent.created_at >= start)) or 0
    if count >= config.requests_per_minute:
        raise TemporarilyLimited()
