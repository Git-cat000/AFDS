export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public details: unknown,
  ) {
    super(message);
  }
}

export type ProjectEvent = {
  event_id: number;
  type: string;
  timestamp: number;
};

export async function fetchEventHistory(
  projectId: string,
  after = 0,
): Promise<ProjectEvent[]> {
  const events: ProjectEvent[] = [];
  let cursor = after;
  while (true) {
    const page = await api<ProjectEvent[]>(
      `/events?project_id=${projectId}&after=${cursor}`,
    );
    events.push(...page);
    if (page.length < 200) return events;
    cursor = page[page.length - 1].event_id;
  }
}

export async function api<T>(
  path: string,
  body?: unknown,
  method?: string,
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method: method || (body === undefined ? "GET" : "POST"),
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    const messages: Record<string, string> = {
      PROVIDER_AUTH_FAILED: "鉴权失败，请检查 API Key 与账号权限。",
      PROVIDER_ENDPOINT_UNSUPPORTED:
        "服务未提供此接口，请检查 URL 版本前缀和兼容协议。",
      PROVIDER_URL_BLOCKED:
        "地址无法访问或被限制，请检查 URL；本机服务需要开启内网选项。",
      PROVIDER_RATE_LIMIT: "服务商限流，请稍后重试。",
      PROVIDER_TIMEOUT: "服务请求超时，可调整超时设置后重试。",
      PROVIDER_CONNECTION_FAILED: "连接失败，请检查服务地址和网络。",
      PROVIDER_RETIRED:
        "OpenAI 官方视频服务已停止，请使用兼容的第三方视频服务。",
      CONFIG_CONFLICT: "连接配置已有更新，请刷新页面后重新保存。",
      REMOTE_CONSENT_REQUIRED: "请先勾选内容发送和费用授权。",
      SECRET_STORE_UNAVAILABLE:
        "本机密钥文件不可用，请恢复 data/secrets 或重新配置连接。",
      PROVIDER_OUTPUT_INVALID:
        "服务返回的结果不符合此接口要求，请检查模型与协议。",
    };
    throw new ApiError(
      data.error?.code || "HTTP_ERROR",
      messages[data.error?.code] || data.error?.message || "请求失败",
      data.error?.details,
    );
  }
  return data;
}

export function mediaUrl(projectId: string, assetId: string, download = false) {
  return `/api/v1/projects/${projectId}/assets/${assetId}/file${download ? "?download=true" : ""}`;
}
