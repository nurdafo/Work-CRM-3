from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Header, status
from types import SimpleNamespace
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User, Organization

password_hash = PasswordHash.recommended()
bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


def create_token(user: User) -> str:
    secret = get_settings().jwt_secret
    if len(secret) < 32:
        raise RuntimeError("JWT_SECRET must be at least 32 characters")
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": user.id, "org": user.organization_id, "ver": user.token_version, "iat": now, "exp": now + timedelta(hours=8)},
        secret,
        algorithm="HS256",
    )


def authenticated_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    unauthorized = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется вход")
    if not credentials:
        raise unauthorized
    try:
        data = jwt.decode(credentials.credentials, get_settings().jwt_secret, algorithms=["HS256"])
        user = db.scalar(select(User).where(User.id == data.get("sub"), User.organization_id == data.get("org")))
    except jwt.InvalidTokenError:
        raise unauthorized from None
    if not user or not user.active or data.get("ver", 0) != user.token_version:
        raise unauthorized
    organization = db.get(Organization, user.organization_id)
    if not organization or not organization.active:
        raise unauthorized
    return user


def current_user(user: User = Depends(authenticated_user),
                 organization_id: str | None = Header(default=None, alias="X-Organization-ID"),
                 db: Session = Depends(get_db)):
    target_id = organization_id or user.organization_id
    if target_id != user.organization_id and user.role != "PLATFORM_ADMIN":
        raise HTTPException(status_code=403, detail="Нет доступа к этому бизнесу")
    org = db.get(Organization, target_id)
    if not org or not org.active:
        raise HTTPException(status_code=403, detail="Кабинет отключён или не найден")
    # A request-local principal: never mutate the persisted user's organization.
    return SimpleNamespace(id=user.id, organization_id=target_id, home_organization_id=user.organization_id,
        organization_name=org.name, username=user.username, full_name=user.full_name,
        role=user.role, active=user.active, password_hash=user.password_hash)


def platform_user(user: User = Depends(authenticated_user)) -> User:
    if user.role != "PLATFORM_ADMIN":
        raise HTTPException(status_code=403, detail="Доступ только для администратора платформы")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if user.role not in ("ADMIN", "PLATFORM_ADMIN"):
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return user
