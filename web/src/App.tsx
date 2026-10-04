import { useEffect, useState } from "react";
import "./app.css";
import BusinessPanel from "./BusinessPanel";

type User = { id: string; full_name: string; role: string; organization_id: string; organization_name: string; home_organization_id: string };
type Project = { id: string; name: string; minimum_ad_budget: number };
type Lead = {
  id: string;
  project_id: string;
  name: string;
  phone: string;
  email: string;
  source: string;
  meta_lead_id: string | null;
  meta_form_id: string | null;
  meta_page_id: string | null;
  meta_ad_id: string | null;
  meta_ad_name: string | null;
  form_answers: { question: string; answers: string[] }[];
  meta_feedback_status: string | null;
  company_name: string;
  city: string;
  requested_service: string;
  monthly_ad_budget: number | null;
  status: string;
  priority: string;
  low_budget: boolean;
  notes: string;
  created_at: string;
  whatsapp_click_count: number;
};
type Analytics = {
  total: number;
  qualified: number;
  won: number;
  low_budget: number;
  qualified_rate: number;
  sale_rate: number;
  by_service: { label: string; count: number }[];
  by_city: { label: string; count: number }[];
  questions: QuestionChart[];
};
type QuestionChart = {
  question: string;
  answered: number;
  options: { label: string; count: number; percent: number }[];
};
type View = "leads" | "analytics" | "settings" | "businesses";
type BoardStage = "new" | "work" | "qualified" | "won";
type LeadComment = { id: string; text: string; created_at: string };
type MetaSync = { forms: number; imported: number; existing: number };
type MetaFeedbackResult = { status: string; error: string };
type AudienceReadiness = { qualified: number; threshold: number; ready: boolean };
type LeadForm = {
  name: string;
  phone: string;
  email: string;
  requested_service: string;
  monthly_ad_budget: string;
  company_name: string;
  city: string;
  notes: string;
};

const API = import.meta.env.DEV ? "http://127.0.0.1:8000" : "/api";
const serviceNames: Record<string, string> = {
  TARGET: "Таргетированная реклама",
  CRM: "CRM",
  API: "API",
  COMPLEX: "Несколько услуг",
};
const freshForm: LeadForm = {
  name: "", phone: "", email: "", requested_service: "TARGET", monthly_ad_budget: "",
  company_name: "", city: "", notes: "",
};

