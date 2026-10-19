"""Секреты только окружения; публичный доступ по умолчанию закрыт."""
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WebSettings(BaseSettings):
    enabled: bool = False
    secret: SecretStr | None = None
    origins: list[str] = Field(default_factory=list)
    trusted_proxies: list[str] = Field(default_factory=list)
    secure_cookie: bool = True
    captcha_secret: SecretStr | None = None
    captcha_sitekey: str = ''
    captcha_hostnames: list[str] = Field(default_factory=list)
    smtp_host: str = ''
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ''
    smtp_password: SecretStr | None = None
    smtp_sender: str = ''
    smtp_ssl: bool = False
    model_config = SettingsConfigDict(env_prefix='SHLUZ_WEB_', env_file='.env', extra='ignore')

    @model_validator(mode='after')
    def validate_public_settings(self):
        import ipaddress
        for proxy in self.trusted_proxies:
            network = ipaddress.ip_network(proxy, strict=False)
            if network.prefixlen == 0:
                raise ValueError('Нельзя доверять forwarded headers всему интернету')
        if not self.enabled:
            return self
        if not self.secret or len(self.secret.get_secret_value()) < 32 or not self.origins:
            raise ValueError('Для web нужны секрет минимум 32 символа и точные Origin')
        for origin in self.origins:
            url = urlsplit(origin)
            if (url.scheme not in {'http', 'https'} or not url.hostname or url.username
                    or url.password or url.path or url.query or url.fragment):
                raise ValueError('Origin должен быть точным адресом без пути и wildcard')
            if '*' in origin or (url.scheme != 'https' and url.hostname not in {'localhost', '127.0.0.1', '::1'}):
                raise ValueError('Публичный web требует HTTPS')
            if not self.secure_cookie and url.hostname not in {'localhost', '127.0.0.1', '::1'}:
                raise ValueError('Незащищённая cookie допустима только на loopback')
        for value in (self.smtp_host, self.smtp_sender, self.smtp_username):
            if '\r' in value or '\n' in value:
                raise ValueError('Недопустимые символы SMTP')
        return self

    @property
    def cookie_name(self):
        return '__Host-shluz_web' if self.secure_cookie else 'shluz_web_local'
