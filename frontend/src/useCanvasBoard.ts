import { useRef, useState } from "react";
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

export function useCanvasBoard(project: Project) {
  const [initial] = useState(() => {
    try {
      return loadBoard(
        project,
        localStorage.getItem(`afds-canvas-${project.project_id}`),
      );
    } catch {
      return emptyBoard();
    }
  });
  const [history, setHistory] = useState<BoardHistory>({
    past: [],
    present: initial,
    future: [],
  });
  const current = useRef(history);
  const gesture = useRef<CanvasBoard | null>(null);
  const [storageError, setStorageError] = useState("");
  function publish(value: BoardHistory, persist = true) {
    current.current = value;
    setHistory(value);
    if (persist) {
      try {
        localStorage.setItem(
          `afds-canvas-${project.project_id}`,
          JSON.stringify(value.present),
        );
        setStorageError("");
      } catch {
        setStorageError("浏览器未能保存画布，本次修改暂存在当前页面。");
      }
    }
  }
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
