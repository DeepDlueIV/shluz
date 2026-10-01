from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    telegram_bot_token: str = ''
    telegram_allowed_ids: str = ''
    gateway_url: str = 'http://shluz:8000'
    database_path: str = './data/telegram.db'

    def allowed_ids(self) -> set[int]:
        return {int(value) for value in self.telegram_allowed_ids.split(',') if value.strip()}
