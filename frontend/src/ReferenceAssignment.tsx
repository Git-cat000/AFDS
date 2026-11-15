import { useState } from "react";
import { api, mediaUrl } from "./api";
import type { ChangeSet, Preview, Project, Shot } from "./types";

export function ReferenceAssignment({
  project,
  assetId,
  shotId,
  busy,
  run,
  complete,
  initialRole = "reference_image",
  remove = false,
}: {
  project: Project;
  assetId: string;
  shotId: string;
  busy: boolean;
  run: (action: () => Promise<unknown>) => Promise<void>;
  complete: () => void;
  initialRole?: "reference_image" | "start_image" | "end_image";
  remove?: boolean;
}) {
  const shot = project.shots.find((item) => item.id === shotId);
  const asset = project.assets.find(
    (item) => item.id === assetId && item.media_type === "image",
  );
  const [source, setSource] = useState({
    revision: project.revision,
    references: shot?.reference_images || {},
  });
  const [role, setRole] =
    useState<keyof NonNullable<Shot["reference_images"]>>(initialRole);
  const [proposal, setProposal] = useState<{
    body: ChangeSet;
    preview: Preview;
  }>();
  const stale = source.revision !== project.revision;
  const differentInput = remove && source.references[role] !== assetId;
  const root = `/projects/${project.project_id}`;
  if (!shot || !asset)
    return <p role="alert">当前图像或镜头已不可用，请重新选择。</p>;
  return (
    <section className="reference-assignment">
      <p>
        将图像关联到镜头 <strong>{shot.title}</strong>
      </p>
      <img
        src={mediaUrl(project.project_id, asset.id)}
        alt={asset.provenance.name || "所选图像"}
      />
      <label>
        图像用途
        <select
          aria-label="图像用途"
          disabled={busy || remove}
          value={role}
          onChange={(e) => {
            setRole(e.target.value as typeof role);
            setProposal(undefined);
          }}
        >
          <option value="reference_image">参考图</option>
          <option value="start_image">首帧图</option>
          <option value="end_image">尾帧图</option>
        </select>
      </label>
      {stale && <p role="alert">镜头已有新版本，请载入最新设置后重新预览。</p>}
      {differentInput && (
        <p role="alert">该镜头当前已使用其它图像，请重新选择连线。</p>
      )}
      {stale && (
        <button
          disabled={busy}
          onClick={() => {
            setSource({
              revision: project.revision,
              references: shot.reference_images || {},
            });
            setProposal(undefined);
          }}
        >
          载入最新镜头设置
        </button>
      )}
      {proposal ? (
        <>
          <p>
            受影响镜头 {proposal.preview.affected_shot_ids.length} 个 ·
            受保护镜头 {proposal.preview.protected_shot_ids?.length || 0} 个
          </p>
          <pre>
            {JSON.stringify(proposal.body.operations[0].value, null, 2)}
          </pre>
          <p className="muted">生成时使用工作流中对应的图像输入映射。</p>
          <button
            disabled={busy || stale || differentInput}
            className="primary"
            onClick={() =>
              void run(async () => {
                await api(`${root}/changesets/commit`, proposal.body);
                complete();
              })
            }
          >
            {remove ? "批准移除镜头输入" : "批准关联到镜头"}
          </button>
          <button onClick={() => setProposal(undefined)}>返回设置用途</button>
        </>
      ) : (
        <button
          disabled={busy || stale || differentInput}
          onClick={() =>
            void run(async () => {
              const references = { ...source.references };
              if (remove) delete references[role];
              else references[role] = asset.id;
              const body: ChangeSet = {
                base_revision: source.revision,
                approved: true,
                reason: "Reuse media as shot reference",
                operations: [
                  {
                    op: "set",
                    entity_type: "shot",
                    entity_id: shot.id,
                    field: "reference_images",
                    value: references,
                  },
                ],
              };
              const preview = await api<Preview>(
                `${root}/changesets/preview`,
                body,
              );
              setProposal({ body, preview });
            })
          }
        >
          {remove ? "预览移除图像关联" : "预览图像关联"}
        </button>
      )}
    </section>
  );
}
