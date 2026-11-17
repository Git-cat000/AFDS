import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { SessionProvider, SessionStatus } from "../src/SessionProvider";
import { api, sessionFetch, setSessionCsrf } from "../src/api";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setSessionCsrf(undefined);
});
const user = {
  id: "demo-owner",
  username: "demo-owner",
  display_name: "DEMO owner",
  is_admin: true,
};

function setup(local = false) {
  let expired = false;
  const fetcher = vi.fn(async (url: string) => {
    if (url.endsWith("/auth/session"))
      return new Response(
        JSON.stringify(
          local
            ? { mode: "local", authenticated: true, user }
            : { mode: "team", authenticated: false, user: null },
        ),
      );
    if (url.endsWith("/auth/login"))
      return new Response(
        JSON.stringify({
          mode: "team",
          authenticated: true,
          user,
          csrf_token: "DEMO-csrf-token",
        }),
      );
    if (url.endsWith("/auth/logout"))
      return new Response(JSON.stringify({ authenticated: false }));
    if (expired)
      return new Response(
        JSON.stringify({
          error: { code: "AUTH_REQUIRED", message: "Sign in" },
        }),
        { status: 401 },
      );
    return new Response(JSON.stringify({ id: "demo-project" }));
  });
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <SessionProvider>
        <p>DEMO workspace</p>
        <SessionStatus />
      </SessionProvider>
    </QueryClientProvider>,
  );
  return {
    client,
    fetcher,
    expire: () => {
      expired = true;
    },
  };
}

async function login() {
  fireEvent.change(await screen.findByRole("textbox", { name: "用户名" }), {
    target: { value: "demo-owner" },
  });
  fireEvent.change(screen.getByLabelText("密码"), {
    target: { value: "DEMO private password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("DEMO workspace");
}

it("signs in with a session and includes its CSRF token on JSON and binary writes", async () => {
  const { fetcher } = setup();
  await login();
  await api("/projects", { title: "DEMO" });
  await sessionFetch("/api/v1/projects/demo/images/import", {
    method: "POST",
    body: new Blob(["DEMO"]),
  });
  const calls = fetcher.mock.calls as unknown as [string, RequestInit][];
  for (const [url, options] of calls.filter(([url]) =>
    url.includes("/projects"),
  )) {
    expect(new Headers(options.headers).get("X-CSRF-Token"), url).toBe(
      "DEMO-csrf-token",
    );
  }
});

it("clears private query data and returns to login when the session expires", async () => {
  const { client, expire } = setup();
  await login();
  client.setQueryData(["private-project"], { title: "DEMO private" });
  expire();
  await expect(api("/projects")).rejects.toThrow();
  await screen.findByRole("button", { name: "登录" });
  expect(client.getQueryData(["private-project"])).toBeUndefined();
});

it("logs out with the current session and clears private cache", async () => {
  const { client } = setup();
  await login();
  client.setQueryData(["private-project"], { title: "DEMO private" });
  fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
  await waitFor(() => expect(screen.queryByText("DEMO workspace")).toBeNull());
  expect(client.getQueryData(["private-project"])).toBeUndefined();
});

it("opens the personal edition through its explicit local session", async () => {
  setup(true);
  await screen.findByText("DEMO workspace");
  expect(screen.queryByRole("button", { name: "登录" })).toBeNull();
});

it("keeps the session CSRF token within same-origin API requests", async () => {
  const fetcher = vi.fn(async () => new Response("{}"));
  vi.stubGlobal("fetch", fetcher);
  setSessionCsrf("DEMO-csrf-token");
  await sessionFetch("https://other.example/api/v1/upload", {
    method: "POST",
    body: "DEMO",
  });
  const options = (
    fetcher.mock.calls as unknown as [string, RequestInit][]
  )[0][1];
  expect(new Headers(options.headers).get("X-CSRF-Token")).toBeNull();
});
