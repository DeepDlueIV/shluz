import re
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs

from fastapi import HTTPException, Request

_PLAN_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_MAX_FORM_BYTES = 16_384
FIELD_LABELS = {
    "code": "Код тарифа", "name": "Название", "display_name": "Имя пользователя",
    "monthly_price_usd": "Цена в месяц", "monthly_cost_limit_usd": "Лимит себестоимости",
    "monthly_credit_limit": "Кредитов в месяц", "monthly_request_limit": "Запросов в месяц",
    "request_credit_reserve": "Резерв кредитов", "markup_percent": "Наценка",
    "role": "Роль", "status": "Статус", "source": "Канал",
}


async def _read_form(request: Request) -> dict[str, str]:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _MAX_FORM_BYTES:
            raise HTTPException(status_code=413, detail="Форма слишком большая")
    try:
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=64)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Некорректная форма") from exc
    form = {key: values[-1] for key, values in parsed.items()}
    # Только безопасные поля для повторного показа формы; не пароли и не ключи.
    request.state.admin_form = {
        key: value for key, value in form.items() if key in FIELD_LABELS or key == "active"
    }
    return form


def _required_text(form: dict[str, str], key: str, *, max_length: int) -> str:
    value = form.get(key, "").strip()
    if not value or len(value) > max_length:
        raise HTTPException(
            status_code=422,
            detail=f"{FIELD_LABELS.get(key, key)}: заполните поле (до {max_length} символов)",
        )
    return value


def _choice(form: dict[str, str], key: str, allowed: set[str]) -> str:
    value = form.get(key, "").strip().lower()
    if value not in allowed:
        raise HTTPException(
            status_code=422, detail=f"{FIELD_LABELS.get(key, key)}: недопустимое значение",
        )
    return value


def _optional_int(form: dict[str, str], key: str) -> int | None:
    raw = form.get(key, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"{FIELD_LABELS.get(key, key)}: нужно целое число",
        ) from exc
    if not 0 <= value <= 2_147_483_647:
        raise HTTPException(
            status_code=422,
            detail=f"{FIELD_LABELS.get(key, key)}: допустимо от 0 до 2147483647",
        )
    return value


def _decimal_value(
    form: dict[str, str], key: str, *, optional: bool = False,
) -> Decimal | None:
    raw = form.get(key, "").strip().replace(",", ".")
    if not raw and optional:
        return None
    try:
        value = Decimal(raw or "0")
    except InvalidOperation as exc:
        raise HTTPException(
            status_code=422, detail=f"{FIELD_LABELS.get(key, key)}: нужно число",
        ) from exc
    upper = Decimal("99999.99") if key == "markup_percent" else Decimal("9999999999")
    if not value.is_finite() or not 0 <= value <= upper:
        raise HTTPException(
            status_code=422,
            detail=f"{FIELD_LABELS.get(key, key)}: допустимо от 0 до {upper}",
        )
    return value


def _plan_values(form: dict[str, str]) -> dict[str, object]:
    code = _required_text(form, "code", max_length=64).lower()
    if not _PLAN_CODE_PATTERN.fullmatch(code):
        raise HTTPException(
            status_code=422,
            detail="Код тарифа: латинские буквы, цифры, дефис и подчёркивание. Например: test",
        )
    return {
        "code": code,
        "name": _required_text(form, "name", max_length=200),
        "monthly_price_usd": _decimal_value(form, "monthly_price_usd"),
        "monthly_cost_limit_usd": _decimal_value(form, "monthly_cost_limit_usd", optional=True),
        "monthly_credit_limit": _optional_int(form, "monthly_credit_limit"),
        "monthly_request_limit": _optional_int(form, "monthly_request_limit"),
        "request_credit_reserve": _optional_int(form, "request_credit_reserve") or 0,
        "markup_percent": _decimal_value(form, "markup_percent"),
        "active": form.get("active", "").lower() in {"on", "true", "1", "yes"},
    }
