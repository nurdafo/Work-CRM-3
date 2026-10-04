import { useEffect, useState } from "react";

type Business = { id: string; name: string; username: string; active: boolean; project_id: string; telegram_pending: number; integrations: Record<string, string | number | boolean> };
type Request = <T>(path: string, token: string | null, init?: RequestInit) => Promise<T>;
const fields = [
  ["meta_page_id", "Facebook Page ID"], ["meta_form_id", "Form ID (необязательно)"],
  ["meta_leads_access_token", "Токен страницы для заявок"],
  ["meta_ad_account_id", "ID рекламного аккаунта"], ["meta_ads_access_token", "Токен рекламного аккаунта"],
  ["meta_dataset_id", "ID набора данных Conversions API"], ["meta_capi_access_token", "Токен Conversions API"],
  ["meta_ad_labels", "Названия объявлений (ID=Название;ID=Название)"],
  ["meta_lookalike_min_leads", "Порог качественных заявок"],
  ["telegram_bot_token", "Токен Telegram-бота"], ["telegram_chat_id", "Telegram chat ID"],
] as const;

export default function BusinessPanel({ token, request, onOpen, ownBusinessId }: { token: string; request: Request; onOpen: (id: string) => void; ownBusinessId: string }) {
  const [businesses, setBusinesses] = useState<Business[]>([]);
  const [editing, setEditing] = useState<Business | null | undefined>(undefined);
  const [values, setValues] = useState<Record<string, string>>({});
  const [clearTokens, setClearTokens] = useState<string[]>([]);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [checks, setChecks] = useState<Record<string, boolean> | null>(null);

  async function reload() { setBusinesses(await request<Business[]>("/admin/businesses", token)); }
  useEffect(() => { let active = true; request<Business[]>("/admin/businesses", token).then(rows => { if (active) setBusinesses(rows); }).catch(error => { if (active) setNotice(error.message); }); return () => { active = false; }; }, [token]);
  async function run(action: () => Promise<void>) {
    setBusy(true); setNotice("");
    try { await action(); } catch (error) { setNotice(error instanceof Error ? error.message : "Не удалось сохранить"); }
    finally { setBusy(false); }
  }
  function edit(business: Business | null) {
    setEditing(business); setChecks(null); setNotice(""); setClearTokens([]);
    const next: Record<string, string> = { name: business?.name || "", username: business?.username || "", password: "" };
    for (const [key] of fields) next[key] = key.endsWith("token") ? "" : String(business?.integrations[key] ?? (key === "meta_lookalike_min_leads" ? 20 : ""));
    setValues(next);
  }
  function input(key: string, value: string) { setValues(current => ({ ...current, [key]: value })); setChecks(null); }

  return <div className="page">
    <div className="pageHead"><div><span className="eyebrow">УПРАВЛЕНИЕ ПЛАТФОРМОЙ</span><h1>Бизнесы</h1><p>Отдельный кабинет, заявки и подключения для каждого бизнеса.</p></div><button className="primary" disabled={busy} onClick={() => edit(null)}>+ Добавить бизнес</button></div>
    {notice && <div className="message" role="status">{notice}</div>}
    <div className="businessList">{businesses.map(business => <section className="businessCard" key={business.id}>
      <div><h2>{business.name}</h2><p>{business.username} · {business.active ? "Кабинет активен" : "Доступ отключён"}</p><small>Meta: {business.integrations.meta_leads_access_token_configured && business.integrations.meta_page_id ? "настроена" : "не настроена"} · Telegram: {business.integrations.telegram_bot_token_configured && business.integrations.telegram_chat_id ? "настроен" : "не настроен"} · В очереди: {business.telegram_pending}</small></div>
      <div className="pageActions"><button className="secondary" disabled={busy} onClick={() => edit(business)}>Настроить</button><button className="primary" disabled={!business.active || busy} onClick={() => onOpen(business.id)}>Открыть CRM →</button></div>
    </section>)}</div>
    {editing !== undefined && <section className="businessEditor">
      <h2>{editing ? `Настройки: ${editing.name}` : "Новый бизнес"}</h2>
      <form onSubmit={event => { event.preventDefault(); void run(async () => {
        const integrations: Record<string, string | number> = {};
        for (const [key] of fields) {
          if (key.endsWith("token") && !values[key] && !clearTokens.includes(key)) continue;
          integrations[key] = clearTokens.includes(key) ? "" : key === "meta_lookalike_min_leads" ? Number(values[key]) : values[key].trim();
        }
        const body = { name: values.name, username: values.username, ...(values.password ? { password: values.password } : {}), integrations };
        const saved = await request<Business>(editing ? `/admin/businesses/${editing.id}` : "/admin/businesses", token, { method: editing ? "PATCH" : "POST", body: JSON.stringify(body) });
        edit(saved); await reload(); setNotice("Кабинет сохранён. Логин и пароль можно передать владельцу бизнеса.");
      }); }}>
        <div className="businessFields">
          <label>Название бизнеса<input required minLength={2} maxLength={180} value={values.name} onChange={e => input("name", e.target.value)} /></label>
          <label>Логин владельца<input required minLength={2} maxLength={80} pattern="[a-zA-Z0-9_.\-]+" autoComplete="off" value={values.username} onChange={e => input("username", e.target.value)} /></label>
          <label>{editing ? "Новый пароль (оставьте пустым, чтобы сохранить текущий)" : "Пароль владельца"}<input type="password" required={!editing} minLength={8} maxLength={256} autoComplete="new-password" value={values.password} onChange={e => input("password", e.target.value)} /></label>
        </div>
        <h3>Подключения</h3><p className="subtle">Подключения можно заполнить позже. Сохранённые токены не отображаются; пустое поле сохраняет текущий токен.</p>
        <div className="businessFields">{fields.map(([key, label]) => <label key={key}>{label}
          <input type={key.endsWith("token") ? "password" : key === "meta_lookalike_min_leads" ? "number" : "text"} min={key === "meta_lookalike_min_leads" ? 1 : undefined} required={key === "meta_lookalike_min_leads"} autoComplete="off" value={values[key]} disabled={clearTokens.includes(key)} onChange={e => input(key, e.target.value)} placeholder={editing?.integrations[`${key}_configured`] ? "Токен сохранён" : ""} />
          {key.endsWith("token") && !!editing?.integrations[`${key}_configured`] && <span className="tokenClear"><input type="checkbox" checked={clearTokens.includes(key)} onChange={e => setClearTokens(current => e.target.checked ? [...current, key] : current.filter(item => item !== key))} />Удалить сохранённый токен</span>}
        </label>)}</div>
        <div className="pageActions"><button className="primary" disabled={busy}>Сохранить</button><button className="secondary" type="button" disabled={busy} onClick={() => setEditing(undefined)}>Закрыть</button></div>
      </form>
      {editing && <div className="businessChecks"><p className="subtle">Проверка и тест используют последние сохранённые настройки.</p><div className="pageActions">
        <button className="secondary" disabled={busy} onClick={() => void run(async () => setChecks(await request<Record<string, boolean>>(`/admin/businesses/${editing.id}/check`, token, { method: "POST" })))}>Проверить подключения</button>
        <button className="secondary" disabled={busy || !editing.active} onClick={() => void run(async () => { await request(`/admin/businesses/${editing.id}/telegram-test`, token, { method: "POST" }); setNotice("Тестовое сообщение отправлено в Telegram"); })}>Отправить тест в Telegram</button>
        {editing.id !== ownBusinessId && <button className="secondary" disabled={busy} onClick={() => void run(async () => {
          const saved = await request<Business>(`/admin/businesses/${editing.id}`, token, { method: "PATCH", body: JSON.stringify({ active: !editing.active }) });
          edit(saved); await reload(); setNotice(saved.active ? "Доступ включён" : "Доступ отключён, текущие сеансы завершены");
        })}>{editing.active ? "Отключить доступ" : "Включить доступ"}</button>}
      </div>{checks && <p role="status">Страница Meta: {checks.meta_page ? "доступ подтверждён" : "не подключена или нет доступа"} · Рекламный аккаунт: {checks.meta_ads ? "доступ подтверждён" : "не подключён или нет доступа"} · Telegram-бот: {checks.telegram ? "токен действителен" : "не подключён или нет доступа"}</p>}</div>}
    </section>}
  </div>;
}
