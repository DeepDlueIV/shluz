import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Account, ApiToken

BOOTSTRAP_ACCOUNT_ID = "bootstrap"


@dataclass(frozen=True, slots=True)
class AuthenticatedToken:
    account_id: str
    token_id: str
    source: str


def _token_hash(raw_token: str) -> str:
    return sha256(raw_token.encode("utf-8")).hexdigest()


def ensure_bootstrap_account(session: Session) -> Account:
    """Return the technical account used before personal authentication exists."""

    account = session.get(Account, BOOTSTRAP_ACCOUNT_ID)
    if account is None:
        account = Account(
            id=BOOTSTRAP_ACCOUNT_ID,
            display_name="Bootstrap access",
            role="service",
            status="active",
        )
        session.add(account)
        session.flush()
    return account


def generate_api_token() -> str:
    """Generate a high-entropy token that is safe to display only once."""

    return f"shluz_{secrets.token_urlsafe(32)}"


def create_api_token(
    session: Session,
    *,
    account_id: str,
    name: str,
    raw_token: str,
    source: str = "harness",
) -> ApiToken:
    """Store only the irreversible hash and a short display prefix."""

    if not raw_token:
        raise ValueError("API token cannot be empty")
    if not source.strip():
        raise ValueError("API token source cannot be empty")

    token = ApiToken(
        account_id=account_id,
        name=name,
        source=source.strip().lower(),
        token_hash=_token_hash(raw_token),
        token_prefix=raw_token[:8],
    )
    session.add(token)
    session.flush()
    return token


def authenticate_api_token(session: Session, raw_token: str) -> AuthenticatedToken | None:
    """Resolve one active personal token without exposing its stored hash."""

    if not raw_token:
        return None

    row = session.execute(
        select(ApiToken, Account)
        .join(Account, Account.id == ApiToken.account_id)
        .where(
            ApiToken.token_hash == _token_hash(raw_token),
            ApiToken.revoked_at.is_(None),
            Account.status == "active",
        )
    ).one_or_none()
    if row is None:
        return None

    token, account = row
    token.last_used_at = datetime.now(UTC)
    session.flush()
    return AuthenticatedToken(
        account_id=account.id,
        token_id=token.id,
        source=token.source,
    )


def revoke_api_token(
    session: Session,
    token_id: str,
    *,
    revoked_at: datetime | None = None,
) -> bool:
    """Revoke a personal token and report whether it existed."""

    token = session.get(ApiToken, token_id)
    if token is None:
        return False
    if token.revoked_at is None:
        token.revoked_at = revoked_at or datetime.now(UTC)
        session.flush()
    return True
