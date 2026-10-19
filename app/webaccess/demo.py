"""Дополнительный общий предохранитель демо: резерв денег и слот в той же SQL-транзакции."""
from decimal import Decimal

from sqlalchemy import func, select

from app.product.service import PolicyError
from app.webaccess.models import DemoHold, DemoModelBound
from app.webaccess.security import control


class DemoPaused(PolicyError):
    code = 'demo_temporarily_unavailable'
    status = 429
    message = 'Бесплатное демо временно недоступно. Попробуйте позже.'


class DemoBusy(PolicyError):
    code = 'demo_busy'
    status = 429
    message = 'Дождитесь завершения текущей генерации.'


def reserve_protection(session, event, config, now):
    cfg = control(session)
    if not cfg.enabled:
        raise DemoPaused()
    epoch = int(now.timestamp())
    active = (DemoHold.active.is_(True), DemoHold.expires > epoch)
    total = session.scalar(select(func.count()).select_from(DemoHold).where(*active)) or 0
    own = session.scalar(select(func.count()).select_from(DemoHold).where(
        *active, DemoHold.account_id == event.account_id)) or 0
    if own or total >= cfg.concurrent:
        raise DemoBusy()
    amount = Decimal('0')
    if (event.provider, event.model) != ('mock', 'mock-chat'):
        bound = session.get(DemoModelBound, event.provider + ':' + event.model)
        if (not bound or not bound.reviewed or bound.max_cost_usd <= 0
                or config.demo_max_input_chars > bound.max_input_chars
                or config.demo_max_output_tokens > bound.max_output_tokens):
            raise DemoPaused()
        amount = bound.max_cost_usd
        if cfg.daily_usd <= 0:
            raise DemoPaused()
    day = epoch // 86400
    reserved = session.scalar(select(func.coalesce(func.sum(DemoHold.amount), 0)).where(
        DemoHold.day == day)) or Decimal('0')
    if amount and reserved + amount > cfg.daily_usd:
        raise DemoPaused()
    session.add(DemoHold(event_id=event.id, account_id=event.account_id, day=day,
                         amount=amount, active=True, uncertain=True,
                         expires=epoch + cfg.timeout_seconds + 10))
    session.flush()
    return cfg.timeout_seconds, cfg.idle_seconds


def settle_protection(session, event, result=None, definite=False):
    hold = session.get(DemoHold, event.id)
    if hold is None or not hold.active:
        return
    hold.active = False
    if definite:
        hold.amount, hold.uncertain = Decimal('0'), False
    elif result is not None and result.cost_known:
        actual = Decimal(result.cost_usd)
        if not actual.is_finite() or actual < 0:
            return  # Неизвестный/недопустимый расход сохраняет резерв.
        if actual > hold.amount:
            # Проверенная оценка перестала ограничивать расход: запрещаем следующие демо.
            control(session).enabled = False
        hold.amount, hold.uncertain = actual, False
    # Обрыв/рестарт не возвращает финансовый резерв; слот и деньги — разные величины.
