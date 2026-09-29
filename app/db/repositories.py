from hashlib import sha256

from sqlalchemy.orm import Session

from app.db.models import Account, ApiToken

BOOTSTRAP_ACCOUNT_ID = "bootstrap"


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


def create_api_token(
    session: Session,
    *,
    account_id: str,
    name: str,
    raw_token: str,
) -> ApiToken:
    """Store only the irreversible hash and a short display prefix."""

    if not raw_token:
        raise ValueError("API token cannot be empty")

    token = ApiToken(
        account_id=account_id,
        name=name,
        token_hash=sha256(raw_token.encode("utf-8")).hexdigest(),
        token_prefix=raw_token[:8],
    )
    session.add(token)
    session.flush()
    return token
