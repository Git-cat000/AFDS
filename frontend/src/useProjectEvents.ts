import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useSession } from "./session";

export function useProjectEvents(projectId: string | null) {
  const client = useQueryClient();
  const { session } = useSession();
  useEffect(() => {
    if (
      !projectId ||
      session.mode !== "team" ||
      !session.authenticated ||
      typeof EventSource === "undefined"
    )
      return;
    const key = `afds-events-${session.user?.id}-${projectId}`;
    let cursor = "0";
    try {
      cursor = sessionStorage.getItem(key) || "0";
    } catch {
      /* The live cursor still works without storage. */
    }
    const source = new EventSource(
      `/api/v1/projects/${projectId}/events/stream?after=${encodeURIComponent(cursor)}`,
    );
    const refresh = () => {
      for (const name of [
        "project",
        "jobs",
        "project-access",
        "project-activity",
        "budget",
        "revisions",
      ]) {
        void client.invalidateQueries({ queryKey: [name, projectId] });
      }
      window.dispatchEvent(
        new CustomEvent("afds-project-event", { detail: { projectId } }),
      );
    };
    source.addEventListener("project", (event) => {
      const item = event as MessageEvent;
      try {
        sessionStorage.setItem(key, item.lastEventId);
      } catch {
        /* Keep the connection active. */
      }
      refresh();
    });
    source.addEventListener("snapshot_required", refresh);
    source.addEventListener("access_revoked", () => {
      source.close();
      void client.invalidateQueries({ queryKey: ["projects"] });
      refresh();
    });
    return () => source.close();
  }, [
    projectId,
    session.authenticated,
    session.mode,
    session.user?.id,
    client,
  ]);
}
