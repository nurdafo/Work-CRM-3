import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select, func

from test_foundation import foundation, login, config_settings
from app.integrations import business_settings, decrypt_secrets
from app.meta import import_meta_lead
from app.migrate_businesses import migrate
from app.models import BusinessIntegration, Lead, Organization, Project, TelegramNotification, User
from app.telegram import process_queue


@pytest.fixture
def platform(foundation):
    client, factory = foundation
    with factory() as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        user.role = "PLATFORM_ADMIN"
        db.commit()
    return client, factory, login(client, "admin")


def create(client, headers, name="Business A", username="business-a", integrations=None):
    response = client.post("/admin/businesses", headers=headers, json={
        "name": name, "username": username, "password": "client-password", "integrations": integrations or {}})
    assert response.status_code == 201, response.text
    return response.json()


def lead(client, headers, project_id):
    response = client.post("/leads", headers=headers, json={
        "project_id": project_id, "name": "Client A", "phone": "77012345678", "requested_service": "CRM"})
    assert response.status_code == 201, response.text
    return response.json()


def test_create_login_settings_and_no_environment_fallback(platform, monkeypatch):
    client, factory, root = platform
    a = create(client, root, integrations={"meta_page_id": "123", "meta_leads_access_token": "private-token"})
    b = create(client, root, "Business B", "business-b")
    assert "private-token" not in json.dumps(a)
    assert a["integrations"]["meta_leads_access_token_configured"] is True
    with factory() as db:
        stored = db.get(BusinessIntegration, a["id"])
        assert "private-token" not in stored.secrets
        assert decrypt_secrets(stored.secrets)["meta_leads_access_token"] == "private-token"
        assert business_settings(db, b["id"]).meta_leads_access_token == ""
    owner = login(client, "business-a", "client-password")
    profile = client.get("/auth/me", headers=owner).json()
    assert profile["organization_name"] == "Business A"
    assert profile["role"] == "ADMIN"
    assert [p["id"] for p in client.get("/projects", headers=owner).json()] == [a["project_id"]]
    assert client.get("/admin/businesses", headers=owner).status_code == 403
    assert client.post(f"/admin/businesses/{a['id']}/check", headers=owner).status_code == 403
    assert client.patch(f"/admin/businesses/{a['id']}", headers=root,
        json={"name": "Renamed"}).json()["name"] == "Renamed"
    with factory() as db:
        assert business_settings(db, a["id"]).meta_leads_access_token == "private-token"
    client.patch(f"/admin/businesses/{a['id']}", headers=root, json={"integrations": {"meta_leads_access_token": ""}})
    with factory() as db:
        assert business_settings(db, a["id"]).meta_leads_access_token == ""


def test_foreign_ids_never_grant_access_and_platform_scope_does_not_persist(platform):
    client, factory, root = platform
    a = create(client, root)
    b = create(client, root, "Business B", "business-b")
    owner = login(client, "business-a", "client-password")
    own_lead = lead(client, owner, a["project_id"])
    other = login(client, "business-b", "client-password")
    paths = [
        ("get", f"/leads?project_id={a['project_id']}", None),
        ("get", f"/analytics?project_id={a['project_id']}", None),
        ("get", f"/leads/{own_lead['id']}/comments", None),
        ("post", f"/leads/{own_lead['id']}/comments", {"text": "Intrusion"}),
        ("patch", f"/leads/{own_lead['id']}/notes", {"notes": "Intrusion"}),
        ("patch", f"/leads/{own_lead['id']}/status", {"status": "WON"}),
        ("post", f"/leads/{own_lead['id']}/qualify", None),
        ("get", f"/leads/{own_lead['id']}/meta-feedback", None),
        ("post", f"/leads/{own_lead['id']}/whatsapp-click", None),
        ("post", f"/integrations/meta/sync?project_id={a['project_id']}", None),
        ("patch", f"/projects/{a['project_id']}", {"minimum_ad_budget": 1}),
        ("delete", f"/projects/{a['project_id']}", None),
    ]
    for method, path, body in paths:
        assert client.request(method, path, headers=other, **({"json": body} if body is not None else {})).status_code == 404, path
    assert client.get("/projects", headers={**owner, "X-Organization-ID": b["id"]}).status_code == 403
    scoped = {**root, "X-Organization-ID": a["id"]}
    assert client.get("/leads", params={"project_id": a["project_id"]}, headers=scoped).json()[0]["id"] == own_lead["id"]
    assert client.get("/auth/me", headers=scoped).json()["organization_id"] == a["id"]
    assert client.get("/auth/me", headers=root).json()["organization_id"] != a["id"]
    with factory() as db:
        assert db.scalar(select(User).where(User.username == "admin")).organization_id != a["id"]