async function apiRequest<T>(path: string, token: string | null, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${API}${path}`, { ...init, headers });
  if (!response.ok) {
    let message = "Не удалось выполнить действие";
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch { /* Network/server response without JSON. */ }
    throw new Error(message);
  }
  return response.status === 204 ? undefined as T : await response.json() as T;
}

function formatBudget(value: number | null): string {
  return value === null ? "Бюджет не указан" : `${value.toLocaleString("ru-RU")} ₸ / мес`;
}

function qualityNotice(lead: Lead): string {
  if (lead.source !== "META" || lead.meta_feedback_status === "NOT_ELIGIBLE") return "Клиент отмечен как качественный в CRM. Эта заявка не передаётся в Meta.";
  if (lead.meta_feedback_status === "SENT") return "Клиент отмечен как качественный. Meta приняла сигнал о качестве.";
  if (lead.meta_feedback_status === "WAITING_SETUP") return "Отметка сохранена. Для отправки в Meta добавьте набор данных и токен Conversions API.";
  if (lead.meta_feedback_status === "FAILED") return "Отметка сохранена, но Meta не приняла сигнал. Проверьте настройки и повторите отправку.";
  return "Клиент отмечен как качественный в CRM.";
}

function stageOf(status: string): BoardStage | "closed" {
  if (status === "NEW") return "new";
  if (["CONTACTED", "MEETING", "PROPOSAL"].includes(status)) return "work";
  if (status === "QUALIFIED") return "qualified";
  if (status === "WON") return "won";
  return "closed";
}

const boardColumns = [
  { id: "new", title: "Новые", hint: "Нужно связаться", status: "NEW" },
  { id: "work", title: "В работе", hint: "Обсудить задачу", status: "CONTACTED" },
  { id: "qualified", title: "Подходят", hint: "Довести до продажи", status: "QUALIFIED" },
  { id: "won", title: "Продажи", hint: "Успешно завершены", status: "WON" },
] as const;

const pieColors = ["#296f5a", "#c8953d", "#a8c3b4", "#9fb9c2", "#bdb6a2", "#78998b", "#d0ae98"];

function piePoint(angle: number): [number, number] {
  const radians = (angle - 90) * Math.PI / 180;
  return [110 + 88 * Math.cos(radians), 110 + 88 * Math.sin(radians)];
}

function PieQuestion({ chart, index }: { chart: QuestionChart; index: number }) {
  let angle = 0;
  return <section className="pieCard">
    <div className="pieHeading"><span>ВОПРОС {index + 1}</span><h3>{chart.question}</h3><p>Ответили: <strong>{chart.answered}</strong> человек</p></div>
    {chart.answered === 0 ? <div className="pieEmpty"><div className="pieEmptyCircle" aria-hidden="true" /><p>Пока нет ответов из Meta</p></div> : <div className="pieContent">
      <svg className="pieGraphic" viewBox="0 0 220 220" role="img" aria-label={`${chart.question} Ответили ${chart.answered} человек`}>
        {chart.options.map((option, colorIndex) => {
          if (!option.count) return null;
          const sweep = 360 * option.count / chart.answered;
          const start = angle;
          angle += sweep;
          if (sweep >= 359.999) return <circle key={option.label} cx="110" cy="110" r="88" fill={pieColors[colorIndex % pieColors.length]} />;
          const [x1, y1] = piePoint(start);
          const [x2, y2] = piePoint(angle);
          return <path key={option.label} d={`M 110 110 L ${x1} ${y1} A 88 88 0 ${sweep > 180 ? 1 : 0} 1 ${x2} ${y2} Z`} fill={pieColors[colorIndex % pieColors.length]} stroke="#fff" strokeWidth="2" />;
        })}
      </svg>
      <ul className="pieLegend">{chart.options.map((option, colorIndex) => <li key={option.label}><span className="legendColor" style={{ backgroundColor: pieColors[colorIndex % pieColors.length] }} /><span className="legendLabel">{option.label}</span><strong>{option.count} · {option.percent.toLocaleString("ru-RU")}%</strong></li>)}</ul>
    </div>}
  </section>;
}

export default function App() {
  const [context, setContext] = useState(() => ({ id: sessionStorage.getItem("crm_business") || "", revision: 0, panel: false }));
  function switchBusiness(id: string, panel = false) {
    if (id) sessionStorage.setItem("crm_business", id);
    else sessionStorage.removeItem("crm_business");
    setContext(current => ({ id, revision: current.revision + 1, panel }));
  }
  return <Workspace key={context.revision} organizationId={context.id} initialPanel={context.panel} switchBusiness={switchBusiness} />;
}

function Workspace({ organizationId, initialPanel, switchBusiness }: { organizationId: string; initialPanel: boolean; switchBusiness: (id: string, panel?: boolean) => void }) {
  function api<T>(path: string, activeToken: string | null, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    if (organizationId) headers.set("X-Organization-ID", organizationId);
    return apiRequest<T>(path, activeToken, { ...init, headers });
  }
  const [token, setToken] = useState<string | null>(() => sessionStorage.getItem("crm_token"));
  const [user, setUser] = useState<User | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [leads, setLeads] = useState<Lead[]>([]);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [analyticsError, setAnalyticsError] = useState("");
  const [metaConnected, setMetaConnected] = useState(false);
  const [audienceReadiness, setAudienceReadiness] = useState<AudienceReadiness | null>(null);
  const [view, setView] = useState<View>(initialPanel ? "businesses" : "leads");
  const [showArchive, setShowArchive] = useState(false);
  const [search, setSearch] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [budget, setBudget] = useState("");
  const [deleteArmed, setDeleteArmed] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState<LeadForm>(freshForm);
  const [selected, setSelected] = useState<Lead | null>(null);
  const [draftNote, setDraftNote] = useState("");
  const [comments, setComments] = useState<LeadComment[]>([]);
  const [draftComment, setDraftComment] = useState("");
  const [draggingLeadId, setDraggingLeadId] = useState<string | null>(null);
  const [dropStage, setDropStage] = useState<BoardStage | null>(null);

  const project = projects.find(item => item.id === projectId);
  const projectLeads = leads.filter(item => item.project_id === projectId);
  const activeLeads = projectLeads.filter(item => stageOf(item.status) !== "closed");
  const visibleLeads = (showArchive ? projectLeads.filter(item => stageOf(item.status) === "closed") : activeLeads)
    .filter(item => `${item.name} ${item.phone} ${item.company_name} ${item.city}`.toLowerCase().includes(search.toLowerCase().trim()));
  const counts = {
    new: activeLeads.filter(item => stageOf(item.status) === "new").length,
    work: activeLeads.filter(item => stageOf(item.status) === "work").length,
    qualified: activeLeads.filter(item => stageOf(item.status) === "qualified").length,
    won: activeLeads.filter(item => stageOf(item.status) === "won").length,
  };

  function signOut() {
    sessionStorage.removeItem("crm_token");
    setToken(null); setUser(null); setProjects([]); setLeads([]); setAnalytics(null);
    switchBusiness("");
  }

  async function loadWorkspace(activeToken: string) {
    const [profile, list] = await Promise.all([
      api<User>("/auth/me", activeToken),
      api<Project[]>("/projects", activeToken),
    ]);
    setUser(profile);
    void api<{ connected: boolean }>("/integrations/meta/status", activeToken).then(result => setMetaConnected(result.connected)).catch(() => setMetaConnected(false));
    setProjects(list);
    setProjectId(current => list.some(item => item.id === current) ? current : (list[0]?.id || ""));
  }

  useEffect(() => {
    if (!token) return;
    loadWorkspace(token).catch(() => { signOut(); setNotice("Войдите ещё раз"); });
  }, [token]);

  useEffect(() => {
    if (!token || !projectId) { setLeads([]); return; }
    let active = true;
    api<Lead[]>(`/leads?project_id=${encodeURIComponent(projectId)}`, token)
      .then(result => { if (active) setLeads(result); })
      .catch(error => { if (active) setNotice(error.message); });
    return () => { active = false; };
  }, [token, projectId]);

  useEffect(() => {
    if (!token || !projectId) { setAudienceReadiness(null); return; }
    let active = true;
    api<AudienceReadiness>(`/integrations/meta/audience-readiness?project_id=${encodeURIComponent(projectId)}`, token)
      .then(result => { if (active) setAudienceReadiness(result); })
      .catch(() => { if (active) setAudienceReadiness(null); });
    return () => { active = false; };
  }, [token, projectId, leads]);

  useEffect(() => {
    if (!token || !projectId || view !== "analytics") return;
    let active = true;
    setAnalytics(null);
    setAnalyticsError("");
    api<Analytics>(`/analytics?project_id=${encodeURIComponent(projectId)}`, token)
      .then(result => { if (active) setAnalytics(result); })
      .catch(error => { if (active) setAnalyticsError(error.message); });
    return () => { active = false; };
  }, [token, projectId, view, leads]);

  useEffect(() => {
    if (!token || !projectId || !metaConnected || view !== "leads") return;
    let active = true;
    const pull = async () => {
      try {
        await api<MetaSync>(`/integrations/meta/sync?project_id=${encodeURIComponent(projectId)}`, token, { method: "POST" });
        const updated = await api<Lead[]>(`/leads?project_id=${encodeURIComponent(projectId)}`, token);
        if (active) setLeads(updated);
      } catch { /* Manual refresh displays connection errors. */ }
    };
    void pull();
    const timer = window.setInterval(() => void pull(), 60000);
    return () => { active = false; window.clearInterval(timer); };
  }, [token, projectId, metaConnected, view]);

  useEffect(() => { setBudget(project?.minimum_ad_budget.toString() || ""); }, [project?.id, project?.minimum_ad_budget]);

  async function run(action: () => Promise<void>) {
    setBusy(true); setNotice("");
    try { await action(); }
    catch (error) { setNotice(error instanceof Error ? error.message : "Произошла ошибка"); }
    finally { setBusy(false); }
  }

  async function refreshLeads() {
    if (token && projectId) setLeads(await api<Lead[]>(`/leads?project_id=${encodeURIComponent(projectId)}`, token));
  }

  async function changeStatus(lead: Lead, status: string) {
    if (!token || lead.status === status) return;
    await run(async () => {
      const updated = await api<Lead>(`/leads/${lead.id}/status`, token, {
        method: "PATCH", body: JSON.stringify({ status }),
      });
      setLeads(current => current.map(item => item.id === updated.id ? updated : item));
      setSelected(current => current?.id === updated.id ? updated : current);
      setNotice(status === "QUALIFIED" ? qualityNotice(updated) : "Заявка обновлена");
    });
  }

  async function saveNote() {
    if (!token || !selected) return;
    await run(async () => {
      const updated = await api<Lead>(`/leads/${selected.id}/notes`, token, {
        method: "PATCH", body: JSON.stringify({ notes: draftNote }),
      });
      setLeads(current => current.map(item => item.id === updated.id ? updated : item));
      setSelected(updated);
      setNotice("Пометка сохранена");
    });
  }

  async function openWhatsApp(lead: Lead) {
    if (!token) return;
    const tab = window.open("", "_blank");
    await run(async () => {
      try {
        const result = await api<{ url: string }>(`/leads/${lead.id}/whatsapp-click`, token, { method: "POST" });
        if (tab) tab.location.href = result.url;
        else window.location.href = result.url;
        setNotice("WhatsApp открыт. После сообщения отметьте результат разговора.");
      } catch (error) {
        tab?.close();
        throw error;
      }
    });
  }

  function openLead(lead: Lead) {
    setSelected(lead);
    setDraftNote(lead.notes);
    setComments([]); setDraftComment("");
    if (token) {
      void api<LeadComment[]>(`/leads/${lead.id}/comments`, token).then(setComments).catch(error => setNotice(error.message));
    }
  }

  async function addComment() {
    if (!token || !selected || !draftComment.trim()) return;
    await run(async () => {
      const created = await api<LeadComment>(`/leads/${selected.id}/comments`, token, { method: "POST", body: JSON.stringify({ text: draftComment.trim() }) });
      setComments(current => [...current, created]); setDraftComment(""); setNotice("Комментарий добавлен");
    });
  }

  async function qualifyLead() {
    if (!token || !selected) return;
    await run(async () => {
      const feedback = await api<MetaFeedbackResult>(`/leads/${selected.id}/qualify`, token, { method: "POST" });
      const updatedLeads = await api<Lead[]>(`/leads?project_id=${encodeURIComponent(selected.project_id)}`, token);
      const updated = updatedLeads.find(item => item.id === selected.id) || { ...selected, meta_feedback_status: feedback.status };
      setLeads(updatedLeads);
      setSelected(updated);
      setNotice(qualityNotice(updated));
    });
  }

  async function syncMetaNow() {
    if (!token || !projectId) return;
    await run(async () => {
      const result = await api<MetaSync>(`/integrations/meta/sync?project_id=${encodeURIComponent(projectId)}`, token, { method: "POST" });
      await refreshLeads();
      setNotice(result.imported ? `Из Meta добавлено заявок: ${result.imported}` : "Новых заявок из Meta пока нет");
    });
  }

  if (!token || !user) return <main className="loginScreen">
    <form className="loginBox" onSubmit={event => { event.preventDefault(); void run(async () => {
      const result = await api<{ access_token: string }>("/auth/login", null, {
        method: "POST", body: JSON.stringify({ username, password }),
      });
      sessionStorage.setItem("crm_token", result.access_token);
      setToken(result.access_token);
    }); }}>
      <div className="brandMark">A</div>
      <span className="eyebrow">РЕКЛАМА • CRM • API</span>
      <h1>ADS.KZ CRM</h1>
      <p>Заявки и результаты рекламы в одном месте. Введите логин и пароль, чтобы войти.</p>
      <label>Логин<input value={username} onChange={event => setUsername(event.target.value)} required autoFocus autoComplete="username" placeholder="Ваш логин" /></label>
      <label>Пароль<div className="passwordField"><input type={showPassword ? "text" : "password"} value={password} onChange={event => setPassword(event.target.value)} required autoComplete="current-password" spellCheck={false} placeholder="Введите пароль" /><button type="button" onClick={() => setShowPassword(value => !value)}>{showPassword ? "Скрыть" : "Показать"}</button></div></label>
      <p className="loginHint">Если видите точки вместо букв — пароль вводится. Нажмите «Показать», чтобы проверить.</p>
      {notice && <p className="message error" role="alert">{notice}</p>}
      <button className="primary wide" disabled={busy}>Войти</button>
    </form>
  </main>;

  const renderCard = (lead: Lead) => <article className={`leadCard${draggingLeadId === lead.id ? " isDragging" : ""}`} key={lead.id}
    draggable={!showArchive && !busy}
    onDragStart={event => {
      if (showArchive || busy) { event.preventDefault(); return; }
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("application/x-ads-kz-lead-id", lead.id);
      event.dataTransfer.setData("text/plain", lead.id);
      setDraggingLeadId(lead.id);
    }}
    onDragEnd={() => { setDraggingLeadId(null); setDropStage(null); }}>
    <div className="cardTop"><span className="serviceTag">{serviceNames[lead.requested_service] || lead.requested_service}</span><span className="cardMeta"><span className="cardDate">{new Date(lead.created_at).toLocaleDateString("ru-RU")}</span>{!showArchive && <span className="dragGrip" title="Перетащить карточку" aria-hidden="true">⋮⋮</span>}</span></div>
    <div className="cardSource">{lead.source === "META" ? `● Meta${lead.meta_ad_name ? ` · ${lead.meta_ad_name}` : ""}` : "○ Добавлена вручную"}</div>
    <button className="cardTitle" onClick={() => openLead(lead)}>{lead.name}</button>
    <p>{lead.company_name || lead.city || lead.phone}</p>
    <div className="cardBudget">{formatBudget(lead.monthly_ad_budget)}</div>
    {lead.low_budget && <div className="attentionTag">Бюджет ниже минимума</div>}
    {lead.status === "QUALIFIED" && <div className="feedbackBadge sent">★ Качественная заявка</div>}
    {lead.notes && <div className="cardNote"><strong>Пометка</strong><span>{lead.notes}</span></div>}
    {lead.form_answers?.length > 0 && <div className="cardAnswers">{lead.form_answers.slice(0, 2).map((answer, index) => <div key={index}><b>{answer.question}</b><span>{answer.answers.join(", ") || "—"}</span></div>)}{lead.form_answers.length > 2 && <small>Все ответы в карточке →</small>}</div>}
    <div className="cardActions">
      {stageOf(lead.status) === "new" && <button onClick={() => void changeStatus(lead, "CONTACTED")}>В работу →</button>}
      {stageOf(lead.status) === "work" && <button onClick={() => openLead(lead)}>Проверить качество →</button>}
      {stageOf(lead.status) === "qualified" && <button onClick={() => void changeStatus(lead, "WON")}>Продажа ✓</button>}
      {stageOf(lead.status) !== "won" && stageOf(lead.status) !== "closed" && <button className="quietAction" onClick={() => openLead(lead)}>Подробнее</button>}
      {(stageOf(lead.status) === "won" || stageOf(lead.status) === "closed") && <button className="quietAction" onClick={() => openLead(lead)}>Открыть карточку</button>}
    </div>
  </article>;

  return <div className="appShell">
    <aside className="sidebar">
      <div className="headerIdentity"><div className="brandMark small">A</div><div><p className="sidebarCaption">РЕКЛАМА • CRM • API</p><div className="brand">ADS<span>.</span>KZ <small>CRM</small></div></div></div>
      <nav aria-label="Основное меню">
        {user.role === "PLATFORM_ADMIN" && <button className={view === "businesses" ? "selected" : ""} onClick={() => switchBusiness("", true)}><span>▣</span> Бизнесы</button>}
        <button className={view === "leads" ? "selected" : ""} onClick={() => setView("leads")}><span>▦</span> Заявки</button>
        <button className={view === "analytics" ? "selected" : ""} onClick={() => setView("analytics")}><span>▤</span> Аналитика</button>
        <button className={view === "settings" ? "selected" : ""} onClick={() => setView("settings")}><span>⚙</span> Настройки</button>
      </nav>
      <div className="sidebarFoot"><span>{user.full_name}</span><button onClick={signOut}>Выйти</button></div>
    </aside>

    <main className="mainArea">
      <header className="topbar"><strong>{view === "leads" ? "Заявки" : view === "analytics" ? "Аналитика" : view === "businesses" ? "Бизнесы" : "Настройки"}</strong><span>{user.organization_name}</span></header>
      {organizationId && user.role === "PLATFORM_ADMIN" && <div className="sourceNote">Кабинет: <strong>{user.organization_name}</strong><button className="secondary" onClick={() => switchBusiness("", true)}>← Все бизнесы</button></div>}
      {view === "businesses" && user.role === "PLATFORM_ADMIN" && <BusinessPanel token={token} request={api} onOpen={id => switchBusiness(id)} ownBusinessId={user.home_organization_id} />}
      {notice && <div className="message" role="status">{notice}<button onClick={() => setNotice("")} aria-label="Закрыть сообщение">×</button></div>}

      {view === "leads" && <div className="page">
        <div className="pageHead"><div><span className="eyebrow">РАБОЧИЙ СТОЛ</span><h1>Ваши заявки</h1><p>Здесь видно, кому нужно ответить и что делать дальше.</p></div><div className="pageActions"><button className="secondary" onClick={() => void syncMetaNow()} disabled={!projectId || !metaConnected || busy}>Обновить из Meta</button><button className="primary" onClick={() => setCreateOpen(true)} disabled={!projectId}>+ Добавить заявку</button></div></div>
        <div className="summaryGrid">
          <div><strong>{projectLeads.length}</strong><span>Всего заявок</span></div>
          <div><strong>{counts.work}</strong><span>В работе</span></div>
          <div><strong>{counts.qualified}</strong><span>Подходят</span></div>
          <div><strong>{counts.won}</strong><span>Продаж</span></div>
        </div>
        <div className="sourceNote"><span>○</span> {metaConnected ? "Доступ к форме Meta настроен. Заявки обновляются при открытии CRM и затем каждую минуту." : "Для заявок из Meta нужны ID страницы и токен доступа к лидам. Пока можно добавлять заявки вручную."}</div>
        {metaConnected && audienceReadiness && <div className="sourceNote"><span>★</span> Качественных заявок для похожей аудитории: {audienceReadiness.qualified} из {audienceReadiness.threshold}. {audienceReadiness.ready ? "Порог CRM достигнут. Для создания аудитории нужен доступ к рекламному аккаунту Meta." : "Отметки качества отправляются в Meta по мере подтверждения; похожая аудитория пока не создаётся."}</div>}
        {!projectId ? <section className="emptyState"><h2>Начнём работу</h2><p>Создадим пространство для ваших заявок.</p><button className="primary" onClick={() => void run(async () => {
          await api("/projects", token, { method: "POST", body: JSON.stringify({ name: user.organization_name }) });
          await loadWorkspace(token);
          setNotice("Рабочее пространство готово");
        })}>Начать</button></section> : <>
          {!showArchive && <div className="boardIntro">{projectLeads.length === 0 ? "Канбан доска готова. Когда появится заявка, её карточка окажется в колонке «Новые»." : "Перетащите карточку в другую колонку, чтобы изменить этап заявки."}</div>}
            <div className="listTools"><div className="segment"><button className={!showArchive ? "active" : ""} onClick={() => setShowArchive(false)}>Текущие</button><button className={showArchive ? "active" : ""} onClick={() => setShowArchive(true)}>Архив</button></div><input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Поиск по имени, телефону, городу" aria-label="Поиск заявок" /></div>
            {showArchive ? <section className="archiveList"><h2>Закрытые заявки</h2>{visibleLeads.length ? visibleLeads.map(renderCard) : <p className="subtle">Закрытых заявок нет</p>}</section> : <div className="simpleBoard">{boardColumns.map(column => <section
              className={`stageColumn ${column.id}${dropStage === column.id ? " dropTarget" : ""}`} key={column.id}
              onDragOver={event => {
                if (!draggingLeadId || busy) return;
                event.preventDefault();
                event.dataTransfer.dropEffect = "move";
                if (dropStage !== column.id) setDropStage(column.id);
              }}
              onDragLeave={event => {
                if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                  setDropStage(current => current === column.id ? null : current);
                }
              }}
              onDrop={event => {
                event.preventDefault();
                const leadId = event.dataTransfer.getData("application/x-ads-kz-lead-id") || event.dataTransfer.getData("text/plain");
                setDraggingLeadId(null); setDropStage(null);
                if (!draggingLeadId || draggingLeadId !== leadId) return;
                const lead = leads.find(item => item.id === leadId);
                if (lead && stageOf(lead.status) !== column.id) void changeStatus(lead, column.status);
              }}>
              <div className="stageHead"><div><h2>{column.title}</h2><span>{column.hint}</span></div><b>{visibleLeads.filter(item => stageOf(item.status) === column.id).length}</b></div>
              <div className="stageBody">{visibleLeads.filter(item => stageOf(item.status) === column.id).length ? visibleLeads.filter(item => stageOf(item.status) === column.id).map(renderCard) : <p className="stageEmpty">Здесь пока пусто</p>}</div>
            </section>)}</div>}
        </>}
      </div>}

      {view === "analytics" && <div className="page analyticsPage"><div className="pageHead"><div><span className="eyebrow">ОТВЕТЫ ИЗ META</span><h1>Аналитика</h1><p>Ответы клиентов на вопросы рекламной формы.</p></div></div>
        {!projectId ? <section className="emptyState"><p>Выберите проект, чтобы открыть отчёт.</p></section>
          : analyticsError ? <section className="emptyState" role="alert"><p>{analyticsError}</p></section>
          : !analytics ? <section className="emptyState"><p>Загружаем отчёт…</p></section> : <>
          <div className="analyticsIntro"><h2>Ответы клиентов из Meta</h2><p>Каждый процент рассчитан среди людей, ответивших на соответствующий вопрос. Ручные заявки в диаграммы не входят.</p></div>
          {analytics.questions.length ? <div className="pieGrid">{analytics.questions.map((chart, index) => <PieQuestion key={chart.question} chart={chart} index={index} />)}</div>
            : <section className="emptyState"><p>В заявках Meta пока нет ответов на вопросы формы.</p></section>}
        </>}
      </div>}

      {view === "settings" && <div className="page narrowPage"><div className="pageHead"><div><span className="eyebrow">ТОЛЬКО НУЖНОЕ</span><h1>Настройки</h1><p>Бюджет для отбора заявок и ваш пароль.</p></div></div>
        {projects.length > 1 && <label className="workspaceSelect">Рабочее пространство<select value={projectId} onChange={event => setProjectId(event.target.value)}>{projects.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
        <div className="settingGrid"><form className="settingCard" onSubmit={event => { event.preventDefault(); if (!projectId) return; void run(async () => {
          const updated = await api<Project>(`/projects/${projectId}`, token, { method: "PATCH", body: JSON.stringify({ minimum_ad_budget: Number(budget) }) });
          setProjects(current => current.map(item => item.id === updated.id ? updated : item));
          await refreshLeads();
          setNotice("Минимальный бюджет сохранён");
        }); }}><h2>Минимальный бюджет</h2><p>Заявки по таргету ниже этой суммы будут отмечены для проверки. Заявки только на CRM или API не оцениваются по рекламному бюджету.</p><label>Рекламный бюджет в месяц, ₸<input type="number" min="0" value={budget} onChange={event => setBudget(event.target.value)} required /></label><button className="primary" disabled={busy || !projectId}>Сохранить бюджет</button></form>
          <form className="settingCard" onSubmit={event => { event.preventDefault(); void run(async () => {
            await api("/auth/password", token, { method: "POST", body: JSON.stringify({ current_password: oldPassword, new_password: newPassword }) });
            signOut();
          }); }}><h2>Пароль для входа</h2><p>Для входа нужны ваш логин и пароль. После изменения пароля войдите заново.</p><label>Сейчас<input type="password" value={oldPassword} onChange={event => setOldPassword(event.target.value)} required autoComplete="current-password" /></label><label>Новый пароль<input type="password" value={newPassword} onChange={event => setNewPassword(event.target.value)} required autoComplete="new-password" /></label><button className="primary" disabled={busy}>Изменить пароль</button></form></div>
        <div className="integrationCard"><div><strong>Получение заявок Meta</strong><p>{metaConnected ? "ID страницы и токен сохранены. Откройте «Заявки» и нажмите «Обновить из Meta», чтобы проверить доступ." : "Нужны ID Facebook-страницы и токен с доступом к её лидам. Пиксель не требуется."}</p></div><span>{metaConnected ? "Данные добавлены" : "Не подключено"}</span></div>
        {project && <details className="advanced"><summary>Дополнительные действия</summary><p>Если это рабочее пространство больше не нужно, его можно скрыть. Данные сохранятся для восстановления.</p>{deleteArmed ? <div className="deleteConfirm"><strong>Удалить «{project.name}» из CRM?</strong><button className="danger" onClick={() => void run(async () => { await api(`/projects/${project.id}`, token, { method: "DELETE" }); await loadWorkspace(token!); setDeleteArmed(false); setView("leads"); setNotice("Рабочее пространство удалено из списка"); })}>Да, удалить</button><button className="secondary" onClick={() => setDeleteArmed(false)}>Отмена</button></div> : <button className="danger" onClick={() => setDeleteArmed(true)}>Удалить проект</button>}</details>}
      </div>}
    </main>

    {createOpen && <div className="overlay" onMouseDown={event => { if (event.target === event.currentTarget) setCreateOpen(false); }}><form className="dialog" onSubmit={event => { event.preventDefault(); if (!projectId) return; void run(async () => {
      await api("/leads", token, { method: "POST", body: JSON.stringify({ ...form, project_id: projectId, monthly_ad_budget: form.monthly_ad_budget ? Number(form.monthly_ad_budget) : null }) });
      await refreshLeads(); setShowArchive(false); setForm(freshForm); setCreateOpen(false); setNotice("Заявка добавлена");
    }); }}><div className="dialogTop"><div><span className="eyebrow">НОВАЯ ЗАЯВКА</span><h2>Добавить клиента</h2></div><button type="button" className="closeButton" onClick={() => setCreateOpen(false)}>×</button></div>
      <div className="formGrid"><label>Имя *<input value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} required minLength={2} /></label><label>Телефон *<input type="tel" value={form.phone} onChange={event => setForm({ ...form, phone: event.target.value })} required minLength={5} /></label><label>Почта (если есть)<input type="email" value={form.email} onChange={event => setForm({ ...form, email: event.target.value })} /></label><label>Какая услуга нужна<select value={form.requested_service} onChange={event => setForm({ ...form, requested_service: event.target.value })}>{Object.entries(serviceNames).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>Рекламный бюджет в месяц, ₸<input type="number" min="0" value={form.monthly_ad_budget} onChange={event => setForm({ ...form, monthly_ad_budget: event.target.value })} /></label><label>Компания<input value={form.company_name} onChange={event => setForm({ ...form, company_name: event.target.value })} /></label><label>Город<input value={form.city} onChange={event => setForm({ ...form, city: event.target.value })} /></label><label className="full">Пометка<textarea rows={3} value={form.notes} onChange={event => setForm({ ...form, notes: event.target.value })} placeholder="Что важно знать о клиенте?" /></label></div>
      {notice && <div className="message error" role="alert">{notice}</div>}
      <div className="dialogActions"><button type="button" className="secondary" onClick={() => setCreateOpen(false)}>Отмена</button><button className="primary" disabled={busy}>Сохранить заявку</button></div>
    </form></div>}

    {selected && <div className="drawerOverlay" onMouseDown={event => { if (event.target === event.currentTarget) setSelected(null); }}><aside className="drawer"><div className="drawerTop"><span>Заявка</span><button className="closeButton" onClick={() => setSelected(null)}>×</button></div><div className="drawerBody"><span className="eyebrow">{serviceNames[selected.requested_service]}</span><h2>{selected.name}</h2>{notice && <div className="message drawerMessage" role="status">{notice}</div>}{selected.low_budget && <div className="attentionTag big">Рекламный бюджет ниже минимума. Проверьте заявку лично.</div>}
      <div className="contactActions"><a href={`tel:${selected.phone}`}>Позвонить</a><button onClick={() => void openWhatsApp(selected)}>Открыть WhatsApp</button></div>
      <dl><div><dt>Источник</dt><dd>{selected.source === "META" ? "Форма Meta" : "Вручную"}</dd></div>{selected.meta_ad_name && <div><dt>Объявление</dt><dd>{selected.meta_ad_name}</dd></div>}<div><dt>Телефон</dt><dd>{selected.phone}</dd></div><div><dt>Почта (если есть)</dt><dd>{selected.email || "—"}</dd></div><div><dt>Компания</dt><dd>{selected.company_name || "—"}</dd></div><div><dt>Город</dt><dd>{selected.city || "—"}</dd></div><div><dt>Услуга</dt><dd>{serviceNames[selected.requested_service] || selected.requested_service}</dd></div><div><dt>Рекламный бюджет</dt><dd>{formatBudget(selected.monthly_ad_budget)}</dd></div><div><dt>Дата заявки</dt><dd>{new Date(selected.created_at).toLocaleString("ru-RU")}</dd></div>{selected.meta_lead_id && <div><dt>ID заявки Meta</dt><dd>{selected.meta_lead_id}</dd></div>}{selected.meta_form_id && <div><dt>ID формы Meta</dt><dd>{selected.meta_form_id}</dd></div>}{selected.meta_ad_id && <div><dt>ID объявления Meta</dt><dd>{selected.meta_ad_id}</dd></div>}</dl>
      <section className="drawerSection"><h3>Ответы на вопросы формы</h3>{selected.form_answers?.length ? selected.form_answers.map((answer, index) => <div className="answerRow" key={index}><strong>{answer.question}</strong><span>{answer.answers.join(", ") || "—"}</span></div>) : <p className="subtle">У этой заявки нет ответов из формы Meta.</p>}</section>
      <label className="noteEditor">Ваша пометка<textarea rows={4} value={draftNote} onChange={event => setDraftNote(event.target.value)} placeholder="Напишите, что важно помнить" /></label><button className="noteButton" disabled={busy || draftNote === selected.notes} onClick={() => void saveNote()}>Сохранить пометку</button>
      <section className="drawerSection"><h3>Комментарии</h3>{comments.length ? comments.map(comment => <div className="comment" key={comment.id}><small>{new Date(comment.created_at).toLocaleString("ru-RU")}</small><p>{comment.text}</p></div>) : <p className="subtle">Комментариев пока нет</p>}<textarea rows={3} value={draftComment} onChange={event => setDraftComment(event.target.value)} placeholder="Напишите комментарий о клиенте" /><button className="secondary" disabled={busy || !draftComment.trim()} onClick={() => void addComment()}>Добавить комментарий</button></section>
      <section className="drawerSection qualityBox"><h3>Качество заявки</h3><p>Отметьте, когда убедитесь, что клиент действительно подходит.</p><button className="primary" disabled={busy || (selected.status === "QUALIFIED" && (selected.source !== "META" || ["SENT", "NOT_ELIGIBLE"].includes(selected.meta_feedback_status || "")))} onClick={() => void qualifyLead()}>{selected.status === "QUALIFIED" ? (selected.source !== "META" || ["SENT", "NOT_ELIGIBLE"].includes(selected.meta_feedback_status || "") ? "★ Качественная заявка" : "Отправить качество в Meta") : "Отметить как качественную"}</button>{selected.status === "QUALIFIED" && <p role="status">{qualityNotice(selected)}</p>}</section>
      <section className="drawerSection"><h3>Переместить на этап</h3><select aria-label="Этап заявки" value={selected.status} onChange={event => void changeStatus(selected, event.target.value)} disabled={busy}><option value="NEW">Новые</option><option value="CONTACTED">В работе</option><option value="QUALIFIED">Подходят</option><option value="MEETING">Назначена встреча</option><option value="PROPOSAL">Отправлено предложение</option><option value="WON">Продажа</option><option value="LOST">Не купил</option><option value="UNQUALIFIED">Не подходит</option></select></section>
      <div className="nextAction"><strong>Быстрые действия</strong>{stageOf(selected.status) === "new" && <button onClick={() => void changeStatus(selected, "CONTACTED")}>Начала работать с заявкой</button>}{stageOf(selected.status) === "work" && <button onClick={() => void qualifyLead()}>Клиент подходит</button>}{stageOf(selected.status) === "qualified" && <button onClick={() => void changeStatus(selected, "WON")}>Получила продажу</button>}{stageOf(selected.status) === "won" && <p>Продажа отмечена ✓</p>}{stageOf(selected.status) === "closed" && <button onClick={() => void changeStatus(selected, "CONTACTED")}>Вернуть в работу</button>}{stageOf(selected.status) !== "closed" && stageOf(selected.status) !== "won" && <button className="secondary" onClick={() => void changeStatus(selected, "UNQUALIFIED")}>Не подошёл</button>}</div>
      <p className="drawerHint">Открытие WhatsApp фиксируется как попытка связи. Отправку сообщения отметьте сами после разговора.</p>
    </div></aside></div>}
  </div>;
}
