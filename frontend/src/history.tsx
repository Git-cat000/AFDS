import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ProjectEvent } from "./api";
import { queueRoutedRequest } from "./model-routing";
import type { Job } from "./types";

export type Page<T> = {
  items: T[];
  total: number;
  next_cursor: string | null;
  has_more: boolean;
};
export type JobPage = Page<Job> & {
  active_count: number;
  active_operations: Record<string, number>;
  state_counts: Record<string, number>;
};

export function useCursorPage<T>(
  key: string,
  projectId: string,
  path: string,
  enabled = true,
) {
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const cursor = cursors.at(-1);
  const query = useQuery({
    queryKey: [key, projectId, path, cursor],
    enabled,
    queryFn: () =>
      api<Page<T>>(
        `/projects/${projectId}/${path}${path.includes("?") ? "&" : "?"}limit=25${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
      ),
    refetchInterval: 5000,
  });
  return {
    ...query,
    pagination: (
      <nav className="pagination" aria-label="记录翻页">
        <button
          disabled={cursors.length === 1}
          onClick={() => setCursors([null])}
        >
          最新记录
        </button>
        <button
          disabled={cursors.length === 1}
          onClick={() => setCursors(cursors.slice(0, -1))}
        >
          上一页
        </button>
        <span>第 {cursors.length} 页</span>
        <button
          disabled={!query.data?.next_cursor || query.isFetching}
          onClick={() => setCursors([...cursors, query.data!.next_cursor])}
        >
          下一页
        </button>
      </nav>
    ),
  };
}

type Activity = { items: ProjectEvent[]; cursor: number; has_more: boolean };
export function useProjectActivity(projectId: string | null) {
  const client = useQueryClient();
  return useQuery({
    queryKey: ["activity", projectId],
    enabled: !!projectId,
    queryFn: async () => {
      const previous = client.getQueryData<Activity>(["activity", projectId]);
      const page = await api<Activity>(
        `/projects/${projectId}/activity${previous ? `?after=${previous.cursor}` : ""}`,
      );
      if (page.items.length && previous) {
        for (const key of [
          "project",
          "jobs",
          "plans",
          "budget",
          "history",
          "feedback",
          "reviews",
          "operations",
        ]) {
          void client.invalidateQueries({ queryKey: [key, projectId] });
        }
      }
      return {
        ...page,
        items: [...(previous?.items || []), ...page.items].slice(-500),
      };
    },
    refetchInterval: (query) => (query.state.data?.has_more ? 100 : 2000),
  });
}

export async function queuePlanning(
  projectId: string,
  kind: "storyboard" | "semantic",
  body: Record<string, unknown>,
) {
  return queueRoutedRequest<Job>({
    project: { project_id: projectId, revision: Number(body.base_revision) },
    path: `/projects/${projectId}/planning/${kind}`,
    body,
    capability: "storyboard",
    storageKey: `afds-planning-${projectId}-${kind}`,
    requirements: {
      remoteOnly: kind === "semantic",
      providerId: kind === "semantic" ? "openai-llm" : undefined,
    },
  });
}