def test_reset_password_and_disable_revoke_sessions(platform):
    client, factory, root = platform
    a = create(client, root)
    owner = login(client, "business-a", "client-password")
    assert client.patch(f"/admin/businesses/{a['id']}", headers=root, json={"password": "new-password"}).status_code == 200
    assert client.get("/projects", headers=owner).status_code == 401
    assert client.post("/auth/login", json={"username": "business-a", "password": "client-password"}).status_code == 401
    owner = login(client, "business-a", "new-password")
    assert client.patch(f"/admin/businesses/{a['id']}", headers=root, json={"active": False}).status_code == 200
    assert client.get("/projects", headers=owner).status_code == 401
    assert client.get("/projects", headers={**root, "X-Organization-ID": a["id"]}).status_code == 403
    assert client.post("/auth/login", json={"username": "business-a", "password": "new-password"}).status_code == 401
    client.patch(f"/admin/businesses/{a['id']}", headers=root, json={"active": True})
    assert client.get("/projects", headers=owner).status_code == 401
    login(client, "business-a", "new-password")


def test_unique_page_and_login_are_atomic_and_validation_does_not_echo_secrets(platform):
    client, factory, root = platform
    a = create(client, root, integrations={"meta_page_id": "123"})
    for username, page in [("business-a", "456"), ("business-b", "123")]:
        response = client.post("/admin/businesses", headers=root, json={
            "name": "Duplicate", "username": username, "password": "private-password", "integrations": {"meta_page_id": page}})
        assert response.status_code == 409
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Organization).where(Organization.name == "Duplicate")) == 0
    response = client.patch(f"/admin/businesses/{a['id']}", headers=root,
        json={"password": "secret", "integrations": {"telegram_bot_token": "private-invalid-token"}})
    assert response.status_code == 422
    assert "private-invalid-token" not in response.text and "secret" not in response.text


