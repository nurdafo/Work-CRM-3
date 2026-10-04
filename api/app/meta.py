"""Meta Lead Ads ingestion and CRM quality feedback."""
import hashlib
import hmac
import re
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import get_settings
from .lead_scoring import score_lead
from .integrations import project_settings
from .telegram import enqueue
from .models import Lead, LeadStatusHistory, MetaFeedback, Project


def configured_ad_labels(settings) -> dict[str, str]:
    labels: dict[str, str] = {}
    for item in settings.meta_ad_labels.split(";"):
        ad_id, separator, label = item.partition("=")
        ad_id, label = ad_id.strip(), label.strip()
        if separator and re.fullmatch(r"\d+", ad_id) and label:
            labels[ad_id] = label[:180]
    return labels


def verify_signature(body: bytes, signature: str) -> bool:
    secret = get_settings().meta_app_secret
    if not secret or not signature.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature[7:], digest)


def meta_value(fields: list[dict], *names: str) -> str:
    for field in fields:
        if str(field.get("name", "")).lower() in names:
            values = field.get("values") or []
            return ", ".join(str(value) for value in values)
    return ""


def normalized_service(fields: list[dict]) -> str:
    answer = " ".join(str(field.get("values", "")) for field in fields).lower()
    found = [key for key, aliases in {
        "TARGET": ("таргет", "реклам", "ads"),
        "CRM": ("crm", "срм"),
        "API": ("api", "апи"),
    }.items() if any(alias in answer for alias in aliases)]
    return found[0] if len(found) == 1 else "COMPLEX"


def import_meta_lead(db: Session, project: Project, data: dict, page_id: str, form_id: str) -> Lead:
    lead_id = str(data["id"])
    existing = db.scalar(select(Lead).where(Lead.meta_lead_id == lead_id))
    if existing:
        if existing.project_id != project.id:
            raise HTTPException(409, "Заявка уже принадлежит другому проекту")
        return existing
    fields = data.get("field_data") or []
    budget_text = meta_value(fields, "monthly_ad_budget", "budget", "бюджет")
    digits = re.sub(r"\D", "", budget_text)
    budget = int(digits) if digits else None
    service = normalized_service(fields)
    score, priority, low_budget = score_lead(project, service, budget)
    ad_id = str(data.get("ad_id") or "").strip() or None
    ad_name = configured_ad_labels(project_settings(db, project.id)).get(ad_id or "") or str(data.get("ad_name") or "").strip() or None
    lead = Lead(project_id=project.id, source="META", meta_lead_id=lead_id,
                meta_page_id=page_id, meta_form_id=form_id,
                meta_ad_id=ad_id, meta_ad_name=ad_name,
                form_answers=[{"question": str(f.get("name", "")), "answers": [str(v) for v in (f.get("values") or [])]} for f in fields],
                name=meta_value(fields, "full_name", "name", "имя") or "Имя не указано",
                phone=meta_value(fields, "phone_number", "phone", "телефон") or "Не указан",
                email=meta_value(fields, "email", "электронная почта"),
                company_name=meta_value(fields, "company_name", "company", "компания"),
                city=meta_value(fields, "city", "город"), requested_service=service,
                monthly_ad_budget=budget, score=score, priority=priority, low_budget=low_budget)
    try:
        with db.begin_nested():
            db.add(lead)
            db.flush()
            db.add(LeadStatusHistory(lead_id=lead.id, new_status="NEW"))
            enqueue(db, project, lead)
            db.flush()
    except IntegrityError:
        existing = db.scalar(select(Lead).where(Lead.meta_lead_id == lead_id))
        if not existing or existing.project_id != project.id:
            raise HTTPException(409, "Конфликт при импорте заявки") from None
        return existing
    db.commit()
    db.refresh(lead)
    return lead


