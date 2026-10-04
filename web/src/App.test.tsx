import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import App from "./App";

beforeEach(() => { sessionStorage.clear(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function mockServer(role = "PLATFORM_ADMIN") {
  let resolveLateComments: ((response: Response) => void) | undefined;
  const calls: { path: string; org: string | null; body?: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const path = new URL(url).pathname;
    const org = new Headers(init.headers).get("X-Organization-ID");
    const name = org === "b" ? "Business B" : "ADS.KZ";
    calls.push({ path, org, body: init.body ? JSON.parse(String(init.body)) : undefined });
    const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
    if (path === "/auth/login") return json({ access_token: "test-token" });
    if (path === "/auth/me") return json({ id: "owner", full_name: "Owner", role, organization_name: name, organization_id: org || "home", home_organization_id: "home" });
    if (path === "/projects") return json([{ id: org ? "project-b" : "project-home", name, minimum_ad_budget: 100 }]);
    if (path === "/integrations/meta/status") return json({ connected: false });
    if (path === "/integrations/meta/audience-readiness") return json({ qualified: 0, threshold: 20, ready: false });
    if (path === "/admin/businesses") return json([{ id: "b", name: "Business B", username: "owner-b", active: true, integrations: {}, telegram_pending: 0 }]);
    if (path.endsWith("/comments")) return await new Promise<Response>(resolve => { resolveLateComments = resolve; });
    if (path === "/leads") return json(org ? [] : [{ id: "lead-home", project_id: "project-home", name: "Private client", phone: "77012345678", source: "MANUAL", requested_service: "CRM", status: "NEW", monthly_ad_budget: null, notes: "Private note", form_answers: [], created_at: "2026-10-02T00:00:00Z" }]);
    throw new Error(`Unexpected request ${path}`);
  }));
  return { calls, resolveComments: () => resolveLateComments?.(new Response(JSON.stringify([{ id: "comment", text: "Old private comment", created_at: "2026-10-02" }]))) };
}

describe("business workspaces", () => {
  it("sends both login and password without changing the login layout", async () => {
    const { calls } = mockServer("ADMIN");
    render(<App />);
    fireEvent.change(screen.getByLabelText("Логин"), { target: { value: "owner-b" } });
    fireEvent.change(screen.getByPlaceholderText("Введите пароль"), { target: { value: "client-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Войти" }));
    await screen.findByRole("heading", { name: "Ваши заявки" });
    expect(calls.find(call => call.path === "/auth/login")?.body).toEqual({ username: "owner-b", password: "client-password" });
    expect(screen.queryByRole("button", { name: /Бизнесы/ })).toBeNull();
    for (const title of ["Новые", "В работе", "Подходят", "Продажи"]) expect(screen.getByRole("heading", { name: new RegExp(title) })).toBeTruthy();
  });

  it("remounts the workspace so late responses and open cards cannot leak across businesses", async () => {
    sessionStorage.setItem("crm_token", "test-token");
    const server = mockServer();
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Private client" }));
    await waitFor(() => expect(server.calls.some(call => call.path.endsWith("/comments"))).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: /Бизнесы/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Открыть CRM →" }));
    await screen.findByRole("button", { name: "← Все бизнесы" });
    await act(async () => server.resolveComments());
    expect(screen.queryByText("Private client")).toBeNull();
    expect(screen.queryByText("Old private comment")).toBeNull();
    expect(screen.queryByText("Private note")).toBeNull();
    await waitFor(() => expect(server.calls.some(call => call.path === "/leads" && call.org === "b")).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "← Все бизнесы" }));
    await screen.findByRole("heading", { name: "Бизнесы" });
    expect(sessionStorage.getItem("crm_business")).toBeNull();
  });
});