def test_webhook_routes_by_page_filters_form_and_deduplicates(platform, monkeypatch):
    client, factory, root = platform
    a = create(client, root, integrations={"meta_page_id": "101", "meta_form_id": "201", "meta_leads_access_token": "token-a"})
    b = create(client, root, "Business B", "business-b", {"meta_page_id": "102", "meta_leads_access_token": "token-b"})
    settings = config_settings().model_copy(update={"meta_app_secret": "webhook-secret"})
    monkeypatch.setattr("app.meta.get_settings", lambda: settings)
    seen = []
    async def get(self, url, **kwargs):
        lead_id = str(url).rsplit("/", 1)[1]
        seen.append(kwargs["headers"]["Authorization"])
        return httpx.Response(200, json={"id": lead_id, "form_id": "999" if lead_id == "303" else "201", "field_data": []}, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    payload = {"entry": [{"id": page, "changes": [{"field": "leadgen", "value": {"leadgen_id": lead_id}}]}
                         for page, lead_id in [("101", "301"), ("102", "302"), ("101", "303"), ("999", "304")]]}
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()
    for _ in range(2):
        assert client.post("/webhooks/meta", content=body, headers={"X-Hub-Signature-256": signature}).status_code == 200
    with factory() as db:
        leads = {item.meta_lead_id: item.project_id for item in db.scalars(select(Lead))}
        assert leads == {"301": a["project_id"], "302": b["project_id"]}
    assert seen[:2] == ["Bearer token-a", "Bearer token-b"]


def test_telegram_outbox_retry_destination_and_duplicate_import(platform, monkeypatch):
    client, factory, root = platform
    a = create(client, root, integrations={"telegram_bot_token": "123:bot_a", "telegram_chat_id": "-101"})
    b = create(client, root, "Business B", "business-b", {"telegram_bot_token": "456:bot_b", "telegram_chat_id": "-102"})
    sent = []
    success = False
    def post(url, **kwargs):
        sent.append((url, kwargs["json"]))
        return httpx.Response(200 if success else 503, json={"ok": success})
    monkeypatch.setattr("app.telegram.httpx.post", post)
    with factory() as db:
        for business, meta_id in [(a, "701"), (b, "702")]:
            project = db.get(Project, business["project_id"])
            import_meta_lead(db, project, {"id": meta_id, "field_data": []}, "100", "200")
            import_meta_lead(db, project, {"id": meta_id, "field_data": []}, "100", "200")
        assert db.scalar(select(func.count()).select_from(TelegramNotification)) == 2
        bind = db.get_bind()
    process_queue(bind, a["id"])
    assert len(sent) == 1 and sent[0][1]["chat_id"] == "-101" and "123:bot_a" in sent[0][0]
    with factory() as db:
        item = db.scalar(select(TelegramNotification).where(TelegramNotification.organization_id == a["id"]))
        assert item.status == "RETRY" and item.attempts == 1
        item.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    success = True
    process_queue(bind)
    process_queue(bind)
    assert len(sent) == 3
    assert any("456:bot_b" in url and data["chat_id"] == "-102" for url, data in sent)
    with factory() as db:
        assert all(item.status == "SENT" for item in db.scalars(select(TelegramNotification)))


def test_failed_notification_does_not_lose_manual_lead(platform, monkeypatch):
    client, factory, root = platform
    a = create(client, root, integrations={"telegram_bot_token": "123:bot_a", "telegram_chat_id": "-101"})
    monkeypatch.setattr("app.telegram.send_message", lambda *args: False)
    owner = login(client, "business-a", "client-password")
    created = lead(client, owner, a["project_id"])
    with factory() as db:
        assert db.get(Lead, created["id"])
        item = db.scalar(select(TelegramNotification))
        assert item.status == "RETRY"


def test_migration_is_idempotent_and_preserves_existing_data(foundation):
    client, factory = foundation
    settings = config_settings().model_copy(update={"admin_username": "admin", "meta_page_id": "123", "meta_leads_access_token": "legacy-token"})
    with factory() as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        original_password = user.password_hash
        migrate(db, settings)
        row = db.get(BusinessIntegration, user.organization_id)
        assert user.role == "PLATFORM_ADMIN" and user.password_hash == original_password
        assert business_settings(db, user.organization_id).meta_leads_access_token == "legacy-token"
        row.config = {**row.config, "telegram_chat_id": "123456"}
        db.commit()
        migrate(db, settings.model_copy(update={"meta_leads_access_token": "obsolete"}))
        assert business_settings(db, user.organization_id).meta_leads_access_token == "legacy-token"
        assert row.config["telegram_chat_id"] == "123456"
        assert db.scalar(select(func.count()).select_from(BusinessIntegration)) == 1
        assert user.token_version == 1


def test_connection_checks_and_test_message_use_saved_business_credentials(platform, monkeypatch):
    client, factory, root = platform
    a = create(client, root, integrations={"meta_page_id": "123", "meta_leads_access_token": "page-token",
        "meta_ad_account_id": "act_456", "meta_ads_access_token": "ads-token", "telegram_bot_token": "123:bot_a", "telegram_chat_id": "-101"})
    seen = []
    async def get(self, url, **kwargs):
        seen.append((url, kwargs))
        return httpx.Response(200, json={"id": "123", "account_id": "456", "ok": True})
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    response = client.post(f"/admin/businesses/{a['id']}/check", headers=root)
    assert response.json() == {"meta_page": True, "meta_ads": True, "telegram": True}
    assert seen[0][1]["headers"]["Authorization"] == "Bearer page-token"
    assert seen[1][1]["headers"]["Authorization"] == "Bearer ads-token"
    sent = []
    monkeypatch.setattr("app.telegram.send_message", lambda settings, text: sent.append((settings.telegram_chat_id, text)) or True)
    assert client.post(f"/admin/businesses/{a['id']}/telegram-test", headers=root).json() == {"sent": True}
    assert sent[0][0] == "-101"


def test_quality_feedback_uses_each_business_dataset_and_token(platform, monkeypatch):
    client, factory, root = platform
    businesses = [create(client, root, f"Business {i}", f"business-{i}", {
        "meta_page_id": str(100 + i), "meta_dataset_id": str(200 + i), "meta_capi_access_token": f"capi-{i}"
    }) for i in (1, 2)]
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs["headers"]["Authorization"]))
        return httpx.Response(200, json={"events_received": 1})
    monkeypatch.setattr("app.meta.httpx.post", post)
    for i, business in enumerate(businesses, 1):
        with factory() as db:
            imported = import_meta_lead(db, db.get(Project, business["project_id"]),
                {"id": str(300 + i), "field_data": []}, str(100 + i), "")
            lead_id = imported.id
        headers = login(client, f"business-{i}", "client-password")
        assert client.post(f"/leads/{lead_id}/qualify", headers=headers).json()["status"] == "SENT"
        assert client.post(f"/leads/{lead_id}/qualify", headers=headers).json()["status"] == "SENT"
    assert len(calls) == 2
    assert calls[0][0].endswith("/201/events") and calls[0][1] == "Bearer capi-1"
    assert calls[1][0].endswith("/202/events") and calls[1][1] == "Bearer capi-2"


def test_no_notification_for_business_without_bot_and_disabled_queue_is_paused(platform, monkeypatch):
    client, factory, root = platform
    a = create(client, root, integrations={"telegram_chat_id": "123", "meta_leads_access_token": "only-meta"})
    owner = login(client, "business-a", "client-password")
    lead(client, owner, a["project_id"])
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(TelegramNotification)) == 0
    client.patch(f"/admin/businesses/{a['id']}", headers=root, json={"integrations": {"telegram_bot_token": "123:bot"}})
    monkeypatch.setattr("app.telegram.send_message", lambda *args: False)
    lead(client, owner, a["project_id"])
    client.patch(f"/admin/businesses/{a['id']}", headers=root, json={"active": False})
    with factory() as db:
        item = db.scalar(select(TelegramNotification))
        item.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        bind = db.get_bind()
    calls = []
    monkeypatch.setattr("app.telegram.send_message", lambda *args: calls.append(args) or True)
    process_queue(bind)
    assert not calls
