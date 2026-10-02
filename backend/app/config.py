from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str = 'sqlite:///./signal.db'
    redis_url: str = 'redis://localhost:6379/0'
    app_origin: str = 'http://localhost:8080'
    secure_cookies: bool = False
    environment: str = 'development'
    admin_email: str = 'admin@signal.local'
    admin_password: str = ''
    seed_demo: bool = False
    openai_api_key: str = ''
    openai_model: str = 'gpt-4.1-mini'
    seed_catalog: bool = True
    ft_client_id: str = ''
    ft_client_secret: str = ''
    ft_departements: str = ''
    ft_backfill_hours: int = 48
    adzuna_app_id: str = ''
    adzuna_app_key: str = ''
    enable_arbeitnow: bool = True
    aggregator_ttl_days: int = 30
    discovery_batch: int = 40
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

settings = Settings()
