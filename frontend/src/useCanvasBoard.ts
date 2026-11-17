import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import {
  emptyBoard,
  loadBoard,
  recordBoard,
  undoBoard,
  redoBoard,
  type BoardHistory,
  type CanvasBoard,
} from "./canvas-board";
import type { Project } from "./types";
import { useSession } from "./session";
import { useCollaborativeBoard } from "./useCollaborativeBoard";

type SharedBoard = Omit<CanvasBoard, "x" | "y" | "zoom">;
type WorkspaceSnapshot = {
  project_id: string;
  workspace_revision: number;
  initialized: boolean;
  document: SharedBoard;
};
type Connection = {
  active: boolean;
  ready: boolean;
  dirty: boolean;
  busy: boolean;
  paused: boolean;
  refreshPending: boolean;
  revision: number;
  savedHash: string;
  timer?: ReturnType<typeof setTimeout>;
};

function shared(board: CanvasBoard): SharedBoard {
  return {
    version: 2,
    positions: board.positions,
    nodes: board.nodes,
    edges: board.edges,
    hiddenShots: board.hiddenShots,
  };
}
const hash = (board: CanvasBoard) => JSON.stringify(shared(board));
function localBoard(project: Project) {
  try {
    const raw = localStorage.getItem(`afds-canvas-${project.project_id}`);
    const metadata = JSON.parse(
      localStorage.getItem(`afds-workspace-sync-${project.project_id}`) ||
        "null",
    );
    return {
      board: loadBoard(project, raw),
      cached: !!raw,
      dirty: metadata?.dirty === true,
    };
  } catch {
    return { board: emptyBoard(), cached: false, dirty: false };
  }
}

export function useCanvasBoard(project: Project) {
  const { session } = useSession();
  const team = session.mode === "team";
  const localBoard = useLocalCanvasBoard(project, !team);
  const teamBoard = useCollaborativeBoard(project, team);
  return team ? teamBoard : { ...localBoard, online: [] };
}

