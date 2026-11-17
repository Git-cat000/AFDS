import { useState } from "react";
import { api } from "./api";
import { useSession } from "./session";
import type { Project } from "./types";
import type {
  ProductionStage,
  ScriptPlan,
  SkillAction,
  SkillPackage,
} from "./skill-types";

export function SkillRunner({
  project,
  skill,
  stage,
  shotId,
  busy,
  perform,
}: {
  project: Project;
  skill: SkillPackage;
  stage: ProductionStage;
  shotId?: string;
  busy: boolean;
  perform: SkillAction;
}) {
  const [script, setScript] = useState(skill.scripts[0]?.path || "");
  const [argumentsText, setArgumentsText] = useState("[]");
  const [timeout, setTimeout] = useState(60);
  const { session } = useSession();
  const [mode, setMode] = useState<"local-user" | "container">(
    session.mode === "team" ? "container" : "local-user",
  );
  const [onlyShot, setOnlyShot] = useState(false);
  const [preview, setPreview] = useState<ScriptPlan>();
  const [consent, setConsent] = useState(false);
  const [submitted, setSubmitted] = useState("");
  const stale = preview && preview.base_revision !== project.revision;
  function changed() {
    setPreview(undefined);
    setConsent(false);
    setSubmitted("");
  }
  return (
    <section className="skill-runner" aria-label="外部脚本运行">
      {!skill.scripts.length ? (
        <p className="muted">该技能包没有可运行脚本。</p>
      ) : (
        <>
          <label>
            执行环境
            <select
              aria-label="执行环境"
              value={mode}
              disabled={busy}
              onChange={(event) => {
                setMode(event.target.value as "local-user" | "container");
                changed();
              }}
            >
              {session.mode === "local" && (
                <option value="local-user">本机用户</option>
              )}
              <option value="container">隔离容器</option>
            </select>
          </label>
          <label>
            运行脚本
            <select
              aria-label="运行脚本"
              value={script}
              disabled={busy}
              onChange={(event) => {
                setScript(event.target.value);
                changed();
              }}
            >
              {skill.scripts.map((item) => (
                <option key={item.path}>{item.path}</option>
              ))}
            </select>
          </label>
          <label>
            脚本参数 JSON
            <textarea
              aria-label="脚本参数 JSON"
              value={argumentsText}
              disabled={busy}
              rows={3}
              spellCheck={false}
              onChange={(event) => {
                setArgumentsText(event.target.value);
                changed();
              }}
            />
          </label>
          <label>
            最长运行秒数
            <input
              type="number"
              min={5}
              max={300}
              value={timeout}
              disabled={busy}
              onChange={(event) => {
                setTimeout(Number(event.target.value));
                changed();
              }}
            />
          </label>
          {shotId && (
            <label className="check-label">
              <input
                type="checkbox"
                checked={onlyShot}
                disabled={busy}
                onChange={(event) => {
                  setOnlyShot(event.target.checked);
                  changed();
                }}
              />
              仅提供当前镜头上下文
            </label>
          )}
          <button
            disabled={busy || !script}
            onClick={() =>
              void perform(async () => {
                const argumentsValue: unknown = JSON.parse(argumentsText);
                if (
                  !Array.isArray(argumentsValue) ||
                  argumentsValue.some((value) => typeof value !== "string")
                )
                  throw new Error("参数需要填写 JSON 字符串数组");
                setConsent(false);
                setSubmitted("");
                setPreview(
                  await api<ScriptPlan>(
                    `/projects/${project.project_id}/skills/scripts/preview`,
                    {
                      base_revision: project.revision,
                      package_id: skill.id,
                      script_path: script,
                      arguments: argumentsValue,
                      timeout_seconds: timeout,
                      execution_mode: mode,
                      stage,
                      shot_ids: onlyShot && shotId ? [shotId] : [],
                    },
                  ),
                );
              })
            }
          >
            预览脚本运行
          </button>
          {preview && (
            <section className="script-approval" aria-label="脚本运行预览">
              <p>
                {preview.runtime.name} · 修订 {preview.base_revision} · 最长{" "}
                {preview.request.timeout_seconds} 秒
              </p>
              <small>包 SHA256 {preview.request.package_id}</small>
              {preview.request.script_sha256 && (
                <small>脚本 SHA256 {preview.request.script_sha256}</small>
              )}
              <pre aria-label="待运行脚本源码">{preview.source_code}</pre>
              <details>
                <summary>参数与输入上下文</summary>
                <pre>
                  {JSON.stringify(
                    {
                      arguments: preview.request.arguments,
                      context: preview.request.context,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
              {preview.request.execution_mode === "container" ? (
                <>
                  <p>隔离容器 · 网络关闭 · 只读技能包与输入 · 非 root 身份。</p>
                  <p>
                    1 CPU · 内存 512 MiB · 64 个进程 · 临时工作区 64 MiB ·
                    输出区 32 MiB。
                  </p>
                  {preview.request.execution_policy?.image_id && (
                    <small>
                      镜像 {preview.request.execution_policy.image_id}
                    </small>
                  )}
                  <small>
                    策略 SHA256 {preview.request.execution_policy_sha256}
                  </small>
                  <p>
                    执行前会验证实际资源限制，运行记录保留解释器版本与策略。
                  </p>
                </>
              ) : (
                <p>
                  脚本以当前本机用户权限运行，能够访问该用户的文件与网络。调用的外部服务及费用由脚本决定。
                </p>
              )}
              <p>
                输入通过标准输入、input.json 和 AFDS_CONTEXT_FILE 提供，outputs/
                中的结果可下载。
              </p>
              {!preview.runtime.available && (
                <p role="alert">
                  {preview.runtime.error
                    ? `${preview.runtime.error.code} · ${preview.runtime.error.message}。恢复后重新预览。`
                    : "本机缺少所需解释器，请安装后重新预览。"}
                </p>
              )}
              {!preview.request.execution_mode && (
                <p role="alert">执行协议已更新，请重新预览本次脚本运行。</p>
              )}
              {stale && (
                <p role="alert">工程修订已变化，请重新预览本次脚本运行。</p>
              )}
              <label className="check-label">
                <input
                  type="checkbox"
                  checked={consent}
                  disabled={busy || !!stale || !!submitted}
                  onChange={(event) => setConsent(event.target.checked)}
                />
                {preview.request.execution_mode === "container"
                  ? "已审阅源码和策略，允许在隔离容器中运行本次脚本"
                  : "已审阅源码和参数，允许以本机用户权限运行本次脚本"}
              </label>
              <button
                className="primary"
                disabled={
                  busy ||
                  !consent ||
                  !!stale ||
                  !preview.runtime.available ||
                  !preview.request.execution_mode ||
                  !!submitted
                }
                onClick={() =>
                  void perform(async () => {
                    const job = await api<{ id: string }>(
                      `/projects/${project.project_id}/skills/scripts/${preview.id}/execute`,
                      {
                        base_revision: preview.base_revision,
                        approved: true,
                        fingerprint: preview.fingerprint,
                      },
                    );
                    setSubmitted(job.id);
                    setConsent(false);
                  })
                }
              >
                批准并运行本次脚本
              </button>
              {submitted && (
                <p role="status">已提交运行，可在下方查看任务进度与结果。</p>
              )}
            </section>
          )}
        </>
      )}
    </section>
  );
}
