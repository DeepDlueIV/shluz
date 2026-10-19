"""Внешние сервисы: фиксированный Siteverify и SMTP с проверкой TLS."""
import asyncio
import json
import smtplib
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage

import httpx


class Turnstile:
    def __init__(self, settings):
        self.settings = settings

    async def verify(self, token, ip, action):
        s = self.settings
        if not s.captcha_secret or not s.captcha_hostnames:
            return False
        secret = s.captcha_secret.get_secret_value()
        if secret in {'1x0000000000000000000000000000000AA',
                      '2x0000000000000000000000000000000AA',
                      '3x0000000000000000000000000000000AA'}:
            return False
        try:
            async with httpx.AsyncClient(timeout=8, trust_env=False, follow_redirects=False) as c:
                async with c.stream('POST', 'https://challenges.cloudflare.com/turnstile/v0/siteverify',
                                    data={'secret': secret, 'response': token, 'remoteip': ip}) as r:
                    if r.status_code != 200:
                        return False
                    data = bytearray()
                    async for chunk in r.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 16384:
                            return False
            body = json.loads(data)
            issued = datetime.fromisoformat(body.get('challenge_ts', '').replace('Z', '+00:00'))
            age = (datetime.now(UTC) - issued).total_seconds()
            return (body.get('success') is True and body.get('hostname') in s.captcha_hostnames
                    and body.get('action') == action and -30 <= age <= 300)
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return False


class SmtpMailer:
    def __init__(self, settings):
        self.settings = settings

    @property
    def configured(self):
        return bool(self.settings.smtp_host and self.settings.smtp_sender)

    async def send_code(self, email, code):
        if not self.configured:
            return False
        def send():
            s = self.settings
            mail = EmailMessage()
            mail['From'], mail['To'], mail['Subject'] = s.smtp_sender, email, 'Код входа Shluz'
            mail.set_content(f'Ваш код входа: {code}\nДействует 10 минут. Никому его не сообщайте.\n'
                             'Если вы не запрашивали код, проигнорируйте это письмо.')
            cls = smtplib.SMTP_SSL if s.smtp_ssl else smtplib.SMTP
            options = {'context': ssl.create_default_context()} if s.smtp_ssl else {}
            with cls(s.smtp_host, s.smtp_port, timeout=10, **options) as smtp:
                if not s.smtp_ssl:
                    smtp.starttls(context=ssl.create_default_context())
                if s.smtp_username:
                    smtp.login(s.smtp_username, s.smtp_password.get_secret_value() if s.smtp_password else '')
                smtp.send_message(mail)
        try:
            # Сетевые timeout SMTP ограничены; неоднозначная отправка не повторяется.
            await asyncio.to_thread(send)
            return True
        except (smtplib.SMTPException, OSError):
            return False
