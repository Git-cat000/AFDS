import {
  Fragment,
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, setSessionCsrf } from "./api";
import { SessionContext, useSession, type Session } from "./session";
import { useStudio } from "./store";

export function SessionProvider({ children }: { children: ReactNode }) {
  const queries = useQueryClient();
  const [session, setSession] = useState<Session>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const activeRequest = useRef<object | null>(null);
  const mutating = useRef(false);
  const previousUser = useRef<string | null | undefined>(undefined);

  const commit = useCallback(
    (value: Session) => {
      const id = value.user?.id || null;
      if (
        previousUser.current !== id &&
        (previousUser.current !== undefined || value.mode === "team")
      ) {
        queries.clear();
        useStudio.getState().setProject(null);
      }
      previousUser.current = id;
      setSessionCsrf(value.csrf_token);
      setSession(value);
    },
    [queries],
  );

  const refresh = useCallback(async () => {
    if (mutating.current) return;
    const ticket = {};
    activeRequest.current = ticket;
    try {
      const value = await api<Session>("/auth/session");
      if (ticket === activeRequest.current) {
        commit(value);
        setError("");
      }
    } catch (cause) {
      if (ticket === activeRequest.current)
        setError(cause instanceof Error ? cause.message : "无法连接服务");
    }
  }, [commit]);

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0);
    const expired = () => {
      activeRequest.current = null;
      commit({ mode: "team", authenticated: false, user: null });
    };
    const focused = () => {
      void refresh();
    };
    window.addEventListener("afds-session-expired", expired);
    window.addEventListener("focus", focused);
    const timer = window.setInterval(focused, 60000);
    return () => {
      activeRequest.current = null;
      window.removeEventListener("afds-session-expired", expired);
      window.removeEventListener("focus", focused);
      window.clearInterval(timer);
      window.clearTimeout(initial);
    };
  }, [refresh, commit]);

  async function login(event: FormEvent) {
    event.preventDefault();
    mutating.current = true;
    activeRequest.current = null;
    setPending(true);
    setError("");
    try {
      const value = await api<Session>("/auth/login", { username, password });
      commit(value);
      setPassword("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "登录失败");
    } finally {
      mutating.current = false;
      setPending(false);
    }
  }

  async function logout() {
    mutating.current = true;
    activeRequest.current = null;
    setPending(true);
    setError("");
    try {
      await api("/auth/logout", {});
      commit({ mode: "team", authenticated: false, user: null });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "退出失败");
    } finally {
      mutating.current = false;
      setPending(false);
    }
  }

  if (!session)
    return (
      <main className="session-screen">
        <section className="session-card">
          <h1>AFDS</h1>
          <p role="status">正在连接工作台</p>
          {error && (
            <>
              <p role="alert">{error}</p>
              <button onClick={() => void refresh()}>重试连接</button>
            </>
          )}
        </section>
      </main>
    );
  if (!session.authenticated)
    return (
      <main className="session-screen">
        <form className="session-card" onSubmit={(event) => void login(event)}>
          <h1>AFDS</h1>
          <h2>团队登录</h2>
          <label>
            用户名
            <input
              autoComplete="username"
              value={username}
              required
              maxLength={64}
              disabled={pending}
              onChange={(event) => setUsername(event.target.value)}
            />
          </label>
          <label>
            密码
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              required
              maxLength={1024}
              disabled={pending}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          {error && <p role="alert">{error}</p>}
          <button className="primary" disabled={pending}>
            {pending ? "登录中" : "登录"}
          </button>
        </form>
      </main>
    );
  return (
    <SessionContext.Provider value={{ session, logout, pending, error }}>
      <Fragment key={session.user?.id}>{children}</Fragment>
    </SessionContext.Provider>
  );
}

export function SessionStatus() {
  const { session, logout, pending, error } = useSession();
  if (session.mode === "local") return null;
  return (
    <div className="session-status">
      <span>{session.user?.display_name}</span>
      <button disabled={pending} onClick={() => void logout()}>
        退出登录
      </button>
      {error && <span role="alert">{error}</span>}
    </div>
  );
}
