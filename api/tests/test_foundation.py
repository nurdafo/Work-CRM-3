import os
import asyncio
import httpx
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ["ADMIN_USERNAME"] = "admin"
os.environ["INTEGRATION_ENCRYPTION_KEY"] = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
for key in ("META_PAGE_ID", "META_FORM_ID", "META_LEADS_ACCESS_TOKEN", "META_CAPI_ACCESS_TOKEN", "META_DATASET_ID", "META_PIXEL_ID", "META_AD_LABELS"):
    os.environ[key] = ""
os.environ["JWT_SECRET"] = "test-secret-that-is-at-least-32-characters-long"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.config import get_settings as config_settings
from app.main import app
from app.models import AuditLog, Lead, Organization, Project, ProjectUser, User
from app.meta import import_meta_lead, sync_page_leads
from app.question_analytics import question_charts
from app.security import hash_password


def set_business_settings(factory, settings):
    from app.integrations import CONFIG_DEFAULTS, SECRET_FIELDS, encrypt_secrets
    from app.models import BusinessIntegration
    with factory() as db:
        org = db.scalar(select(Organization).where(Organization.name == "ADS.KZ"))
        row = db.get(BusinessIntegration, org.id)
        if row is None:
            row = BusinessIntegration(organization_id=org.id,
                project_id=db.scalar(select(Project.id).where(Project.organization_id == org.id)),
                owner_id=db.scalar(select(User.id).where(User.username == "admin")), config={}, secrets="")
            db.add(row)
        row.meta_page_id = getattr(settings, "meta_page_id", "") or None
        row.config = {key: getattr(settings, key, default) for key, default in CONFIG_DEFAULTS.items()}
        row.secrets = encrypt_secrets({key: getattr(settings, key, "") for key in SECRET_FIELDS})
        db.commit()


@pytest.fixture
def foundation(monkeypatch):
    isolated_settings = config_settings().model_copy(update={
        "meta_page_id": "", "meta_form_id": "", "meta_leads_access_token": "",
    })
    monkeypatch.setattr("app.main.get_settings", lambda: isolated_settings)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        org = Organization(name="ADS.KZ")
        other = Organization(name="Other agency")
        db.add_all([org, other])
        db.flush()
        db.add_all([
            User(organization_id=org.id, username="admin", full_name="Admin", role="ADMIN", password_hash=hash_password("correct-password-123")),
            User(organization_id=org.id, username="manager", full_name="Manager", role="MANAGER", password_hash=hash_password("correct-password-123")),
            Project(organization_id=org.id, name="ADS.KZ"),
            Project(organization_id=other.id, name="Private project"),
        ])
        db.commit()

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client, factory
    app.dependency_overrides.clear()
    engine.dispose()


def login(client, username, password="correct-password-123"):
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_admin_can_create_project_and_manager_but_manager_cannot(foundation):
    client, factory = foundation
    admin = login(client, "admin")
    manager = login(client, "manager")
    assert client.post("/projects", headers=manager, json={"name": "Not allowed"}).status_code == 403
    response = client.post("/projects", headers=admin, json={"name": "Client A", "minimum_ad_budget": 200000})
    assert response.status_code == 201
    project_id = response.json()["id"]
    assert client.get("/projects", headers=manager).json() == []
    with factory() as db:
        user_id = db.scalar(select(User.id).where(User.username == "manager"))
    assert client.post(f"/projects/{project_id}/managers", headers=admin, json={"user_id": user_id}).status_code == 204
    assert [p["name"] for p in client.get("/projects", headers=manager).json()] == ["Client A"]
    assert client.get("/users", headers=manager).status_code == 403
    with factory() as db:
        assert db.scalar(select(ProjectUser).where(ProjectUser.project_id == project_id))
        assert db.scalar(select(AuditLog).where(AuditLog.action == "project.created"))


