import { AudioLines, BrainCircuit, Image, Video } from "lucide-react";

export const categories = [
  {
    kind: "llm" as const,
    label: "LLM",
    role: "剧本与分镜规划",
    icon: BrainCircuit,
    path: "/chat/completions",
  },
  {
    kind: "video" as const,
    label: "视频生成",
    role: "镜头视频与异步任务",
    icon: Video,
    path: "/videos",
  },
  {
    kind: "image" as const,
    label: "图像生成",
    role: "参考图与视觉素材",
    icon: Image,
    path: "/images/generations",
  },
  {
    kind: "speech" as const,
    label: "语音合成",
    role: "旁白与配音素材",
    icon: AudioLines,
    path: "/audio/speech",
  },
  {
    kind: "transcription" as const,
    label: "语音转写",
    role: "带时间戳的字幕候选",
    icon: AudioLines,
    path: "/audio/transcriptions",
  },
];
