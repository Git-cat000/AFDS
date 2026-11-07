export function framesToTime(
  frames: number,
  format: { fps_num: number; fps_den: number },
) {
  const seconds = (frames * format.fps_den) / format.fps_num;
  return `${Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0")}:${(seconds % 60).toFixed(1).padStart(4, "0")}`;
}

export const statusLabel: Record<string, string> = {
  draft: "草稿",
  planned: "待生成",
  queued: "排队中",
  claimed: "已领取",
  running: "执行中",
  generating: "生成中",
  verifying: "校验中",
  ready: "可用",
  approved: "已批准",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  cancel_requested: "取消中",
  stale: "待更新",
  needs_review: "需审阅",
  retry_wait: "等待重试",
  missing: "尚未生成",
};
