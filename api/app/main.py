from contextlib import asynccontextmanager
from datetime import datetime, timezone
from collections import Counter
import re

from fastapi import Depends, FastAPI, HTTPException, Request, BackgroundTasks
import httpx
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .lead_scoring import score_lead
from .integrations import business_settings, project_settings
from .businesses import router as business_router
from .telegram import enqueue, process_queue
from .models import AuditLog, BusinessIntegration, Organization, Lead, LeadComment, LeadStatusHistory, MetaFeedback, Project, ProjectUser, User
from .schemas import AnalyticsOut, AssignmentIn, BreakdownRow, LeadCommentIn, LeadCommentOut, LeadCreate, LeadNotesIn, LeadOut, LoginIn, MetaFeedbackOut, PasswordChange, ProjectCreate, ProjectOut, ProjectSettingsIn, StatusIn, TokenOut, UserCreate, UserOut, WhatsAppOut
from .meta import import_meta_lead, send_qualified_feedback, sync_page_leads, verify_signature
from .question_analytics import question_charts
from .security import admin_user, create_token, current_user, hash_password, verify_password


@asynccontextmanager
async def lifespan(app: FastAPI):
    if len(get_settings().jwt_secret) < 32:
        raise RuntimeError("Set JWT_SECRET to a random string of at least 32 characters")
    yield


app = FastAPI(title="ADS.KZ CRM API", version="0.1.0", lifespan=lifespan)
app.include_router(business_router)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    # Pydantic's default response echoes invalid input, including passwords/tokens.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": error["loc"], "msg": error["msg"], "type": error["type"]} for error in exc.errors()
    ]})


app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Organization-ID"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


async def meta_page_token_matches(settings) -> bool:
    """A Page token must identify the exact Page selected for imports."""
    async with httpx.AsyncClient(
        base_url=f"https://graph.facebook.com/{settings.meta_api_version}",
        headers={"Authorization": f"Bearer {settings.meta_leads_access_token}"},
        timeout=15,
    ) as client:
        response = await client.get("/me", params={"fields": "id"})
        response.raise_for_status()
        return str(response.json().get("id", "")) == settings.meta_page_id


@app.get("/integrations/meta/status")
async def meta_integration_status(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, bool]:
    settings = business_settings(db, user.organization_id)
    if not settings.meta_leads_access_token or not settings.meta_page_id:
        return {"connected": False}
    try:
        return {"connected": await meta_page_token_matches(settings)}
    except (httpx.HTTPError, ValueError):
        return {"connected": False}


@app.get("/integrations/meta/audience-readiness")
def meta_audience_readiness(project_id: str, user: User = Depends(current_user),
                            db: Session = Depends(get_db)) -> dict[str, int | bool]:
    permitted_project(db, user, project_id)
    eligible = active_leads_query(project_id, db).where(
        Lead.source == "META", Lead.meta_lead_id.is_not(None),
        Lead.qualified_at.is_not(None), Lead.status.not_in(("LOST", "UNQUALIFIED")),
    )
    qualified = len(db.scalars(eligible).all())
    threshold = max(1, business_settings(db, user.organization_id).meta_lookalike_min_leads)
    return {"qualified": qualified, "threshold": threshold, "ready": qualified >= threshold}