async def graph_items(client: httpx.AsyncClient, path: str, fields: str) -> AsyncIterator[dict]:
    """Read every Graph API page using cursors, without accepting external URLs."""
    cursor: str | None = None
    seen: set[str] = set()
    for _ in range(100):
        params = {"fields": fields, "limit": 100}
        if cursor:
            params["after"] = cursor
        response = await client.get(path, params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload.get("data"), list):
            raise ValueError("Meta вернула неверный формат данных")
        for item in payload["data"]:
            if isinstance(item, dict):
                yield item
        next_cursor = (payload.get("paging") or {}).get("cursors", {}).get("after")
        if not next_cursor or next_cursor in seen:
            return
        seen.add(next_cursor)
        cursor = str(next_cursor)
    raise ValueError("Meta вернула слишком много страниц данных")


async def sync_page_leads(db: Session, project: Project, client: httpx.AsyncClient,
                          page_id: str, selected_form_id: str = "") -> dict[str, int]:
    """Import leads from the selected form, or every form of the configured Page."""
    if not re.fullmatch(r"\d+", page_id):
        raise ValueError("Неверный ID страницы Meta")
    if selected_form_id and not re.fullmatch(r"\d+", selected_form_id):
        raise ValueError("Неверный ID формы Meta")
    forms = imported = existing = 0
    async for form in graph_items(client, f"/{page_id}/leadgen_forms", "id,name"):
        form_id = str(form.get("id", ""))
        if not re.fullmatch(r"\d+", form_id) or (selected_form_id and form_id != selected_form_id):
            continue
        forms += 1
        async for data in graph_items(client, f"/{form_id}/leads", "id,created_time,field_data,form_id,ad_id,ad_name"):
            lead_id = str(data.get("id", ""))
            if not re.fullmatch(r"\d+", lead_id):
                continue
            if db.scalar(select(Lead.id).where(Lead.meta_lead_id == lead_id)):
                existing += 1
                continue
            import_meta_lead(db, project, data, page_id, form_id)
            imported += 1
    if selected_form_id and not forms:
        raise ValueError("Указанная форма Meta не найдена на подключённой странице")
    return {"forms": forms, "imported": imported, "existing": existing}


def send_qualified_feedback(db: Session, feedback: MetaFeedback, lead: Lead) -> MetaFeedback:
    settings = project_settings(db, lead.project_id)
    dataset_id = settings.meta_dataset_id or settings.meta_pixel_id
    if (not lead.meta_lead_id or lead.source != "META"
            or lead.meta_page_id != settings.meta_page_id
            or (settings.meta_form_id and lead.meta_form_id != settings.meta_form_id)):
        feedback.status = "NOT_ELIGIBLE"
        feedback.error = "Заявка не получена из подключённой формы Meta"
    elif not settings.meta_capi_access_token or not dataset_id:
        feedback.status = "WAITING_SETUP"
        feedback.error = "Добавьте набор данных и токен Conversions API в подключениях бизнеса"
    else:
        payload = {"data": [{"event_name": feedback.event_name,
                             "event_time": int((lead.qualified_at or datetime.now(timezone.utc)).timestamp()),
                             "event_id": f"qualified:{lead.meta_lead_id}",
                             "action_source": "system_generated",
                             "user_data": {"lead_id": lead.meta_lead_id},
                             "custom_data": {"event_source": "crm", "lead_event_source": "ADS.KZ CRM"}}]}
        feedback.attempts += 1
        try:
            response = httpx.post(
                f"https://graph.facebook.com/{settings.meta_api_version}/{dataset_id}/events",
                headers={"Authorization": f"Bearer {settings.meta_capi_access_token}"},
                json=payload, timeout=15)
            result = response.json()
            if response.is_success and result.get("events_received", 0) >= 1:
                feedback.status = "SENT"
                feedback.error = ""
                feedback.sent_at = datetime.now(timezone.utc)
            else:
                feedback.status = "FAILED"
                feedback.error = "Meta не подтвердила приём события. Проверьте подключение бизнеса."
        except (httpx.HTTPError, ValueError) as exc:
            feedback.status = "FAILED"
            feedback.error = "Не удалось связаться с Meta"
    db.commit()
    db.refresh(feedback)
    return feedback
