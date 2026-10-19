"""OTP: код связан с браузерной сессией; лимиты и погашение находятся в SQL."""
import hashlib
import re
import secrets
import time

from sqlalchemy import select

from app.db.models import Account, DemoGrant, Identity
from app.product.service import grant_demo, locked_session, policy
from app.webaccess.models import OtpChallenge
from app.webaccess.security import (
    client_network, consume, control, current_session, digest, new_session, reject,
    verify_captcha,
)


def normalize_email(value):
    value = value.strip()
    if len(value) > 320 or value.count('@') != 1:
        reject('invalid_email', 422, 'Проверьте адрес почты.')
    local, host = value.rsplit('@', 1)
    try:
        host = host.encode('idna').decode('ascii').lower()
    except UnicodeError:
        reject('invalid_email', 422, 'Проверьте адрес почты.')
    if (not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}", local)
            or local.startswith('.') or local.endswith('.') or '..' in local
            or '.' not in host or len(host) > 253
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                   for label in host.split('.'))):
        reject('invalid_email', 422, 'Проверьте адрес почты.')
    # Не объединяем Gmail dots/+aliases, разные адреса остаются разными identities.
    return local.lower() + '@' + host


async def request_code(request, payload):
    settings = request.app.state.web_settings
    db = request.app.state.database
    email = normalize_email(payload.email)
    with db.session() as s:
        current_session(request, s, csrf=True)
    if not request.app.state.web_mailer.configured:
        reject('mail_not_configured', 503, 'Отправка писем пока не подключена.')
    await verify_captcha(request, payload.turnstile_token, 'email_otp')
    _, network = client_network(request, settings)
    key = settings.secret.get_secret_value()
    email_key = digest(key, 'email-rate', email)
    raw, code = secrets.token_urlsafe(32), f'{secrets.randbelow(1000000):06d}'
    challenge_id = digest(key, 'otp-flow', raw)
    now = int(time.time())
    with locked_session(db) as s:
        browser = current_session(request, s, csrf=True)
        cfg = control(s)
        consume(s, 'otp:cooldown:' + email_key, 1, 60, now)
        consume(s, 'otp:email:' + email_key, cfg.otp_email_daily, 86400, now)
        consume(s, 'otp:network:' + network, cfg.otp_network_daily, 86400, now)
        consume(s, 'otp:global', cfg.otp_global_daily, 86400, now)
        previous = s.scalars(select(OtpChallenge).where(
            OtpChallenge.session_id == browser.id, OtpChallenge.used.is_(False)))
        for old in previous:
            old.used, old.email, old.code_hash = True, '', ''
        s.add(OtpChallenge(id=challenge_id, session_id=browser.id, email=email,
                          code_hash=digest(key, 'otp-code', challenge_id + ':' + code),
                          attempts=0, used=False, sent=False, expires=now + 600))
    sent = await request.app.state.web_mailer.send_code(email, code)
    with locked_session(db) as s:
        row = s.get(OtpChallenge, challenge_id)
        if row is not None:
            row.sent = sent
            if not sent:
                row.used, row.email, row.code_hash = True, '', ''
    if not sent:
        reject('mail_unavailable', 503, 'Письмо не удалось отправить. Попробуйте позже.')
    return {'challenge_id': raw, 'expires_in': 600, 'resend_after': 60,
            'message': 'Код отправлен на указанный адрес.'}


def verify_code(request, payload):
    db, settings = request.app.state.database, request.app.state.web_settings
    key = settings.secret.get_secret_value()
    _, network = client_network(request, settings)
    now = int(time.time())
    # Rate limit фиксируется и при неверном коде, не откатывается вместе с отказом.
    with locked_session(db) as s:
        cfg = control(s)
        consume(s, 'otp:verify:' + network, cfg.otp_verify_minute, 60, now)
        consume(s, 'otp:verify-global', 500, 60, now)
    valid = False
    with locked_session(db) as s:
        browser = current_session(request, s, csrf=True)
        row = s.get(OtpChallenge, digest(key, 'otp-flow', payload.challenge_id))
        if (row and row.session_id == browser.id and not row.used and row.sent
                and row.expires > now and row.attempts < 5):
            row.attempts += 1
            valid = secrets.compare_digest(row.code_hash,
                    digest(key, 'otp-code', row.id + ':' + payload.code))
            if valid:
                email = row.email
                subject = hashlib.sha256(email.encode()).hexdigest()
                identity = s.scalar(select(Identity).where(Identity.provider == 'email',
                                                           Identity.subject == subject))
                guest = s.get(Account, browser.account_id) if browser.account_id else None
                if identity:
                    account = s.get(Account, identity.account_id)
                    if account is None or account.status != 'active':
                        valid = False
                    elif guest and guest.role == 'guest' and guest.id != account.id:
                        old_grant = s.get(DemoGrant, guest.id)
                        if old_grant:
                            target = grant_demo(s, account.id)
                            target.requests_used += old_grant.requests_used
                        guest.status = 'inactive'
                else:
                    if guest and guest.role == 'guest' and guest.status == 'active':
                        account = guest
                        account.role = 'user'
                        account.display_name = email.split('@')[0][:80]
                    else:
                        account = Account(display_name=email.split('@')[0][:80])
                        s.add(account)
                        s.flush()
                    s.add(Identity(account_id=account.id, provider='email', subject=subject, email=email))
                    if policy(s).mode == 'demo':
                        grant_demo(s, account.id)
                if valid:
                    browser.revoked = True
                    raw, new = new_session(s, settings, account.id, True)
                row.used, row.email, row.code_hash = True, '', ''
            elif row.attempts >= 5:
                row.used, row.email, row.code_hash = True, '', ''
    if not valid:
        reject('invalid_or_expired_code', 401, 'Неверный или истёкший код.')
    return raw, new