def test_invalid_login_and_foreign_project_hidden(foundation):
    client, factory = foundation
    assert client.post("/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    assert client.get("/projects").status_code == 401
    admin = login(client, "admin")
    assert [p["name"] for p in client.get("/projects", headers=admin).json()] == ["ADS.KZ"]
    password_only = client.post("/auth/login", json={"password": "correct-password-123"})
    assert password_only.status_code == 200


def test_user_can_change_password(foundation):
    client, factory = foundation
    headers = login(client, "manager")
    assert client.post("/auth/password", headers=headers, json={"current_password": "wrong", "new_password": "abc"}).status_code == 400
    assert client.post("/auth/password", headers=headers, json={"current_password": "correct-password-123", "new_password": "abc"}).status_code == 204
    assert client.post("/auth/login", json={"username": "manager", "password": "correct-password-123"}).status_code == 401
    login(client, "manager", "abc")


def test_project_can_be_removed_and_leads_move_on_board(foundation):
    client, factory = foundation
    admin = login(client, "admin")
    project_id = client.get("/projects", headers=admin).json()[0]["id"]
    created = client.post("/leads", headers=admin, json={
        "project_id": project_id, "name": "Иван", "phone": "+77001234567",
        "requested_service": "TARGET", "monthly_ad_budget": 100000,
    })
    assert created.status_code == 201
    lead = created.json()
    assert lead["low_budget"] is True
    assert lead["status"] == "NEW"
    changed = client.patch(f"/leads/{lead['id']}/status", headers=admin, json={"status": "CONTACTED"})
    assert changed.status_code == 200
    assert changed.json()["status"] == "CONTACTED"
    note = client.patch(f"/leads/{lead['id']}/notes", headers=admin, json={"notes": "Позвонить завтра"})
    assert note.status_code == 200
    assert note.json()["notes"] == "Позвонить завтра"
    assert len(client.get(f"/leads?project_id={project_id}", headers=admin).json()) == 1
    assert client.delete(f"/projects/{project_id}", headers=admin).status_code == 204
    assert client.get("/projects", headers=admin).json() == []
    assert client.get(f"/leads?project_id={project_id}", headers=admin).status_code == 404


def test_browser_can_preflight_project_delete(foundation):
    client, factory = foundation
    response = client.options("/projects/example", headers={
        "Origin": "http://127.0.0.1:5173",
        "Access-Control-Request-Method": "DELETE",
        "Access-Control-Request-Headers": "authorization",
    })
    assert response.status_code == 200
    assert "DELETE" in response.headers["access-control-allow-methods"]


def test_analytics_counts_confirmed_stages_and_budget(foundation):
    client, factory = foundation
    admin = login(client, "admin")
    project_id = client.get("/projects", headers=admin).json()[0]["id"]
    empty = client.get(f"/analytics?project_id={project_id}", headers=admin).json()
    assert empty["total"] == 0
    assert empty["qualified_rate"] == 0
    assert empty["questions"] == []
    lead = client.post("/leads", headers=admin, json={
        "project_id": project_id, "name": "Клиент", "phone": "+77001234567",
        "requested_service": "TARGET", "monthly_ad_budget": 100000, "city": "Алматы",
    }).json()
    client.patch(f"/leads/{lead['id']}/status", headers=admin, json={"status": "QUALIFIED"})
    client.patch(f"/leads/{lead['id']}/status", headers=admin, json={"status": "WON"})
    report = client.get(f"/analytics?project_id={project_id}", headers=admin).json()
    assert (report["total"], report["qualified"], report["won"], report["low_budget"]) == (1, 1, 1, 1)
    assert report["qualified_rate"] == 100
    assert report["by_city"] == [{"label": "Алматы", "count": 1}]


def test_moving_card_back_from_sale_updates_persisted_stage_and_totals(foundation):
    client, factory = foundation
    admin = login(client, "admin")
    project_id = client.get("/projects", headers=admin).json()[0]["id"]
    lead = client.post("/leads", headers=admin, json={
        "project_id": project_id, "name": "Client", "phone": "+77001234567",
        "requested_service": "TARGET",
    }).json()
    for status in ("CONTACTED", "QUALIFIED", "WON", "CONTACTED"):
        response = client.patch(f"/leads/{lead['id']}/status", headers=admin, json={"status": status})
        assert response.status_code == 200
    listed = client.get(f"/leads?project_id={project_id}", headers=admin).json()
    report = client.get(f"/analytics?project_id={project_id}", headers=admin).json()
    assert listed[0]["status"] == "CONTACTED"
    assert (report["qualified"], report["won"]) == (0, 0)


def test_whatsapp_attempt_and_budget_setting(foundation):
    client, factory = foundation
    admin = login(client, "admin")
    project_id = client.get("/projects", headers=admin).json()[0]["id"]
    lead = client.post("/leads", headers=admin, json={
        "project_id": project_id, "name": "Клиент", "phone": "8 (700) 123-45-67",
        "requested_service": "TARGET", "monthly_ad_budget": 100000,
    }).json()
    assert lead["low_budget"] is True
    first = client.post(f"/leads/{lead['id']}/whatsapp-click", headers=admin)
    second = client.post(f"/leads/{lead['id']}/whatsapp-click", headers=admin)
    assert first.status_code == 200
    assert first.json()["url"] == "https://wa.me/77001234567"
    assert second.json()["first_response_seconds"] == first.json()["first_response_seconds"]
    unchanged = client.get(f"/leads?project_id={project_id}", headers=admin).json()[0]
    assert unchanged["status"] == "NEW"
    assert unchanged["whatsapp_click_count"] == 2
    assert client.patch(f"/projects/{project_id}", headers=admin,
                        json={"minimum_ad_budget": 50000}).status_code == 200
    rescored = client.get(f"/leads?project_id={project_id}", headers=admin).json()[0]
    assert rescored["low_budget"] is False


def test_card_comments_and_manual_quality_state(foundation):
    client, factory = foundation
    admin = login(client, "admin")
    project_id = client.get("/projects", headers=admin).json()[0]["id"]
    lead = client.post("/leads", headers=admin, json={
        "project_id": project_id, "name": "Клиент", "phone": "+77001234567",
        "email": "client@example.com", "requested_service": "CRM",
    }).json()
    assert lead["email"] == "client@example.com"
    assert lead["form_answers"] == []
    comment = client.post(f"/leads/{lead['id']}/comments", headers=admin, json={"text": "Позвонить вечером"})
    assert comment.status_code == 201
    assert client.get(f"/leads/{lead['id']}/comments", headers=admin).json()[0]["text"] == "Позвонить вечером"
    feedback = client.post(f"/leads/{lead['id']}/qualify", headers=admin)
    assert feedback.status_code == 200
    assert feedback.json()["status"] == "NOT_ELIGIBLE"
    assert client.post(f"/leads/{lead['id']}/qualify", headers=admin).json()["id"] == feedback.json()["id"]
    assert client.get(f"/leads?project_id={project_id}", headers=admin).json()[0]["status"] == "QUALIFIED"


def test_qualifying_meta_lead_sends_one_capi_event_and_reports_result(foundation, monkeypatch):
    client, factory = foundation
    settings = config_settings().model_copy(update={
        "meta_page_id": "page-1", "meta_form_id": "form-1",
        "meta_dataset_id": "dataset-1", "meta_capi_access_token": "test-capi-token",
    })
    set_business_settings(factory, settings)
    set_business_settings(factory, settings)
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(is_success=True, json=lambda: {"events_received": 1})

    monkeypatch.setattr("app.meta.httpx.post", fake_post)
    with factory() as db:
        project = db.scalar(select(Project).where(Project.name == "ADS.KZ"))
        imported = import_meta_lead(db, project, {"id": "12345", "field_data": []}, "page-1", "form-1")
        lead_id = imported.id
    admin = login(client, "admin")
    changed = client.patch(f"/leads/{lead_id}/status", headers=admin, json={"status": "QUALIFIED"})
    assert changed.status_code == 200
    assert changed.json()["meta_feedback_status"] == "SENT"
    assert client.post(f"/leads/{lead_id}/qualify", headers=admin).json()["status"] == "SENT"
    assert len(calls) == 1
    url, request = calls[0]
    assert url.endswith("/dataset-1/events")
    assert "test-capi-token" not in url
    assert request["headers"]["Authorization"] == "Bearer test-capi-token"
    assert request["json"]["data"][0]["user_data"]["lead_id"] == "12345"
    assert request["json"]["data"][0]["event_id"] == "qualified:12345"
    sale = client.patch(f"/leads/{lead_id}/status", headers=admin, json={"status": "WON"})
    assert sale.status_code == 200
    assert client.post(f"/leads/{lead_id}/qualify", headers=admin).json()["status"] == "SENT"
    assert client.get(f"/leads?project_id={sale.json()['project_id']}", headers=admin).json()[0]["status"] == "WON"
    assert len(calls) == 1


def test_audience_readiness_counts_only_current_qualified_form_leads(foundation, monkeypatch):
    client, factory = foundation
    settings = config_settings().model_copy(update={
        "meta_page_id": "page-1", "meta_form_id": "form-1", "meta_lookalike_min_leads": 20,
    })
    set_business_settings(factory, settings)
    admin = login(client, "admin")
    project_id = client.get("/projects", headers=admin).json()[0]["id"]
    with factory() as db:
        project = db.get(Project, project_id)
        for index in range(19):
            lead = import_meta_lead(db, project, {"id": str(1000 + index), "field_data": []}, "page-1", "form-1")
            lead.status = "QUALIFIED"
            lead.qualified_at = datetime.now(timezone.utc)
        other = import_meta_lead(db, project, {"id": "9999", "field_data": []}, "page-1", "form-2")
        other.status = "QUALIFIED"
        other.qualified_at = datetime.now(timezone.utc)
        db.commit()
    path = f"/integrations/meta/audience-readiness?project_id={project_id}"
    assert client.get(path, headers=admin).json() == {"qualified": 19, "threshold": 20, "ready": False}
    with factory() as db:
        lead = import_meta_lead(db, db.get(Project, project_id), {"id": "1020", "field_data": []}, "page-1", "form-1")
        lead.status = "MEETING"
        lead.qualified_at = datetime.now(timezone.utc)
        db.commit()
    assert client.get(path, headers=admin).json() == {"qualified": 20, "threshold": 20, "ready": True}
    client.patch(f"/leads/{lead.id}/status", headers=admin, json={"status": "UNQUALIFIED"})
    assert client.get(path, headers=admin).json() == {"qualified": 19, "threshold": 20, "ready": False}


def test_meta_answers_preserved_and_duplicate_ignored(foundation, monkeypatch):
    client, factory = foundation
    set_business_settings(factory, config_settings().model_copy(
        update={"meta_page_id": "page-1", "meta_form_id": ""}))
    with factory() as db:
        project = db.scalar(select(Project).where(Project.name == "ADS.KZ"))
        payload = {"id": "meta-123", "field_data": [
            {"name": "full_name", "values": ["Алия"]},
            {"name": "phone_number", "values": ["+77001234567"]},
            {"name": "Сколько готовы вложить?", "values": ["200 000 ₸"]},
        ]}
        first = import_meta_lead(db, project, payload, "page-1", "form-1")
        second = import_meta_lead(db, project, payload, "page-1", "form-1")
        assert first.id == second.id
        assert db.query(Lead).count() == 1
        assert first.form_answers[2] == {"question": "Сколько готовы вложить?", "answers": ["200 000 ₸"]}
    admin = login(client, "admin")
    project_id = client.get("/projects", headers=admin).json()[0]["id"]
    lead = client.get(f"/leads?project_id={project_id}", headers=admin).json()[0]
    assert lead["source"] == "META"
    assert lead["meta_lead_id"] == "meta-123"


def test_meta_ad_id_uses_configured_business_label(foundation, monkeypatch):
    _, factory = foundation
    settings = config_settings().model_copy(update={
        "meta_ad_labels": "52535164517030=Агробизнес;52535153314030=Строитель",
    })
    set_business_settings(factory, settings)
    with factory() as db:
        project = db.scalar(select(Project).where(Project.name == "ADS.KZ"))
        lead = import_meta_lead(db, project, {
            "id": "meta-ad-lead",
            "ad_id": "52535164517030",
            "ad_name": "Meta internal name",
            "field_data": [],
        }, "page-1", "form-1")
        assert lead.meta_ad_id == "52535164517030"
        assert lead.meta_ad_name == "Агробизнес"


def test_meta_webhook_rejects_unsigned_data(foundation):
    client, factory = foundation
    assert client.post("/webhooks/meta", json={"entry": []}).status_code == 403


def test_meta_page_sync_imports_form_leads_without_pixel_or_webhook(foundation, monkeypatch):
    client, factory = foundation
    set_business_settings(factory, config_settings().model_copy(
        update={"meta_page_id": "123", "meta_form_id": ""}))
    admin = login(client, "admin")
    project_id = client.get("/projects", headers=admin).json()[0]["id"]
    requested = []

    def graph(request: httpx.Request) -> httpx.Response:
        requested.append((request.url.path, request.url.params.get("after")))
        assert request.headers["Authorization"] == "Bearer example-leads-token"
        if request.url.path.endswith("/123/leadgen_forms"):
            return httpx.Response(200, json={"data": [{"id": "456", "name": "ADS.KZ"}]})
        if request.url.path.endswith("/456/leads") and not request.url.params.get("after"):
            return httpx.Response(200, json={"data": [{"id": "789", "field_data": [
                {"name": "full_name", "values": ["Алия"]},
                {"name": "В какой сфере работает ваш бизнес?", "values": ["Стоматология"]},
            ]}], "paging": {"cursors": {"after": "page-two"}}})
        if request.url.path.endswith("/456/leads") and request.url.params.get("after") == "page-two":
            return httpx.Response(200, json={"data": [{"id": "790", "field_data": [
                {"name": "full_name", "values": ["Анара"]},
            ]}]})
        return httpx.Response(404, json={"error": {"message": "Unexpected request"}})

    async def pull(db):
        async with httpx.AsyncClient(base_url="https://graph.facebook.com/v23.0",
                                     headers={"Authorization": "Bearer example-leads-token"},
                                     transport=httpx.MockTransport(graph)) as graph_client:
            return await sync_page_leads(db, db.get(Project, project_id), graph_client, "123")

    with factory() as db:
        assert asyncio.run(pull(db)) == {"forms": 1, "imported": 2, "existing": 0}
        assert asyncio.run(pull(db)) == {"forms": 1, "imported": 0, "existing": 2}
    assert ("/v23.0/456/leads", "page-two") in requested
    report = client.get(f"/analytics?project_id={project_id}", headers=admin).json()
    assert report["questions"][0]["answered"] == 1
    assert report["questions"][0]["options"][0]["count"] == 1


def test_meta_page_sync_uses_only_selected_form(foundation):
    client, factory = foundation
    admin = login(client, "admin")
    project_id = client.get("/projects", headers=admin).json()[0]["id"]
    requested = []

    def graph(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path.endswith("/123/leadgen_forms"):
            return httpx.Response(200, json={"data": [{"id": "111"}, {"id": "222"}]})
        if request.url.path.endswith("/222/leads"):
            return httpx.Response(200, json={"data": [{"id": "333", "field_data": [
                {"name": "full_name", "values": ["Client"]},
                {"name": "phone_number", "values": ["+77000000000"]},
            ]}]})
        return httpx.Response(404)

    async def pull(db, form_id):
        async with httpx.AsyncClient(base_url="https://graph.facebook.com/v23.0",
                                     transport=httpx.MockTransport(graph)) as graph_client:
            return await sync_page_leads(db, db.get(Project, project_id), graph_client, "123", form_id)

    with factory() as db:
        assert asyncio.run(pull(db, "222")) == {"forms": 1, "imported": 1, "existing": 0}
        assert asyncio.run(pull(db, "222")) == {"forms": 1, "imported": 0, "existing": 1}
        with pytest.raises(ValueError, match="Meta"):
            asyncio.run(pull(db, "999"))
    assert not any(path.endswith("/111/leads") for path in requested)


def test_crm_hides_meta_leads_from_a_different_page(foundation, monkeypatch):
    client, factory = foundation
    admin = login(client, "admin")
    project_id = client.get("/projects", headers=admin).json()[0]["id"]
    with factory() as db:
        project = db.get(Project, project_id)
        for lead_id, page_id, form_id in (
            ("old", "old-page", "old-form"),
            ("other-form", "new-page", "other-form"),
            ("current", "new-page", "selected-form"),
        ):
            import_meta_lead(db, project, {"id": lead_id, "field_data": [
                {"name": "full_name", "values": ["Client"]},
                {"name": "question?", "values": ["yes"]},
            ]}, page_id, form_id)
    set_business_settings(factory, config_settings().model_copy(
        update={"meta_page_id": "new-page", "meta_form_id": "selected-form"}))
    leads = client.get(f"/leads?project_id={project_id}", headers=admin).json()
    assert [lead["meta_lead_id"] for lead in leads] == ["current"]
    set_business_settings(factory, config_settings().model_copy(
        update={"meta_page_id": "new-page", "meta_form_id": ""}))
    report = client.get(f"/analytics?project_id={project_id}", headers=admin).json()
    assert report["total"] == 2
    assert report["questions"][0]["answered"] == 2


def test_meta_question_charts_preserve_five_exact_form_questions(foundation):
    client, factory = foundation
    admin = login(client, "admin")
    project_id = client.get("/projects", headers=admin).json()[0]["id"]
    with factory() as db:
        project = db.get(Project, project_id)
        import_meta_lead(db, project, {"id": "lead-one", "field_data": [
            {"name": "full_name", "values": ["Client"]},
            {"name": "first_question?", "values": ["yes"]},
        ]}, "123", "222")
        import_meta_lead(db, project, {"id": "wrong-form", "field_data": [
            {"name": "unrelated_question?", "values": ["wrong"]},
        ]}, "123", "111")
        selected = db.scalars(select(Lead).where(Lead.meta_form_id == "222")).all()
        metadata = [{"key": f"{name}_question?", "label": f"Exact question {index}?",
                     "type": "CUSTOM", "options": [{"key": "yes", "value": "Yes"}]}
                    for index, name in enumerate(("first", "second", "third", "fourth", "fifth"), 1)]
        charts = question_charts(selected, metadata)
    assert [chart.question for chart in charts] == [f"Exact question {i}?" for i in range(1, 6)]
    assert [chart.answered for chart in charts] == [1, 0, 0, 0, 0]
    assert charts[0].options[0].label == "Yes"
    assert charts[0].options[0].count == 1


def test_meta_connection_status_needs_only_page_and_leads_token(foundation, monkeypatch):
    client, factory = foundation
    set_business_settings(factory, SimpleNamespace(
        meta_page_id="123", meta_form_id="", meta_leads_access_token="lead-token"))
    async def matching_page(_settings):
        return True
    monkeypatch.setattr("app.main.meta_page_token_matches", matching_page)
    admin = login(client, "admin")
    assert client.get("/integrations/meta/status", headers=admin).json() == {"connected": True}


def test_meta_sync_rejects_token_for_a_different_page(foundation, monkeypatch):
    client, factory = foundation
    async def different_page(_settings):
        return False
    set_business_settings(factory, SimpleNamespace(
        meta_page_id="123", meta_form_id="", meta_leads_access_token="lead-token"))
    monkeypatch.setattr("app.main.meta_page_token_matches", different_page)
    admin = login(client, "admin")
    project_id = client.get("/projects", headers=admin).json()[0]["id"]
    assert client.get("/integrations/meta/status", headers=admin).json() == {"connected": False}
    response = client.post(f"/integrations/meta/sync?project_id={project_id}", headers=admin)
    assert response.status_code == 400
    assert client.get(f"/leads?project_id={project_id}", headers=admin).json() == []


def test_meta_question_charts_use_actual_form_fields_and_ignore_contacts(foundation, monkeypatch):
    client, factory = foundation
    set_business_settings(factory, config_settings().model_copy(
        update={"meta_page_id": "page-1", "meta_form_id": ""}))
    admin = login(client, "admin")
    project_id = client.get("/projects", headers=admin).json()[0]["id"]
    client.post("/leads", headers=admin, json={
        "project_id": project_id, "name": "Ручная заявка", "phone": "+77001234567",
        "requested_service": "TARGET",
    })
    with factory() as db:
        project = db.get(Project, project_id)
        for lead_id, answers in (
            ("meta-1", [("какая_у_вас_сфера_бизнеса?", "строительство"),
                        ("какой_рекламный_бюджет_вы_готовы_выделять_в_месяц?", "до_150_000_₸"),
                        ("что_у_вас_уже_есть?", "ничего_нет"),
                        ("когда_планируете_запуск_рекламы?", "в_ближайшие_дни")]),
            ("meta-2", [("Какая у вас сфера бизнеса?", "Строительство"),
                        ("Какой рекламный бюджет вы готовы выделять в месяц?", "До 150 000 ₸")]),
            ("meta-3", [("какая_у_вас_сфера_бизнеса?", "агробизнес")]),
        ):
            import_meta_lead(db, project, {"id": lead_id, "field_data": [
                {"name": "full_name", "values": ["Клиент"]},
                {"name": "phone_number", "values": ["+77001234567"]},
                {"name": "Город", "values": ["Алматы"]},
                *({"name": question, "values": [answer]} for question, answer in answers),
            ]}, "page-1", "form-1")

    report = client.get(f"/analytics?project_id={project_id}", headers=admin).json()
    charts = report["questions"]
    assert len(charts) == 4
    assert [chart["answered"] for chart in charts] == [3, 2, 1, 1]
    assert all("имя" not in chart["question"].lower() and "phone" not in chart["question"].lower() for chart in charts)
    industry = {option["label"]: option for option in charts[0]["options"]}
    assert industry["Строительство"] == {"label": "Строительство", "count": 2, "percent": 66.7}
    assert industry["Агробизнес"] == {"label": "Агробизнес", "count": 1, "percent": 33.3}
    budget = {option["label"]: option for option in charts[1]["options"]}
    assert budget["До 150 000 ₸"] == {"label": "До 150 000 ₸", "count": 2, "percent": 100}
    assert charts[2]["options"][0]["label"] == "Ничего нет"
    assert report["total"] == 4  # Existing all-source metrics remain unchanged.
