import {
  Clapperboard,
  Film,
  Images,
  ListTodo,
  Scissors,
  ScanEye,
  Users,
} from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

const tools = [
  { id: "script", label: "剧本构思", icon: Clapperboard },
  { id: "continuity", label: "设定与参考", icon: Users },
  { id: "assets", label: "图像与声音", icon: Images },
  { id: "storyboard", label: "镜头与候选", icon: Film },
  { id: "review", label: "镜头审看", icon: ScanEye },
  { id: "timeline", label: "剪辑与声音", icon: Scissors },
  { id: "jobs", label: "执行进度", icon: ListTodo },
];

export function WorkbenchToolbar({
  activeTool,
  select,
}: {
  activeTool: string;
  select: (tool: string) => void;
}) {
  return (
    <nav className="workbench-tools" aria-label="创作工具">
      {tools.map(({ id, label, icon: Icon }) => (
        <button
          key={id}
          aria-pressed={activeTool === id}
          onClick={() => select(id)}
        >
          <Icon size={16} />
          {label}
        </button>
      ))}
    </nav>
  );
}

export function WorkbenchDock({
  activeTool,
  shotTitle,
  close,
  children,
}: {
  activeTool: string;
  shotTitle?: string;
  close: () => void;
  children: ReactNode;
}) {
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (window.innerWidth <= 1100)
      panel.current?.scrollIntoView?.({ block: "start" });
  }, [activeTool]);
  return (
    <section ref={panel} className="workbench-dock" aria-label="创作工具面板">
      <header className="dock-header">
        <div>
          <strong>{tools.find((t) => t.id === activeTool)?.label}</strong>
          {shotTitle && <small>操作镜头 · {shotTitle}</small>}
        </div>
        {shotTitle && <button onClick={close}>回到镜头候选</button>}
      </header>
      {children}
    </section>
  );
}