@app.post("/integrations/meta/sync")
async def sync_meta(project_id: str, background: BackgroundTasks, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict[str, int]:
    project = permitted_project(db, user, project_id)
    settings = business_settings(db, user.organization_id)
    integration = db.get(BusinessIntegration, user.organization_id)
    if integration and integration.project_id != project.id:
        raise HTTPException(400, "Meta подключена к основному проекту бизнеса")
    if not settings.meta_leads_access_token or not settings.meta_page_id:
        raise HTTPException(status_code=400, detail="Сначала добавьте ID страницы и токен лидов Meta")
    try:
        if not await meta_page_token_matches(settings):
            raise HTTPException(status_code=400, detail="Токен Meta принадлежит другой странице Facebook")
        async with httpx.AsyncClient(
            base_url=f"https://graph.facebook.com/{settings.meta_api_version}",
            headers={"Authorization": f"Bearer {settings.meta_leads_access_token}"},
            timeout=30,
        ) as client:
            result = await sync_page_leads(db, project, client, settings.meta_page_id, settings.meta_form_id)
            background.add_task(process_queue, db.get_bind(), user.organization_id)
            return result
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Не удалось обновить заявки Meta. Проверьте доступ к странице и форме.") from exc



@app.post("/auth/login", response_model=TokenOut)
def login(payload: LoginIn, db: Session = Depends(get_db)) -> TokenOut:
    username = (payload.username or get_settings().admin_username).strip().lower()
    user = db.scalar(select(User).where(User.username == username, User.active.is_(True)))
    if not user or not db.get(Organization, user.organization_id).active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    return TokenOut(access_token=create_token(user))


@app.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> User:
    return user


@app.post("/auth/password", status_code=204)
def change_password(payload: PasswordChange, user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    if not payload.new_password:
        raise HTTPException(status_code=422, detail="Введите новый пароль")
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Текущий пароль неверен")
    account = db.get(User, user.id)
    account.password_hash = hash_password(payload.new_password)
    account.token_version += 1
    db.add(AuditLog(organization_id=user.organization_id, actor_id=user.id,
                    action="user.password_changed", entity_id=user.id))
    db.commit()


@app.get("/projects", response_model=list[ProjectOut])
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[Project]:
    query = select(Project).where(Project.organization_id == user.organization_id, Project.deleted_at.is_(None))
    if user.role not in ("ADMIN", "PLATFORM_ADMIN"):
        query = query.join(ProjectUser, ProjectUser.project_id == Project.id).where(
            ProjectUser.user_id == user.id, ProjectUser.active.is_(True)
        )
    return list(db.scalars(query.order_by(Project.created_at.desc())).all())


@app.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectCreate, user: User = Depends(admin_user), db: Session = Depends(get_db)) -> Project:
    project = Project(organization_id=user.organization_id, **payload.model_dump())
    db.add(project)
    db.flush()
    db.add(AuditLog(organization_id=user.organization_id, actor_id=user.id, project_id=project.id,
                    action="project.created", entity_id=project.id))
    db.commit()
    db.refresh(project)
    return project


@app.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project_settings(project_id: str, payload: ProjectSettingsIn,
                            user: User = Depends(admin_user), db: Session = Depends(get_db)) -> Project:
    project = permitted_project(db, user, project_id)
    project.minimum_ad_budget = payload.minimum_ad_budget
    for lead in db.scalars(select(Lead).where(Lead.project_id == project.id)):
        lead.score, lead.priority, lead.low_budget = score_lead(project, lead.requested_service, lead.monthly_ad_budget)
    db.add(AuditLog(organization_id=user.organization_id, actor_id=user.id, project_id=project.id,
                    action="project.budget_changed", entity_id=project.id))
    db.commit()
    db.refresh(project)
    return project


@app.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, user: User = Depends(admin_user), db: Session = Depends(get_db)) -> None:
    project = db.scalar(select(Project).where(Project.id == project_id,
                                              Project.organization_id == user.organization_id,
                                              Project.deleted_at.is_(None)))
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")
    project.deleted_at = datetime.now(timezone.utc)
    db.add(AuditLog(organization_id=user.organization_id, actor_id=user.id, project_id=project.id,
                    action="project.deleted", entity_id=project.id))
    db.commit()


@app.get("/users", response_model=list[UserOut])
def list_users(user: User = Depends(admin_user), db: Session = Depends(get_db)) -> list[User]:
    return list(db.scalars(select(User).where(User.organization_id == user.organization_id).order_by(User.full_name)).all())


@app.post("/users", response_model=UserOut, status_code=201)
def create_user(payload: UserCreate, user: User = Depends(admin_user), db: Session = Depends(get_db)) -> User:
    if payload.role != "MANAGER":
        raise HTTPException(status_code=422, detail="Сейчас можно добавить только менеджера")
    if not payload.password:
        raise HTTPException(status_code=422, detail="Введите пароль")
    created = User(organization_id=user.organization_id, username=payload.username.strip().lower(),
                   full_name=payload.full_name.strip(), password_hash=hash_password(payload.password), role="MANAGER")
    db.add(created)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Этот логин уже занят") from None
    db.add(AuditLog(organization_id=user.organization_id, actor_id=user.id,
                    action="user.created", entity_id=created.id))
    db.commit()
    db.refresh(created)
    return created


