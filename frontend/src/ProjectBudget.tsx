import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { ProjectBudgetState } from "./types";

type Run = (action: () => Promise<unknown>) => Promise<void>;

export function ProjectBudget({
  projectId,
  run,
  busy,
}: {
  projectId: string;
  run: Run;
  busy: boolean;
}) {
  const query = useQuery({
    queryKey: ["budget", projectId],
    queryFn: () => api<ProjectBudgetState>(`/projects/${projectId}/budget`),
    refetchInterval: 2000,
  });
  if (query.error) return <p role="alert">{query.error.message}</p>;
  if (!query.data) return <p className="muted">正在读取项目预算…</p>;
  return (
    <BudgetForm
      key={`${projectId}:${query.data.revision}`}
      data={query.data}
      projectId={projectId}
      run={run}
      busy={busy}
    />
  );
}

function BudgetForm({
  data,
  projectId,
  run,
  busy,
}: {
  data: ProjectBudgetState;
  projectId: string;
  run: Run;
  busy: boolean;
}) {
  const [limit, setLimit] = useState(data.budget_limit);
  const [currency, setCurrency] = useState(data.currency);
  const [remote, setRemote] = useState(data.allow_remote);
  const [unknown, setUnknown] = useState(data.allow_unknown_cost);
  return (
    <section className="panel budget-panel">
      <h2>预算与隐私</h2>
      <div className="budget-summary">
        <span>
          已对账费用
          <strong>
            {data.spent} {data.currency}
          </strong>
        </span>
        <span>
          预留 / 待对账
          <strong>
            {data.reserved} {data.currency}
          </strong>
        </span>
        <span>
          未知金额记录<strong>{data.unknown_count} 笔</strong>
        </span>
      </div>
      {data.over_budget && (
        <p role="alert" className="settings-error">
          已对账与预留费用超过预算，请核对账单并调整预算。
        </p>
      )}
      <div className="editor-grid">
        <label>
          项目预算上限
          <input
            aria-label="项目预算上限"
            type="number"
            min="0"
            step="0.000001"
            value={limit}
            onChange={(e) => setLimit(e.target.value)}
          />
        </label>
        <label>
          预算币种
          <input
            aria-label="预算币种"
            maxLength={3}
            disabled={data.entries.length > 0}
            value={currency}
            onChange={(e) => setCurrency(e.target.value.toUpperCase())}
          />
        </label>
      </div>
      <label className="check-label">
        <input
          type="checkbox"
          checked={remote}
          onChange={(e) => setRemote(e.target.checked)}
        />
        允许本项目向配置的模型服务发送内容
      </label>
      <label className="check-label">
        <input
          type="checkbox"
          checked={unknown}
          onChange={(e) => setUnknown(e.target.checked)}
        />
        允许未知费用请求（每次仍需授权，金额无法计入预算上限）
      </label>
      <p className="muted">
        预留按设置中的单次预估费用计算。预估不保证最终账单；已提交、失败或取消的远端请求需核对实际费用。Mock
        始终免费。
      </p>
      <button
        disabled={busy || !limit}
        onClick={() =>
          void run(() =>
            api(
              `/projects/${projectId}/budget`,
              {
                base_revision: data.revision,
                budget_limit: limit,
                currency,
                allow_remote: remote,
                allow_unknown_cost: unknown,
              },
              "PUT",
            ),
          )
        }
      >
        保存预算与隐私
      </button>
      <details>
        <summary>费用明细与对账（{data.entries.length}）</summary>
        {!data.entries.length && <p className="muted">尚无模型费用记录。</p>}
        {data.entries.map((entry) => (
          <ChargeRow
            key={entry.id}
            entry={entry}
            projectId={projectId}
            revision={data.revision}
            run={run}
            busy={busy}
          />
        ))}
      </details>
    </section>
  );
}

function ChargeRow({
  entry,
  projectId,
  revision,
  run,
  busy,
}: {
  entry: ProjectBudgetState["entries"][number];
  projectId: string;
  revision: number;
  run: Run;
  busy: boolean;
}) {
  const [amount, setAmount] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  return (
    <article className="charge-row">
      <p>
        <strong>{entry.provider_id}</strong> ·{" "}
        {{
          reserved: "已预留",
          pending: "待核对账单",
          released: "未提交，已释放",
          settled: "已对账",
        }[entry.state] || entry.state}
      </p>
      <small>
        预估 {entry.estimate ?? "未知"} · 实际 {entry.actual ?? "未知"}{" "}
        {entry.currency}
      </small>
      <small className="charge-reference">关联 {entry.reference}</small>
      {entry.state === "pending" && (
        <div className="charge-reconcile">
          {entry.can_reconcile === false && (
            <p className="muted">
              请求仍在时限内。完成或超时后可核对中断请求的费用。
            </p>
          )}
          <label>
            实际费用
            <input
              aria-label={`实际费用 ${entry.id}`}
              type="number"
              min="0"
              step="0.000001"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          </label>
          <label className="check-label">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            已根据服务商账单核对
          </label>
          <button
            disabled={
              busy ||
              !confirmed ||
              amount === "" ||
              entry.can_reconcile === false
            }
            onClick={() =>
              void run(() =>
                api(`/projects/${projectId}/budget/entries/${entry.id}`, {
                  base_revision: revision,
                  approved: true,
                  actual_amount: amount,
                  currency: entry.currency,
                }),
              )
            }
          >
            确认实际费用
          </button>
        </div>
      )}
    </article>
  );
}
