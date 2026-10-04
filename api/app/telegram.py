"""Durable outbox. Run `python -m app.telegram` for scheduled retries."""
from datetime import datetime, timedelta, timezone
import time
import httpx
from sqlalchemy import select, update, and_
from sqlalchemy.orm import Session

from .integrations import business_settings
from .models import BusinessIntegration, Lead, Organization, Project, TelegramNotification


def enqueue(db: Session, project: Project, lead: Lead):
    row = db.get(BusinessIntegration, project.organization_id)
    # No historical notification flood when a bot is connected later.
    if not row or not row.config.get("telegram_chat_id") or not row.config.get("telegram_enabled"):
        return
    db.add(TelegramNotification(organization_id=project.organization_id, lead_id=lead.id))


def send_message(settings, text: str) -> bool:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False
    try:
        response = httpx.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": text[:4096]}, timeout=15)
        return response.is_success and response.json().get("ok") is True
    except (httpx.HTTPError, ValueError):
        # Never persist request URLs: Telegram embeds its token in the URL.
        return False


def process_queue(bind, organization_id: str | None = None, limit: int = 50):
    with Session(bind, expire_on_commit=False) as db:
        now = datetime.now(timezone.utc)
        due = and_(TelegramNotification.status.in_(("PENDING", "RETRY", "SENDING")),
                   TelegramNotification.next_attempt_at <= now)
        query = select(TelegramNotification.id).join(Organization).where(due, Organization.active.is_(True))
        if organization_id:
            query = query.where(TelegramNotification.organization_id == organization_id)
        ids = list(db.scalars(query.order_by(TelegramNotification.next_attempt_at).limit(limit)))
        for notification_id in ids:
            # Compare-and-swap lease prevents concurrent workers sending the same row.
            claimed = db.execute(update(TelegramNotification).where(
                TelegramNotification.id == notification_id, due).values(
                status="SENDING", next_attempt_at=now + timedelta(minutes=5),
                attempts=TelegramNotification.attempts + 1))
            db.commit()
            if claimed.rowcount != 1:
                continue
            item = db.get(TelegramNotification, notification_id)
            lead = db.get(Lead, item.lead_id)
            project = db.get(Project, lead.project_id) if lead else None
            org = db.get(Organization, item.organization_id)
            if not project or project.organization_id != item.organization_id or project.deleted_at or not org.active:
                item.status = "CANCELLED"
                db.commit()
                continue
            try:
                settings = business_settings(db, item.organization_id)
                success = send_message(settings, f"{org.name}\nНовая заявка: {lead.name}\nТелефон: {lead.phone}\nИсточник: {lead.source}")
            except Exception:
                success = False
            if success:
                item.status, item.error, item.sent_at = "SENT", "", datetime.now(timezone.utc)
            else:
                item.status = "RETRY"
                item.error = "Telegram недоступен или подключение не настроено"
                item.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=min(3600, 30 * 2 ** min(item.attempts, 7)))
            db.commit()


def main():
    from .db import engine
    while True:
        try:
            process_queue(engine)
        except Exception:
            print("Telegram outbox temporarily unavailable; retrying", flush=True)
        time.sleep(5)


if __name__ == "__main__":
    main()