@app.post("/projects/{project_id}/managers", status_code=204)
def assign_manager(project_id: str, payload: AssignmentIn, actor: User = Depends(admin_user),
                   db: Session = Depends(get_db)) -> None:
    project = db.scalar(select(Project).where(Project.id == project_id, Project.organization_id == actor.organization_id,
                                              Project.deleted_at.is_(None)))
    manager = db.scalar(select(User).where(User.id == payload.user_id,
                                          User.organization_id == actor.organization_id,
                                          User.role == "MANAGER", User.active.is_(True)))
    if not project or not manager:
        raise HTTPException(status_code=404, detail="Проект или менеджер не найден")
    assignment = db.scalar(select(ProjectUser).where(ProjectUser.project_id == project.id, ProjectUser.user_id == manager.id))
    if assignment:
        assignment.active = True
    else:
        db.add(ProjectUser(project_id=project.id, user_id=manager.id))
    db.add(AuditLog(organization_id=actor.organization_id, actor_id=actor.id, project_id=project.id,
                    action="project.manager_assigned", entity_id=manager.id))
    db.commit()


def permitted_project(db: Session, user: User, project_id: str) -> Project:
    query = select(Project).where(Project.id == project_id, Project.organization_id == user.organization_id,
                                  Project.deleted_at.is_(None))
    if user.role not in ("ADMIN", "PLATFORM_ADMIN"):
        query = query.join(ProjectUser, ProjectUser.project_id == Project.id).where(
            ProjectUser.user_id == user.id, ProjectUser.active.is_(True))
    project = db.scalar(query)
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")
    return project


def active_leads_query(project_id: str, db: Session):
    """Keep manual leads and Meta leads from the configured Page/form in this CRM view."""
    settings = project_settings(db, project_id)
    query = select(Lead).where(Lead.project_id == project_id)
    if not settings.meta_page_id:
        return query.where(Lead.source != "META")
    meta_source = Lead.meta_page_id == settings.meta_page_id
    if settings.meta_form_id:
        meta_source = and_(meta_source, Lead.meta_form_id == settings.meta_form_id)
    return query.where(or_(Lead.source != "META", and_(Lead.source == "META", meta_source)))


