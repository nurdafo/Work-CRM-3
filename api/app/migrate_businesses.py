"""Explicit, idempotent migration of the existing ADS.KZ administrator."""
from sqlalchemy import select
from .config import get_settings
from .db import SessionLocal
from .integrations import CONFIG_DEFAULTS, encrypt_secrets
from .models import AuditLog, BusinessIntegration, Organization, Project, User


def migrate(db, settings):
    user = db.scalar(select(User).where(User.username == settings.admin_username.strip().lower()))
    if not user:
        raise RuntimeError("ADMIN_USERNAME does not identify an existing administrator")
    org = db.get(Organization, user.organization_id)
    existing = db.get(BusinessIntegration, org.id)
    if existing and existing.owner_id == user.id and user.role == "PLATFORM_ADMIN":
        return
    if org.name != "ADS.KZ" or user.role not in ("ADMIN", "PLATFORM_ADMIN"):
        raise RuntimeError("Migration is restricted to the ADS.KZ administrator")
    if not existing:
        project = db.scalar(select(Project).where(Project.organization_id == org.id,
            Project.deleted_at.is_(None)).order_by(Project.created_at.desc()))
        if not project:
            raise RuntimeError("ADS.KZ has no active project")
        config = {key: getattr(settings, key, default) for key, default in CONFIG_DEFAULTS.items()}
        config["meta_dataset_id"] = settings.meta_dataset_id or settings.meta_pixel_id
        secrets = {key: getattr(settings, key) for key in ("meta_leads_access_token", "meta_capi_access_token")
                   if getattr(settings, key)}
        db.add(BusinessIntegration(organization_id=org.id, project_id=project.id, owner_id=user.id,
            meta_page_id=settings.meta_page_id or None, config=config, secrets=encrypt_secrets(secrets)))
    if user.role != "PLATFORM_ADMIN":
        user.role = "PLATFORM_ADMIN"
        user.token_version += 1
        db.add(AuditLog(organization_id=org.id, actor_id=user.id, action="platform.migrated", entity_id=org.id))
    db.commit()


def main():
    with SessionLocal() as db:
        migrate(db, get_settings())
    print("ADS.KZ business is ready; existing credentials and data preserved")


if __name__ == "__main__":
    main()
