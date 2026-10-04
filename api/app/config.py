from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    database_url: str = "sqlite:///./crm.db"
    jwt_secret: str = ""
    integration_encryption_key: str = ""
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    admin_username: str = ""
    admin_password: str = ""
    meta_verify_token: str = ""
    meta_app_secret: str = ""
    meta_leads_access_token: str = ""
    meta_capi_access_token: str = ""
    meta_dataset_id: str = ""
    meta_lookalike_min_leads: int = 20
    meta_page_id: str = ""
    meta_form_id: str = ""
    meta_ad_labels: str = ""
    meta_pixel_id: str = ""
    meta_api_version: str = "v23.0"

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        extra="ignore",
    )

    @field_validator("database_url")
    @classmethod
    def use_psycopg_driver(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return "postgresql+psycopg://" + value.removeprefix("postgres://")
        if value.startswith("postgresql://"):
            return "postgresql+psycopg://" + value.removeprefix("postgresql://")
        return value

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
