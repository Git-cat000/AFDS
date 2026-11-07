import { create } from "zustand";
import { persist } from "zustand/middleware";

export const useStudio = create(
  persist<{
    projectId: string | null;
    setProject: (projectId: string | null) => void;
  }>(
    (set) => ({
      projectId: null,
      setProject: (projectId) => set({ projectId }),
    }),
    { name: "afds-studio" },
  ),
);
