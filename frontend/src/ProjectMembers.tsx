import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

const roles = {
  owner: "所有者",
  editor: "编辑者",
  reviewer: "审片者",
  viewer: "查看者",
};
type Role = keyof typeof roles;
type Member = {
  id: string;
  username: string;
  display_name: string;
  role: Role;
};

export function ProjectMembers({
  projectId,
  canManage,
}: {
  projectId: string;
  canManage: boolean;
}) {
  const queries = useQueryClient();
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<Role>("editor");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const members = useQuery({
    queryKey: ["project-members", projectId],
    queryFn: () => api<Member[]>(`/projects/${projectId}/members`),
    refetchInterval: 5000,
  });

  async function change(action: () => Promise<unknown>) {
    setPending(true);
    setError("");
    try {
      await action();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "成员更新失败");
    } finally {
      await members.refetch();
      await queries.invalidateQueries({
        queryKey: ["project-access", projectId],
      });
      setPending(false);
    }
  }
  async function invite(event: FormEvent) {
    event.preventDefault();
    await change(async () => {
      await api(`/projects/${projectId}/members`, { username, role });
      setUsername("");
    });
  }

  const ownerCount =
    members.data?.filter((member) => member.role === "owner").length || 0;
  return (
    <section className="project-members" aria-label="项目成员">
      {(error || members.error) && (
        <p role="alert">{error || members.error?.message}</p>
      )}
      <div className="member-list">
        {members.data?.map((member) => {
          const lastOwner = member.role === "owner" && ownerCount === 1;
          return (
            <article key={member.id}>
              <div>
                <strong>{member.display_name}</strong>
                {member.display_name !== member.username && <small>{member.username}</small>}
                {canManage && lastOwner && <small>至少保留一名所有者</small>}
              </div>
              <select
                aria-label={`${member.display_name}的角色`}
                value={member.role}
                disabled={!canManage || pending || lastOwner}
                onChange={(event) =>
                  void change(() =>
                    api(
                      `/projects/${projectId}/members/${member.id}`,
                      { role: event.target.value },
                      "PUT",
                    ),
                  )
                }
              >
                {Object.entries(roles).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
              {canManage && (
                <button
                  disabled={pending || lastOwner}
                  onClick={() =>
                    void change(() =>
                      api(
                        `/projects/${projectId}/members/${member.id}`,
                        {},
                        "DELETE",
                      ),
                    )
                  }
                >
                  移除 {member.display_name}
                </button>
              )}
            </article>
          );
        })}
      </div>
      {canManage && (
        <form
          onSubmit={(event) => void invite(event)}
          className="member-invite"
        >
          <label>
            成员用户名
            <input
              value={username}
              required
              maxLength={64}
              autoComplete="off"
              disabled={pending}
              onChange={(event) => setUsername(event.target.value)}
            />
          </label>
          <label>
            新增成员角色
            <select
              aria-label="新增成员角色"
              value={role}
              disabled={pending}
              onChange={(event) => setRole(event.target.value as Role)}
            >
              {Object.entries(roles).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <button className="primary" disabled={pending || !username.trim()}>
            添加成员
          </button>
        </form>
      )}
    </section>
  );
}
