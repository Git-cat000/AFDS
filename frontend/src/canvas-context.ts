import { createContext, useContext } from "react";
import type { Point } from "./canvas-board";
import type { Shot } from "./types";

export type CanvasActions = {
  selectShot: (id: string) => void;
  editShot: (shot: Shot) => void;
  moveNode: (id: string, point: Point) => void;
  updateNote: (id: string, text: string) => void;
  port: (id: string, type: "source" | "target") => void;
  pendingFrom: string | null;
};
export const CanvasActionContext = createContext<CanvasActions | null>(null);
export function useCanvasActions() {
  const value = useContext(CanvasActionContext);
  if (!value) throw new Error("Canvas node requires its workbench");
  return value;
}
