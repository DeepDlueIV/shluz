"""PAYG: вызывать изменения только внутри product.locked_session, без сетевых вызовов."""

import time
from decimal import ROUND_CEILING, Decimal
from hashlib import sha256

from sqlalchemy import func, select

from app.billing.models import Wallet, WalletEntry, WalletHold, WalletModelBound
from app.db.models import Account
from app.product.service import PolicyError

QUANTUM = Decimal("0.00000001")


class WalletUnavailable(PolicyError):
    code = "wallet_insufficient"
    status = 402
    message = "Недостаточно доступных средств. Пополните баланс."


class PriceUnavailable(PolicyError):
    code = "wallet_price_unavailable"
    status = 503
    message = "Цена и предел запроса этой модели ещё не проверены. Выберите другую модель."


def wallet(session, account_id):
    row = session.get(Wallet, account_id)
    if row is None:
        row = Wallet(account_id=account_id, balance_usd=Decimal("0"), display_unit="usd")
        session.add(row)
        session.flush()
    return row


def credit(session, account_id, amount, operation_id, actor, note):
    account = session.get(Account, account_id)
    if not account or account.status != "active" or account.role not in {"user", "admin"}:
        raise ValueError("Выберите активный зарегистрированный аккаунт")
    if (
        not isinstance(amount, Decimal)
        or not amount.is_finite()
        or not Decimal("0") < amount <= Decimal("9999999999")
        or amount != amount.quantize(QUANTUM)
    ):
        raise ValueError("Сумма: положительное число USD, максимум 8 знаков после запятой")
    if not 1 <= len(operation_id) <= 100 or not 1 <= len(note.strip()) <= 200:
        raise ValueError("Нужны идентификатор операции и комментарий до 200 символов")
    key = "credit:" + sha256((account_id + ":" + operation_id).encode()).hexdigest()
    prior = session.get(WalletEntry, key)
    if prior:
        if prior.account_id != account_id or prior.amount_usd != amount:
            raise ValueError("Этот идентификатор уже использован для другой суммы")
        return prior
    row = wallet(session, account_id)
    if row.balance_usd + amount > Decimal("9999999999"):
        raise ValueError("Превышен предельный баланс")
    row.balance_usd += amount
    entry = WalletEntry(
        id=key,
        account_id=account_id,
        amount_usd=amount,
        kind="credit",
        actor=actor,
        note=note.strip(),
    )
    session.add(entry)
    session.flush()
    return entry


def snapshot(session, account_id):
    row = session.get(Wallet, account_id)
    balance = row.balance_usd if row else Decimal("0")
    held = session.scalar(
        select(func.coalesce(func.sum(WalletHold.amount_usd), 0)).where(
            WalletHold.account_id == account_id, WalletHold.state.in_(["pending", "uncertain"])
        )
    ) or Decimal("0")
    available = max(balance - held, Decimal("0"))
    return {
        "currency": "USD",
        "display_unit": row.display_unit if row else "usd",
        "unit_rate": 100,
        "balance_usd": format(balance, "f"),
        "reserved_usd": format(held, "f"),
        "available_usd": format(available, "f"),
        "balance_credits": format(balance * 100, "f"),
        "available_credits": format(available * 100, "f"),
        "payment_enabled": False,
    }


def reserve(session, event, input_chars, output_tokens):
    rule_id = event.provider + ":" + event.model
    cost = Decimal("0")
    if rule_id != "mock:mock-chat":
        rule = session.get(WalletModelBound, rule_id)
        if (
            not rule
            or not rule.reviewed
            or rule.expires <= int(time.time())
            or rule.max_cost_usd <= 0
            or input_chars > rule.max_input_chars
            or output_tokens > rule.max_output_tokens
        ):
            raise PriceUnavailable()
        cost = rule.max_cost_usd
    amount = (cost * (1 + event.pricing_markup_percent / 100)).quantize(
        QUANTUM, rounding=ROUND_CEILING
    )
    data = snapshot(session, event.account_id)
    if amount > Decimal(data["available_usd"]):
        raise WalletUnavailable()
    session.add(
        WalletHold(
            event_id=event.id, account_id=event.account_id, amount_usd=amount, rule_id=rule_id
        )
    )
    session.flush()


def settle(session, event, result=None, definite=False):
    hold = session.get(WalletHold, event.id)
    if not hold or hold.state in {"settled", "released", "review"}:
        return
    if definite:
        hold.state = "released"
        return
    if (
        result is None
        or not result.cost_known
        or not result.cost_usd.is_finite()
        or result.cost_usd < 0
        or result.cost_diem != 0
    ):
        hold.state = "uncertain"
        event.cost_known = False  # Нет подтверждённой полной себестоимости в USD.
        return
    amount = (result.cost_usd * (1 + event.pricing_markup_percent / 100)).quantize(QUANTUM)
    # Клиент не оплачивает сверх зарезервированной верхней цены. Ошибка оценки — риск сервиса.
    if amount > hold.amount_usd:
        rule = session.get(WalletModelBound, hold.rule_id)
        if rule:
            rule.reviewed = False
        amount = hold.amount_usd
        hold.state = "review"
    else:
        hold.state = "settled"
    row = wallet(session, event.account_id)
    row.balance_usd -= amount
    event.billed_usd = amount
    session.add(
        WalletEntry(
            id="debit:" + event.id,
            account_id=event.account_id,
            amount_usd=-amount,
            kind="usage",
            note=("Генерация " + event.model)[:200],
            actor="gateway",
        )
    )
    session.flush()


def history(session, account_id, limit=20, offset=0):
    rows = session.scalars(
        select(WalletEntry)
        .where(WalletEntry.account_id == account_id)
        .order_by(WalletEntry.created_at.desc(), WalletEntry.id)
        .offset(offset)
        .limit(limit)
    )
    return {
        "wallet": snapshot(session, account_id),
        "entries": [
            {
                "id": row.id,
                "kind": row.kind,
                "amount_usd": format(row.amount_usd, "f"),
                "amount_credits": format(row.amount_usd * 100, "f"),
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
        "limit": limit,
        "offset": offset,
    }
