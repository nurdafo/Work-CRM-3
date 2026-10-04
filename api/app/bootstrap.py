from sqlalchemy import select

from .config import get_settings
from .db import SessionLocal
from .models import Organization, Project, User
from .security import hash_password


def main() -> None:
    settings = get_settings()
    if not settings.admin_username or not settings.admin_password:
        raise SystemExit("Set ADMIN_USERNAME and ADMIN_PASSWORD in .env")
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.username == settings.admin_username.lower())):
            raise SystemExit("Admin already exists; bootstrap did not change the password")
        organization = Organization(name="ADS.KZ")
        db.add(organization)
        db.flush()
        db.add(User(organization_id=organization.id, username=settings.admin_username.lower(),
                    full_name="Администратор ADS.KZ", password_hash=hash_password(settings.admin_password), role="ADMIN"))
        db.add(Project(organization_id=organization.id, name="Услуги ADS.KZ",
                       description="Таргетированная реклама, CRM и API", minimum_ad_budget=150000))
        db.commit()
    print("ADS.KZ organization, administrator and first project created")


if __name__ == "__main__":
    main()
