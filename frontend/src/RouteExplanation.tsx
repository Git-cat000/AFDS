import { rejectionLabels, type RouteDecision } from "./routing-types";

export function RouteExplanation({ decision }: { decision: RouteDecision }) {
  const selected = decision.evaluations.find(
    (value) => value.candidate_id === decision.selected_id,
  );
  return (
    <div className="route-explanation">
      <p>
        选择 {decision.selected_id}
        {selected && ` · ${selected.model} · v${selected.config_revision}`}
      </p>
      <p>
        预估费用 {selected?.estimated_cost ?? "未知"} {selected?.currency || ""}{" "}
        · 项目版本 {decision.source_revision}
      </p>
      {decision.fallback_from && (
        <p>
          备用切换自 {decision.fallback_from} · {decision.fallback_reason}
        </p>
      )}
      <details>
        <summary>查看候选与筛选理由</summary>
        {decision.evaluations.map((value) => (
          <p key={value.candidate_id}>
            {value.candidate_id} · {value.model} ·{" "}
            {value.estimated_cost ?? "费用未知"} {value.currency}
            {value.rejections.length
              ? ` · ${value.rejections.map((reason) => rejectionLabels[reason] || reason).join("、")}`
              : " · 符合条件"}
          </p>
        ))}
      </details>
    </div>
  );
}
