from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
import httpx
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import get_db
from .integrations import CONFIG_DEFAULTS, SECRET_FIELDS, business_settings, decrypt_secrets, encrypt_secrets
from .models import AuditLog, BusinessIntegration, Organization, Project, TelegramNotification, User
from .security import hash_password, platform_user

router = APIRouter(prefix="/admin/businesses", dependencies=[Depends(platform_user)])


class IntegrationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    meta_page_id: str = Field(default="", pattern=r"^\d*$", max_length=80)
    meta_form_id: str = Field(default="", pattern=r"^\d*$", max_length=80)
    meta_dataset_id: str = Field(default="", pattern=r"^\d*$", max_length=80)
    meta_ad_account_id: str = Field(default="", pattern=r"^(act_)?\d*$", max_length=80)
    meta_ad_labels: str = Field(default="", max_length=10000)
    meta_lookalike_min_leads: int = Field(default=20, ge=1, le=1000000)
    telegram_chat_id: str = Field(default="", pattern=r"^(-?\d+|@[a-zA-Z0-9_]+)?$", max_length=100)
    meta_leads_access_token: str = Field(default="", max_length=8192)
    meta_capi_access_token: str = Field(default="", max_length=8192)
    meta_ads_access_token: str = Field(default="", max_length=8192)
    telegram_bot_token: str = Field(default="", pattern=r"^(\d+:[a-zA-Z0-9_-]+)?$", max_length=256)


class BusinessInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=2, max_length=180)
    username: str = Field(min_length=2, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=256)
    integrations: IntegrationInput = Field(default_factory=IntegrationInput)


class BusinessPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(default=None, min_length=2, max_length=180)
    username: str | None = Field(default=None, min_length=2, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str | None = Field(default=None, min_length=8, max_length=256)
    active: bool | None = None
    integrations: IntegrationInput | None = None

    @field_validator("name", "username", "password", "active", "integrations", mode="before")
    @classmethod
    def no_null(cls, value):
        if value is None:
            raise ValueError("Поле не может быть null")
        return value


def get_business(db, business_id):
    org = db.get(Organization, business_id)
    row = db.get(BusinessIntegration, business_id)
    if not org or not row:
        raise HTTPException(404, "Бизнес не найден")
    return org, row


def present(db, org, row):
    owner = db.get(User, row.owner_id)
    stored = decrypt_secrets(row.secrets)
    return {"id": org.id, "name": org.name, "active": org.active,
            "username": owner.username, "project_id": row.project_id,
            "integrations": {**CONFIG_DEFAULTS, **row.config, "meta_page_id": row.meta_page_id or "",
                             **{f"{key}_configured": bool(stored.get(key)) for key in SECRET_FIELDS}},
            "telegram_pending": db.scalar(select(func.count()).select_from(TelegramNotification).where(
                TelegramNotification.organization_id == org.id, TelegramNotification.status != "SENT"))}


def apply_integrations(row, payload):
    changes = payload.model_dump(exclude_unset=True)
    secrets = decrypt_secrets(row.secrets)
    config = dict(row.config or {})
    for key, value in changes.items():
        if key in SECRET_FIELDS:
            secrets[key] = value
        elif key == "meta_page_id":
            row.meta_page_id = value or None
        else:
            config[key] = value
    row.config = {**config, "telegram_enabled": bool(secrets.get("telegram_bot_token"))}
    if SECRET_FIELDS.intersection(changes):
        row.secrets = encrypt_secrets(secrets)


def commit(db):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Логин или Facebook-страница уже используются") from None


@router.get("")
def list_businesses(db: Session = Depends(get_db)):
    return [present(db, org, row) for org, row in db.execute(select(Organization, BusinessIntegration).join(
        BusinessIntegration).order_by(Organization.created_at))]


@router.post("", status_code=201)
def create_business(payload: BusinessInput, actor: User = Depends(platform_user), db: Session = Depends(get_db)):
    org = Organization(name=payload.name)
    db.add(org)
    db.flush()
    owner = User(organization_id=org.id, username=payload.username.lower(), full_name=payload.name,
                 role="ADMIN", password_hash=hash_password(payload.password))
    project = Project(organization_id=org.id, name=payload.name)
    db.add_all([owner, project])
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Логин уже используется") from None
    row = BusinessIntegration(organization_id=org.id, project_id=project.id, owner_id=owner.id, config={}, secrets="")
    apply_integrations(row, payload.integrations)
    db.add_all([row, AuditLog(organization_id=org.id, actor_id=actor.id, action="business.created", entity_id=org.id)])
    commit(db)
    return present(db, org, row)


@router.patch("/{business_id}")
def update_business(business_id: str, payload: BusinessPatch, actor: User = Depends(platform_user), db: Session = Depends(get_db)):
    org, row = get_business(db, business_id)
    owner = db.get(User, row.owner_id)
    if payload.active is False and org.id == actor.organization_id:
        raise HTTPException(400, "Нельзя отключить собственный кабинет администратора платформы")
    if payload.name is not None:
        org.name = payload.name
        db.get(Project, row.project_id).name = payload.name
    if payload.username is not None:
        owner.username = payload.username.lower()
    if payload.password is not None:
        owner.password_hash = hash_password(payload.password)
        owner.token_version += 1
    if payload.active is not None and org.active != payload.active:
        org.active = payload.active
        for member in db.scalars(select(User).where(User.organization_id == org.id)):
            member.token_version += 1
    if payload.integrations is not None:
        apply_integrations(row, payload.integrations)
    db.add(AuditLog(organization_id=org.id, actor_id=actor.id, action="business.updated", entity_id=org.id))
    commit(db)
    return present(db, org, row)


@router.post("/{business_id}/check")
async def check_connections(business_id: str, db: Session = Depends(get_db)):
    get_business(db, business_id)
    settings = business_settings(db, business_id)
    results = {"meta_page": False, "meta_ads": False, "telegram": False}
    async with httpx.AsyncClient(timeout=15) as client:
        async def graph(path, token, expected, field="id"):
            if not token or not expected:
                return False
            try:
                response = await client.get(f"https://graph.facebook.com/{settings.meta_api_version}/{path}",
                    headers={"Authorization": f"Bearer {token}"}, params={"fields": field})
                return response.is_success and str(response.json().get(field)) == expected
            except (httpx.HTTPError, ValueError):
                return False
        results["meta_page"] = await graph("me", settings.meta_leads_access_token, settings.meta_page_id)
        account = settings.meta_ad_account_id.removeprefix("act_")
        results["meta_ads"] = await graph(f"act_{account}", settings.meta_ads_access_token, account, "account_id")
        if settings.telegram_bot_token:
            try:
                response = await client.get(f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe")
                results["telegram"] = response.is_success and response.json().get("ok") is True
            except (httpx.HTTPError, ValueError):
                pass
    return results


@router.post("/{business_id}/telegram-test")
def test_telegram(business_id: str, db: Session = Depends(get_db)):
    org, _ = get_business(db, business_id)
    if not org.active:
        raise HTTPException(400, "Кабинет отключён")
    from .telegram import send_message
    if not send_message(business_settings(db, business_id), f"{org.name}: уведомления CRM подключены."):
        raise HTTPException(502, "Проверьте токен бота, chat ID и доступ бота к чату")
    return {"sent": True}