@app.get("/leads", response_model=list[LeadOut])
def list_leads(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[Lead]:
    permitted_project(db, user, project_id)
    return list(db.scalars(active_leads_query(project_id, db).order_by(Lead.created_at.desc())).all())


@app.get("/leads/{lead_id}/comments", response_model=list[LeadCommentOut])
def list_comments(lead_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[LeadComment]:
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    permitted_project(db, user, lead.project_id)
    return list(db.scalars(select(LeadComment).where(LeadComment.lead_id == lead_id).order_by(LeadComment.created_at)).all())


@app.post("/leads/{lead_id}/comments", response_model=LeadCommentOut, status_code=201)
def add_comment(lead_id: str, payload: LeadCommentIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> LeadComment:
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    permitted_project(db, user, lead.project_id)
    comment = LeadComment(lead_id=lead_id, author_id=user.id, text=payload.text.strip())
    if not comment.text:
        raise HTTPException(status_code=422, detail="Напишите комментарий")
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return comment


@app.get("/leads/{lead_id}/meta-feedback", response_model=MetaFeedbackOut | None)
def get_meta_feedback(lead_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> MetaFeedback | None:
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    permitted_project(db, user, lead.project_id)
    return db.scalar(select(MetaFeedback).where(MetaFeedback.lead_id == lead_id, MetaFeedback.event_name == "QualifiedLead"))


@app.post("/leads/{lead_id}/qualify", response_model=MetaFeedbackOut)
def qualify_and_send(lead_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> MetaFeedback:
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    permitted_project(db, user, lead.project_id)
    if lead.status not in ("QUALIFIED", "WON"):
        old_status = lead.status
        lead.status = "QUALIFIED"
        lead.qualified_at = lead.qualified_at or datetime.now(timezone.utc)
        db.add(LeadStatusHistory(lead_id=lead.id, actor_id=user.id, old_status=old_status, new_status="QUALIFIED"))
        db.add(AuditLog(organization_id=user.organization_id, actor_id=user.id, project_id=lead.project_id,
                        action="lead.qualified", entity_id=lead.id))
    elif lead.qualified_at is None:
        lead.qualified_at = datetime.now(timezone.utc)
    feedback = db.scalar(select(MetaFeedback).where(MetaFeedback.lead_id == lead_id, MetaFeedback.event_name == "QualifiedLead"))
    if not feedback:
        feedback = MetaFeedback(lead_id=lead_id, event_name="QualifiedLead")
        db.add(feedback)
    db.commit()
    if feedback.status == "SENT":
        return feedback
    return send_qualified_feedback(db, feedback, lead)


@app.get("/webhooks/meta", response_class=PlainTextResponse)
def meta_webhook_verify(request: Request) -> str:
    query = request.query_params
    if query.get("hub.mode") != "subscribe" or not get_settings().meta_verify_token or query.get("hub.verify_token") != get_settings().meta_verify_token:
        raise HTTPException(status_code=403, detail="Ошибка проверки Meta")
    return query.get("hub.challenge", "")


@app.post("/webhooks/meta")
async def meta_webhook(request: Request, background: BackgroundTasks, db: Session = Depends(get_db)) -> dict[str, str]:
    body = await request.body()
    if not verify_signature(body, request.headers.get("X-Hub-Signature-256", "")):
        raise HTTPException(status_code=403, detail="Неверная подпись Meta")
    payload = await request.json()
    for entry in payload.get("entry", []):
        page_id = str(entry.get("id", ""))
        integration = db.scalar(select(BusinessIntegration).join(Organization).where(
            BusinessIntegration.meta_page_id == page_id, Organization.active.is_(True)))
        if not integration:
            continue
        settings = business_settings(db, integration.organization_id)
        if not settings.meta_leads_access_token:
            raise HTTPException(503, "Meta не подключена")
        for change in entry.get("changes", []):
            value = change.get("value") or {}
            lead_id = str(value.get("leadgen_id", ""))
            if change.get("field") != "leadgen" or not lead_id:
                continue
            if db.scalar(select(Lead).where(Lead.meta_lead_id == lead_id)):
                continue
            project = db.scalar(select(Project).where(Project.id == integration.project_id, Project.organization_id == integration.organization_id, Project.deleted_at.is_(None)))
            if not project:
                raise HTTPException(status_code=503, detail="Нет активного проекта")
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    response = await client.get(f"https://graph.facebook.com/{settings.meta_api_version}/{lead_id}",
                                                params={"fields": "id,created_time,field_data,form_id,ad_id,ad_name"}, headers={"Authorization": f"Bearer {settings.meta_leads_access_token}"})
                response.raise_for_status()
                data = response.json()
                if str(data.get("id", "")) != lead_id:
                    raise ValueError("Unexpected lead ID")
            except (httpx.HTTPError, ValueError) as exc:
                raise HTTPException(status_code=503, detail="Не удалось получить заявку из Meta") from exc
            form_id = str(data.get("form_id") or value.get("form_id") or "")
            if settings.meta_form_id and form_id != settings.meta_form_id:
                continue
            import_meta_lead(db, project, data, page_id, form_id)
            background.add_task(process_queue, db.get_bind(), integration.organization_id)
    return {"status": "ok"}


@app.post("/leads", response_model=LeadOut, status_code=201)
def create_lead(payload: LeadCreate, background: BackgroundTasks, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Lead:
    project = permitted_project(db, user, payload.project_id)
    score, priority, low_budget = score_lead(project, payload.requested_service, payload.monthly_ad_budget)
    lead = Lead(**payload.model_dump(), score=score, priority=priority, low_budget=low_budget)
    db.add(lead)
    db.flush()
    enqueue(db, project, lead)
    background.add_task(process_queue, db.get_bind(), user.organization_id)
    db.add(LeadStatusHistory(lead_id=lead.id, actor_id=user.id, new_status="NEW"))
    db.add(AuditLog(organization_id=user.organization_id, actor_id=user.id, project_id=project.id,
                    action="lead.created", entity_id=lead.id))
    db.commit()
    db.refresh(lead)
    return lead


@app.patch("/leads/{lead_id}/status", response_model=LeadOut)
def change_lead_status(lead_id: str, payload: StatusIn, user: User = Depends(current_user),
                       db: Session = Depends(get_db)) -> Lead:
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    permitted_project(db, user, lead.project_id)
    if payload.status == "QUALIFIED":
        qualify_and_send(lead_id, user, db)
        db.refresh(lead)
        return lead
    if lead.status == payload.status:
        return lead
    old_status = lead.status
    lead.status = payload.status
    if payload.status == "QUALIFIED" and not lead.qualified_at:
        lead.qualified_at = datetime.now(timezone.utc)
    if payload.status == "WON" and not lead.won_at:
        lead.won_at = datetime.now(timezone.utc)
    db.add(LeadStatusHistory(lead_id=lead.id, actor_id=user.id, old_status=old_status, new_status=payload.status))
    db.add(AuditLog(organization_id=user.organization_id, actor_id=user.id, project_id=lead.project_id,
                    action="lead.status_changed", entity_id=lead.id))
    db.commit()
    db.refresh(lead)
    return lead


@app.patch("/leads/{lead_id}/notes", response_model=LeadOut)
def update_lead_notes(lead_id: str, payload: LeadNotesIn, user: User = Depends(current_user),
                      db: Session = Depends(get_db)) -> Lead:
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    permitted_project(db, user, lead.project_id)
    lead.notes = payload.notes.strip()
    db.add(AuditLog(organization_id=user.organization_id, actor_id=user.id, project_id=lead.project_id,
                    action="lead.notes_updated", entity_id=lead.id))
    db.commit()
    db.refresh(lead)
    return lead


@app.post("/leads/{lead_id}/whatsapp-click", response_model=WhatsAppOut)
def whatsapp_click(lead_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> WhatsAppOut:
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    permitted_project(db, user, lead.project_id)
    digits = re.sub(r"\D", "", lead.phone)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if not 10 <= len(digits) <= 15:
        raise HTTPException(status_code=422, detail="Проверьте номер телефона")
    now = datetime.now(timezone.utc)
    if lead.first_whatsapp_click_at is None:
        lead.first_whatsapp_click_at = now
        created = lead.created_at if lead.created_at.tzinfo else lead.created_at.replace(tzinfo=timezone.utc)
        lead.first_response_seconds = max(0, int((now - created).total_seconds()))
    lead.last_whatsapp_click_at = now
    lead.whatsapp_click_count += 1
    db.add(AuditLog(organization_id=user.organization_id, actor_id=user.id, project_id=lead.project_id,
                    action="lead.whatsapp_opened", entity_id=lead.id))
    db.commit()
    return WhatsAppOut(url=f"https://wa.me/{digits}", first_response_seconds=lead.first_response_seconds)


@app.get("/analytics", response_model=AnalyticsOut)
async def analytics(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> AnalyticsOut:
    permitted_project(db, user, project_id)
    leads = db.scalars(active_leads_query(project_id, db)).all()
    settings = business_settings(db, user.organization_id)
    form_questions: list[dict] | None = None
    chart_leads = leads
    if settings.meta_form_id:
        if not re.fullmatch(r"\d+", settings.meta_form_id):
            raise HTTPException(status_code=400, detail="Неверный ID формы Meta")
        chart_leads = [lead for lead in leads if lead.meta_form_id == settings.meta_form_id]
        try:
            async with httpx.AsyncClient(
                base_url=f"https://graph.facebook.com/{settings.meta_api_version}",
                headers={"Authorization": f"Bearer {settings.meta_leads_access_token}"},
                timeout=15,
            ) as client:
                response = await client.get(f"/{settings.meta_form_id}", params={"fields": "id,name,questions"})
                response.raise_for_status()
                form_questions = response.json().get("questions")
                if not isinstance(form_questions, list):
                    raise ValueError("Meta не вернула вопросы формы")
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=502, detail="Не удалось получить вопросы выбранной формы Meta") from exc
    total = len(leads)
    qualified = sum(lead.status in {"QUALIFIED", "WON"} for lead in leads)
    won = sum(lead.status == "WON" for lead in leads)
    low_budget = sum(lead.low_budget for lead in leads)

    def breakdown(values: list[str]) -> list[BreakdownRow]:
        return [BreakdownRow(label=label, count=count) for label, count in
                sorted(Counter(values).items(), key=lambda item: (-item[1], item[0]))]

    return AnalyticsOut(
        project_id=project_id,
        total=total,
        qualified=qualified,
        won=won,
        low_budget=low_budget,
        qualified_rate=round(100 * qualified / total, 1) if total else 0,
        sale_rate=round(100 * won / total, 1) if total else 0,
        by_status=breakdown([lead.status for lead in leads]),
        by_service=breakdown([lead.requested_service for lead in leads]),
        by_city=breakdown([lead.city.strip() or "Не указан" for lead in leads]),
        questions=question_charts(chart_leads, form_questions),
    )
