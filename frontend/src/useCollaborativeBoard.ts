import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import {
  emptyBoard,
  loadBoard,
  type BoardHistory,
  type CanvasBoard,
} from "./canvas-board";
import {
  diffWorkspace,
  applyWorkspaceOperations,
  mergeWorkspaceDraft,
  type SharedBoard,
  type WorkspaceOperation,
} from "./workspace-operations";
import { useSession } from "./session";
import type { Project } from "./types";

type Snapshot = {
  project_id: string;
  workspace_revision: number;
  initialized: boolean;
  document: SharedBoard;
  entity_versions: Record<string, number>;
};
type Pending = {
  operations: WorkspaceOperation[];
  board: CanvasBoard;
  redo?: boolean;
};
type Group = {
  operations: WorkspaceOperation[];
  versions: Record<string, number>;
};
type Presence = {
  user_id: string;
  display_name: string;
  entity_id: string | null;
};
type Controls = {
  schedule: () => void;
  retry: () => Promise<void>;
  replace: () => Promise<void>;
  undo: () => Promise<void>;
  redo: () => Promise<void>;
};
const nothing = async () => {};

export function useCollaborativeBoard(project: Project, enabled: boolean) {
  const { session } = useSession();
  const projectId = project.project_id;
  const projectRef = useRef(project);
  useEffect(() => {
    projectRef.current = project;
  }, [project]);
  const key = `afds-team-canvas-${session.user?.id}-${projectId}`;
  const [history, setHistory] = useState<BoardHistory>({
    past: [],
    present: emptyBoard(),
    future: [],
  });
  const current = useRef(history);
  const [syncError, setSyncError] = useState("");
  const [syncStatus, setSyncStatus] = useState("正在读取协作画布");
  const [online, setOnline] = useState<Presence[]>([]);
  const controls = useRef<Controls>({
    schedule: () => {},
    retry: nothing,
    replace: nothing,
    undo: nothing,
    redo: nothing,
  });
  const gesture = useRef(false);
  const editing = useRef<string | null>(null);
  const commits = useRef<Group[]>([]);
  const redos = useRef<Group[]>([]);
  const cache = useRef<() => void>(() => {});
  const publish = useCallback((board: CanvasBoard) => {
    const value = {
      present: board,
      past: commits.current.map(() => board),
      future: redos.current.map(() => board),
    };
    current.current = value;
    setHistory(value);
    cache.current();
  }, []);

  useEffect(() => {
    if (!enabled) return;
    let active = true;
    let ready = false;
    let busy = false;
    let paused = false;
    let refreshPending = false;
    let conflict = false;
    let pending: Pending | undefined;
    let timer: ReturnType<typeof setTimeout>;
    let base: Snapshot = {
      project_id: projectId,
      workspace_revision: 0,
      initialized: false,
      document: emptyBoard(),
      entity_versions: {},
    };
    const root = `/projects/${projectId}`;
    commits.current = [];
    redos.current = [];
    gesture.current = false;
    editing.current = null;
    let board = emptyBoard();
    try {
      const saved = JSON.parse(localStorage.getItem(key) || "null");
      if (saved?.base?.project_id === projectId) {
        base = saved.base;
        board = loadBoard(projectRef.current, JSON.stringify(saved.board));
        pending = saved.pending;
      }
    } catch {
      /* Start from the server when the cache is unreadable. */
    }
    cache.current = () => {
      if (!active) return;
      try {
        localStorage.setItem(
          key,
          JSON.stringify({ base, board: current.current.present, pending }),
        );
      } catch {
        setSyncError("浏览器未能保存协作草稿，当前修改暂存在页面中。");
      }
    };
    publish(board);
    const dirty = () =>
      diffWorkspace(
        base.document,
        current.current.present,
        base.entity_versions,
      ).length > 0;

    function accept(saved: Snapshot, before: SharedBoard, reset = false) {
      if (!active) return;
      const merged = mergeWorkspaceDraft(
        before,
        current.current.present,
        saved.document,
      );
      base = saved;
      ready = true;
      conflict = !reset && merged.conflicts.length > 0;
      paused = conflict;
      publish(
        reset
          ? loadBoard(
              projectRef.current,
              JSON.stringify({ ...current.current.present, ...saved.document }),
            )
          : merged.board,
      );
      setSyncError(
        conflict
          ? `这些对象存在同时修改，草稿已保留。${merged.conflicts.join("、")}`
          : "",
      );
      setSyncStatus(
        conflict
          ? "协作冲突待处理"
          : dirty()
            ? "协作草稿待保存"
            : "协作画布已同步",
      );
    }

    async function load(reset = false, overwrite = false) {
      if (busy || gesture.current) {
        refreshPending = true;
        return;
      }
      const sourceRevision = base.workspace_revision;
      try {
        const saved = await api<Snapshot>(`${root}/workspace/collaboration`);
        if (!active || saved.workspace_revision < base.workspace_revision)
          return;
        const before = base.document;
        if (overwrite) {
          base = saved;
          ready = true;
          paused = false;
          conflict = false;
          setSyncError("");
          cache.current();
        } else accept(saved, before, reset);
        schedule();
      } catch (error) {
        if (!active || sourceRevision < base.workspace_revision) return;
        paused = true;
        setSyncStatus("协作草稿待同步");
        setSyncError(
          error instanceof ApiError
            ? error.message
            : "协作服务暂不可用，草稿已保留。恢复连接后可重试。",
        );
        cache.current();
      }
    }

    async function flush() {
      if (
        !active ||
        !ready ||
        paused ||
        busy ||
        gesture.current ||
        (!pending && !dirty())
      )
        return;
      busy = true;
      setSyncStatus("正在提交协作操作");
      if (!pending) {
        const operations = diffWorkspace(
          base.document,
          current.current.present,
          base.entity_versions,
        ).slice(0, 64);
        pending = {
          operations,
          board: applyWorkspaceOperations(
            { ...current.current.present, ...base.document },
            operations,
          ),
        };
      }
      cache.current();
      try {
        // Retain this exact identity across an unknown response and page reload.
        const submitted = pending;
        const saved = await api<Snapshot>(`${root}/workspace/operations`, {
          operations: submitted.operations,
        });
        if (!active) return;
        if (submitted.redo) advanceRedos(submitted.operations, saved);
        pending = undefined;
        commits.current.push({
          operations: submitted.operations,
          versions: saved.entity_versions,
        });
        commits.current = commits.current.slice(-50);
        if (submitted.redo) redos.current.pop();
        else redos.current = [];
        accept(saved, submitted.board);
      } catch (error) {
        if (!active) return;
        paused = true;
        conflict =
          error instanceof ApiError &&
          ["ENTITY_CONFLICT", "INVALID_WORKSPACE"].includes(error.code);
        setSyncStatus("协作草稿待同步");
        setSyncError(
          conflict
            ? "协作对象已发生变化，当前草稿已保留。请载入服务器版本或确认重新保存草稿。"
            : "提交结果暂未确认，草稿与请求身份已保留。恢复连接后可重试。",
        );
        cache.current();
      } finally {
        busy = false;
        if (active && refreshPending && !paused) {
          refreshPending = false;
          void load();
        }
        schedule();
      }
    }

    function schedule() {
      clearTimeout(timer);
      if (active && ready && !paused && !gesture.current)
        timer = setTimeout(() => void flush(), 250);
    }

    function advanceRedos(operations: WorkspaceOperation[], saved: Snapshot) {
      for (const item of operations) {
        const key = `${item.kind.includes("edge") ? "edge" : "node"}/${item.entity_id}`;
        for (const group of redos.current) {
          if (group.versions[key] === base.entity_versions[key])
            group.versions = {
              ...group.versions,
              [key]: saved.entity_versions[key],
            };
        }
      }
    }

    async function undo() {
      if (
        !active ||
        busy ||
        !ready ||
        dirty() ||
        pending ||
        !commits.current.length
      )
        return;
      busy = true;
      const group = commits.current.at(-1)!;
      try {
        const before = base.document;
        const saved = await api<Snapshot>(`${root}/workspace/undo`, {
          operation_ids: group.operations.map((item) => item.operation_id),
        });
        if (!active) return;
        commits.current.pop();
        advanceRedos(group.operations, saved);
        redos.current.push({ ...group, versions: saved.entity_versions });
        accept(saved, before);
      } catch (error) {
        if (active)
          setSyncError(
            error instanceof Error ? error.message : "后续修改阻止了撤销。",
          );
      } finally {
        busy = false;
        if (refreshPending) {
          refreshPending = false;
          void load();
        }
      }
    }

    async function redo() {
      if (
        !active ||
        busy ||
        !ready ||
        dirty() ||
        pending ||
        !redos.current.length
      )
        return;
      const group = redos.current.at(-1)!;
      const operations = group.operations.map((item) => ({
        ...item,
        operation_id: crypto.randomUUID(),
        expected_entity_version:
          group.versions[
            `${item.kind.includes("edge") ? "edge" : "node"}/${item.entity_id}`
          ] || 0,
      }));
      pending = { operations, board: current.current.present, redo: true };
      await flush();
    }

    controls.current = {
      schedule,
      undo,
      redo,
      retry: async () => {
        if (conflict) {
          pending = undefined;
          await load(false, true);
        }
        paused = false;
        ready = true;
        await flush();
      },
      replace: async () => {
        pending = undefined;
        paused = false;
        await load(true);
      },
    };
    if (pending) {
      ready = true;
      void flush();
    } else void load();
    const event = (event: Event) => {
      if ((event as CustomEvent).detail?.projectId !== projectId) return;
      if (busy || gesture.current || pending) refreshPending = true;
      else void load();
    };
    window.addEventListener("afds-project-event", event);
    const reconnect = () => {
      if (!paused) void load();
    };
    window.addEventListener("online", reconnect);
    const presence = async () => {
      try {
        const people = await api<Presence[]>(
          `${root}/presence`,
          { entity_id: editing.current },
          "PUT",
        );
        if (active) setOnline(people);
      } catch {
        /* Session and access feedback come from the main request path. */
      }
    };
    void presence();
    const heartbeat = setInterval(() => void presence(), 2000);
    const refresh = setInterval(() => {
      if (!busy && !paused && !gesture.current) void load();
    }, 5000);
    return () => {
      active = false;
      clearTimeout(timer);
      clearInterval(heartbeat);
      clearInterval(refresh);
      window.removeEventListener("afds-project-event", event);
      window.removeEventListener("online", reconnect);
      cache.current = () => {};
      controls.current = {
        schedule: () => {},
        retry: nothing,
        replace: nothing,
        undo: nothing,
        redo: nothing,
      };
    };
  }, [enabled, key, projectId, publish]);

  function change(
    update: (board: CanvasBoard) => CanvasBoard,
    remember = true,
    persist = true,
  ) {
    const next = update(current.current.present);
    if (next === current.current.present) return;
    if (remember) redos.current = [];
    publish(next);
    if (persist) controls.current.schedule();
  }
  return {
    board: history.present,
    history,
    current,
    syncError,
    syncStatus,
    storageError: "",
    online,
    retrySync: () => controls.current.retry(),
    loadServer: () => controls.current.replace(),
    change,
    hasGesture: () => gesture.current,
    startGesture: (entityId?: string) => {
      gesture.current = true;
      editing.current = entityId ?? editing.current;
    },
    finishGesture: () => {
      gesture.current = false;
      editing.current = null;
      cache.current();
      controls.current.schedule();
    },
    undo: () => void controls.current.undo(),
    redo: () => void controls.current.redo(),
    persist: () => {
      cache.current();
      controls.current.schedule();
    },
  };
}
