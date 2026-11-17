import { createContext, useContext } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

export type Session = {
  mode: "local" | "team";
  authenticated: boolean;
  user: {
    id: string;
    username: string;
    display_name: string;
    is_admin: boolean;
  } | null;
  csrf_token?: string;
};
export const SessionContext = createContext({
  session: {
    mode: "local",
    authenticated: true,
    user: {
      id: "00000000-0000-0000-0000-000000000001",
      username: "_local",
      display_name: "本机用户",
      is_admin: true,
    },
  } as Session,
  logout: async () => {},
  pending: false,
  error: "",
});
export function useSession() {
  return useContext(SessionContext);
}

export function useProjectAccess(projectId: string | null) {
  const { session } = useSession();
  const access = useQuery({
    queryKey: ["project-access", projectId, session.user?.id],
    enabled: session.mode === "team" && !!projectId,
    queryFn: () =>
      api<{ role: string; actions: string[] }>(`/projects/${projectId}/access`),
    refetchInterval: 5000,
  });
  return session.mode === "local"
    ? {
        role: "owner",
        actions: [
          "read",
          "edit",
          "review",
          "generate",
          "run_script",
          "manage_members",
          "manage_budget",
        ],
      }
    : access.data;
}
