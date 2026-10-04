from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginIn(BaseModel):
    username: str | None = None
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_id: str
    username: str
    full_name: str
    role: str
    active: bool
    organization_name: str = ""
    home_organization_id: str = ""


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    full_name: str = Field(min_length=2, max_length=180)
    password: str
    role: str = "MANAGER"


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    description: str = Field(default="", max_length=1000)
    minimum_ad_budget: int = Field(default=150000, ge=0)
    currency: str = Field(default="KZT", pattern=r"^[A-Z]{3}$")


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_id: str
    name: str
    description: str
    minimum_ad_budget: int
    currency: str
    active: bool
    created_at: datetime


class ProjectSettingsIn(BaseModel):
    minimum_ad_budget: int = Field(ge=0)


class LeadCreate(BaseModel):
    project_id: str
    name: str = Field(min_length=2, max_length=180)
    phone: str = Field(min_length=5, max_length=40)
    email: str = Field(default="", max_length=255)
    company_name: str = Field(default="", max_length=180)
    city: str = Field(default="", max_length=120)
    requested_service: str = Field(pattern=r"^(TARGET|CRM|API|COMPLEX)$")
    monthly_ad_budget: int | None = Field(default=None, ge=0)
    notes: str = Field(default="", max_length=5000)


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    name: str
    phone: str
    email: str
    source: str
    meta_lead_id: str | None
    meta_form_id: str | None
    meta_page_id: str | None
    meta_ad_id: str | None
    meta_ad_name: str | None
    form_answers: list[dict]
    meta_feedback_status: str | None
    company_name: str
    city: str
    requested_service: str
    monthly_ad_budget: int | None
    status: str
    score: int
    priority: str
    low_budget: bool
    notes: str
    created_at: datetime
    whatsapp_click_count: int
    first_response_seconds: int | None


class StatusIn(BaseModel):
    status: str = Field(pattern=r"^(NEW|CONTACTED|QUALIFIED|MEETING|PROPOSAL|WON|LOST|UNQUALIFIED)$")


class LeadNotesIn(BaseModel):
    notes: str = Field(max_length=5000)


class LeadCommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


class LeadCommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    lead_id: str
    text: str
    created_at: datetime


class MetaFeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    lead_id: str
    event_name: str
    status: str
    error: str
    attempts: int
    sent_at: datetime | None


class BreakdownRow(BaseModel):
    label: str
    count: int


class QuestionAnswerOut(BaseModel):
    label: str
    count: int
    percent: float


class QuestionAnalyticsOut(BaseModel):
    question: str
    answered: int
    options: list[QuestionAnswerOut]


class AnalyticsOut(BaseModel):
    project_id: str
    total: int
    qualified: int
    won: int
    low_budget: int
    qualified_rate: float
    sale_rate: float
    by_status: list[BreakdownRow]
    by_service: list[BreakdownRow]
    by_city: list[BreakdownRow]
    questions: list[QuestionAnalyticsOut]


class WhatsAppOut(BaseModel):
    url: str
    first_response_seconds: int | None


class AssignmentIn(BaseModel):
    user_id: str
