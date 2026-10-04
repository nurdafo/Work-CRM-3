"""Business credentials have no environment fallback at request time."""
import json
from types import SimpleNamespace

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy.orm import Session

from .config import get_settings
from .models import BusinessIntegration, Project

SECRET_FIELDS = {"meta_leads_access_token", "meta_capi_access_token", "meta_ads_access_token", "telegram_bot_token"}
CONFIG_DEFAULTS = {
    "meta_form_id": "", "meta_dataset_id": "", "meta_ad_account_id": "",
    "meta_ad_labels": "", "meta_lookalike_min_leads": 20, "telegram_chat_id": "",
}


def cipher():
    try:
        return Fernet(get_settings().integration_encryption_key.encode())
    except (ValueError, TypeError):
        raise HTTPException(503, "Настройте INTEGRATION_ENCRYPTION_KEY на сервере") from None


def decrypt_secrets(value: str) -> dict:
    if not value:
        return {}
    try:
        return json.loads(cipher().decrypt(value.encode()))
    except (InvalidToken, ValueError):
        raise HTTPException(503, "Не удалось расшифровать подключения бизнеса") from None


def encrypt_secrets(values: dict) -> str:
    return cipher().encrypt(json.dumps(values).encode()).decode() if values else ""


def business_settings(db: Session, organization_id: str):
    row = db.get(BusinessIntegration, organization_id)
    values = {**CONFIG_DEFAULTS, **{key: "" for key in SECRET_FIELDS}, "meta_page_id": "",
              "meta_pixel_id": "", "meta_api_version": get_settings().meta_api_version}
    if row:
        values.update(row.config)
        values.update(decrypt_secrets(row.secrets))
        values["meta_page_id"] = row.meta_page_id or ""
    return SimpleNamespace(**values)


def project_settings(db: Session, project_id: str):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Проект не найден")
    return business_settings(db, project.organization_id)