function useLocalCanvasBoard(project: Project, enabled: boolean) {
  const projectId = project.project_id;
  const projectRef = useRef(project);
  useEffect(() => {
    projectRef.current = project;
  }, [project]);
  const [initial] = useState(() => localBoard(project).board);
  const [history, setHistory] = useState<BoardHistory>({
    past: [],
    present: initial,
    future: [],
  });
  const current = useRef(history);
  const gesture = useRef<CanvasBoard | null>(null);
  const [storageError, setStorageError] = useState("");
  const [syncError, setSyncError] = useState("");
  const [syncStatus, setSyncStatus] = useState("正在读取画布");
  const connection = useRef<Connection | null>(null);
  const schedule = useRef<() => void>(() => {});
  const reload = useRef<
    (mode: "retry" | "replace" | "refresh") => Promise<void>
  >(async () => {});
  const publish = useCallback(
    (value: BoardHistory, persist = true) => {
      current.current = value;
      setHistory(value);
      const state = connection.current;
      if (state) state.dirty = hash(value.present) !== state.savedHash;
      if (persist) {
        try {
          localStorage.setItem(
            `afds-canvas-${projectId}`,
            JSON.stringify(value.present),
          );
          localStorage.setItem(
            `afds-workspace-sync-${projectId}`,
            JSON.stringify({ dirty: state?.dirty ?? true }),
          );
          setStorageError("");
        } catch {
          setStorageError("浏览器未能保存画布，本次修改暂存在当前页面。");
        }
        schedule.current();
      }
    },
    [projectId],
  );

  useEffect(() => {
    if (!enabled) return;
    const local = localBoard(projectRef.current);
    const state: Connection = {
      active: true,
      ready: false,
      dirty: local.dirty,
      busy: false,
      paused: false,
      refreshPending: false,
      revision: 0,
      savedHash: hash(local.board),
    };
    connection.current = state;
    gesture.current = null;
    const initialHistory = { past: [], present: local.board, future: [] };
    current.current = initialHistory;
    setHistory(initialHistory);
    setSyncError("");
    setSyncStatus("正在读取画布");
    const path = `/projects/${projectId}/workspace`;

    async function flush() {
      if (
        !state.active ||
        !state.ready ||
        state.paused ||
        state.busy ||
        !state.dirty
      )
        return;
      state.busy = true;
      setSyncStatus("正在保存画布");
      const document = shared(current.current.present);
      try {
        const saved = await api<WorkspaceSnapshot>(
          path,
          { base_workspace_revision: state.revision, document },
          "PUT",
        );
        if (!state.active) return;
        if (!Number.isInteger(saved.workspace_revision))
          throw new Error("画布服务返回了无效版本。");
        state.revision = saved.workspace_revision;
        state.savedHash = JSON.stringify(document);
        state.dirty = hash(current.current.present) !== state.savedHash;
        setSyncError("");
        setSyncStatus(state.dirty ? "画布草稿待保存" : "画布已保存");
        publish(current.current);
      } catch (error) {
        if (!state.active) return;
        state.paused = true;
        setSyncStatus("画布草稿待同步");
        setSyncError(
          error instanceof ApiError && error.code === "WORKSPACE_CONFLICT"
            ? "画布已被其他页面修改，当前草稿已保留。请选择载入服务器版本或重新保存草稿。"
            : "服务器未能保存画布，当前草稿已保留。连接恢复后可重新保存。",
        );
      } finally {
        state.busy = false;
        if (state.active && state.refreshPending) {
          state.refreshPending = false;
          void load("refresh");
        }
        if (state.active && !state.paused && state.dirty) schedule.current();
      }
    }
    schedule.current = () => {
      clearTimeout(state.timer);
      if (state.active && state.ready && !state.paused && state.dirty) {
        state.timer = setTimeout(() => void flush(), 250);
      }
    };
    async function load(mode: "initial" | "retry" | "replace" | "refresh") {
      if (mode === "refresh" && (!state.ready || state.busy)) {
        state.refreshPending = true;
        return;
      }
      clearTimeout(state.timer);
      try {
        const saved = await api<WorkspaceSnapshot>(path);
        if (!state.active) return;
        if (
          !Number.isInteger(saved.workspace_revision) ||
          !saved.document ||
          saved.project_id !== projectId
        ) {
          throw new Error("画布服务返回了无效数据。");
        }
        if (saved.workspace_revision < state.revision) return;
        if (mode === "refresh" && saved.workspace_revision === state.revision) {
          if (state.dirty) schedule.current();
          return;
        }
        const board = loadBoard(
          projectRef.current,
          JSON.stringify({ ...current.current.present, ...saved.document }),
        );
        state.revision = saved.workspace_revision;
        state.ready = true;
        state.savedHash = hash(board);
        const hasDraft =
          state.dirty && hash(current.current.present) !== state.savedHash;
        if (
          (mode === "initial" || mode === "refresh") &&
          saved.initialized &&
          hasDraft
        ) {
          state.paused = true;
          setSyncStatus("画布草稿待同步");
          setSyncError(
            "发现尚未同步的本机草稿，服务器也有画布。请选择载入服务器版本或重新保存草稿。",
          );
          return;
        }
        state.paused = false;
        setSyncError("");
        if (
          mode === "replace" ||
          ((mode === "initial" || mode === "refresh") &&
            saved.initialized &&
            !hasDraft)
        ) {
          state.dirty = false;
          publish(
            mode === "replace"
              ? recordBoard(current.current, board)
              : { past: [], present: board, future: [] },
          );
        } else {
          state.dirty = hash(current.current.present) !== state.savedHash;
          if (
            mode === "initial" &&
            !saved.initialized &&
            !local.cached &&
            !hasDraft
          )
            state.dirty = false;
          if (state.dirty) schedule.current();
        }
        setSyncStatus(state.dirty ? "画布草稿待保存" : "画布已保存");
      } catch {
        if (state.active) {
          state.paused = true;
          setSyncStatus("画布草稿待同步");
          setSyncError(
            "服务器画布暂不可用，本机草稿已保留。可在连接恢复后重新保存。",
          );
        }
      } finally {
        if (
          state.active &&
          state.ready &&
          state.refreshPending &&
          !state.busy
        ) {
          state.refreshPending = false;
          void load("refresh");
        }
      }
    }
    reload.current = load;
    void load("initial");
    return () => {
      state.active = false;
      clearTimeout(state.timer);
    };
  }, [projectId, publish, enabled]);
  const previousFilm = useRef({ projectId, revision: project.revision });
  useEffect(() => {
    if (!enabled) return;
    const previous = previousFilm.current;
    previousFilm.current = { projectId, revision: project.revision };
    if (
      previous.projectId === projectId &&
      previous.revision !== project.revision
    ) {
      void reload.current("refresh");
    }
  }, [projectId, project.revision, enabled]);
  function change(
    update: (board: CanvasBoard) => CanvasBoard,
    remember = true,
    persist = true,
  ) {
    const value = current.current;
    const next = update(value.present);
    if (next === value.present) return;
    publish(
      remember ? recordBoard(value, next) : { ...value, present: next },
      persist,
    );
  }
  function finishGesture() {
    const before = gesture.current;
    gesture.current = null;
    if (before && before !== current.current.present) {
      publish({
        past: [...current.current.past, before].slice(-50),
        present: current.current.present,
        future: [],
      });
    }
  }
  return {
    board: history.present,
    history,
    current,
    storageError,
    syncError,
    syncStatus,
    retrySync: () => reload.current("retry"),
    loadServer: () => reload.current("replace"),
    change,
    hasGesture: () => gesture.current !== null,
    startGesture: () => {
      gesture.current ??= current.current.present;
    },
    finishGesture,
    undo: () => publish(undoBoard(current.current)),
    redo: () => publish(redoBoard(current.current)),
    persist: () => publish(current.current),
  };
}
