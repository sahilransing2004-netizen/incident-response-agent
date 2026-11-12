from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    webhook_token: str
    database_url: str
    ollama_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "llama3.2:3b"
    prometheus_url: str = "http://prometheus:9090"
    loki_url: str = "http://loki:3100"
    llm_timeout_seconds: int = 120


@lru_cache
def get_settings() -> Settings:
    return Settings()