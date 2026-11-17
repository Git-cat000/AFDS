# AFDS 工程级画布与脚本沙箱实施计划

> 执行代理使用 `superpowers:executing-plans` 逐项实施。每项包含失败测试、实现、验证和精确提交。

## 目标、架构与技术栈

将共享画布纳入项目持久化及工程恢复，为导入脚本提供固定策略的 Linux 容器运行器。画布使用独立数据库聚合和修订，Project Engine 管理其事务。沿用 FastAPI、Pydantic、SQLAlchemy、Alembic、React Flow 和既有脚本审批，新增容器运行协议。

## 需求与约束

需求依据、待确认的部署假设、全局约束和验收基线见[总计划](2026-11-16-product-completion.md)。共享布局包含节点位置、便签、普通连线和隐藏镜头，视口以用户偏好保存。脚本运行策略与本次批准内容绑定。

## 文件职责

| 文件 | 操作与责任 |
| --- | --- |
| `backend/app/workspace_schema.py`、`workspace_service.py`、`workspace_api.py` | 新增画布校验、事务服务和 HTTP 入口 |
| `backend/app/db.py`、`project_engine.py`、`main.py` | 扩展工作台存储、Engine 事务入口和路由 |
| `backend/migrations/versions/0008_workspaces.py` | 新增每项目画布修订与内容 |
| `frontend/src/canvas-board.ts`、`useCanvasBoard.ts`、`api.ts` | 接通服务器保存、浏览器迁移与冲突反馈 |
| `backend/app/exchange.py`、`restore_schema.py`、`restore_service.py` | 增加画布快照、包校验与实体映射 |
| `backend/app/skill_sandbox.py`、`sandbox_protocol.py` | 新增容器控制和结果协议 |
| `deploy/sandbox/Dockerfile`、`deploy/sandbox/entrypoint.py` | 新增固定解释器运行镜像和受信任启动程序 |
| `backend/app/skill_schema.py`、`skill_execution.py`、`skill_api.py`、`skill_process.py` | 扩展策略预览、审批、执行与恢复 |
| `frontend/src/SkillRunner.tsx`、`skill-types.ts`、`SkillRuns.tsx` | 展示执行方式、权限、可用性与结果 |
| `backend/tests/test_workspace.py`、`test_workspace_exchange.py`、`test_skill_sandbox.py` | 新增存储、恢复和隔离回归 |
| `frontend/tests/workspace-sync.test.tsx`、`tests/e2e/workspace-portable.spec.ts`、`tests/e2e/skill-sandbox.spec.ts` | 新增保存、跨浏览器和真实执行入口验收 |

## 重点情况

1. 两个浏览器持有不同旧布局，服务器数据按修订判定，冲突草稿可保留。S1。
2. 布局引用的镜头和素材在恢复时更换 ID，恢复后的引用指向新项目。S2。
3. 恶意脚本试图读主机文件、联网、耗尽进程或填满输出空间。S3。
4. 镜像标签变化、Docker 不可用及启动后失去租约。S3、S4。
5. 旧方案、日志、输出归属及重复批准的兼容行为。S4。

## S1 服务器画布与浏览器迁移

### 接口

- `WorkspaceDocument` 复用 board v2 的共享字段，约束最多 1000 个物料/便签节点、3000 条边、每条便签 8000 字符，位置为有限数值。
- `WorkspaceSnapshot` 包含 `project_id`、`workspace_revision`、`document`。
- `WorkspaceService.read(project_id) -> WorkspaceSnapshot`。
- `WorkspaceService.update(project_id, body, author="local") -> WorkspaceSnapshot`，请求包含 `base_workspace_revision` 和 `document`。
- `ProjectEngine.mutate_workspace(project_id, base_workspace_revision, operation, author)` 管理画布事务，修改共享布局递增画布修订。
- `GET /projects/{id}/workspace` 与 `PUT /projects/{id}/workspace`，初始修订为 0。

- [x] 在 `test_workspace.py` 添加失败测试 `test_concurrent_workspace_save_conflicts`、`test_workspace_save_keeps_apr_revision` 和 `test_workspace_rejects_invalid_references`。相同画布版本的两次竞争保存仅一次成功，另一请求返回 409。布局保存后 APR 的 `revision`、素材状态和有效审批保持可读取。非法素材引用、非有限坐标、悬空边及超限节点返回 422。
- [x] 执行 `.venv/Scripts/python.exe -m pytest backend/tests/test_workspace.py -q --basetemp data/workspace-red`，记录缺失入口或行为的失败。
- [x] 实现 0008 迁移、上述 Schema 和 Engine 服务。工程删除归档时按现有项目可见性处理工作台读取。将服务器空画布与已有布局区分，采用修订 0 的条件保存完成单次本机布局迁移。
- [x] 接通 `useCanvasBoard`，拖动结束提交一次，键盘移动及时保存。视口按用户/工程保存在浏览器偏好中。服务器有内容时展示服务器布局，409 时保留草稿并提供刷新及重试，网络中断时显示未同步状态。
- [x] 执行定向 pytest、`npm --prefix frontend test -- workspace-sync` 和双浏览器持久化流程。断言服务器重启后共享布局存在，本地旧布局再次装载具有幂等结果。
- [x] 精确提交 S1 对应源文件、迁移、测试与接口说明。

## S2 画布工程包与恢复

### 接口

- 工程包 0.3 增加 `workspace.json`，manifest 记录其摘要与快照修订，装载继续接受 0.2。
- `remap_workspace(document, entity_ids) -> WorkspaceDocument` 位于 `workspace_service.py`，`entity_ids` 使用恢复服务的旧 ID 到新 ID 映射。
- 导出快照携带 APR 修订和画布修订，二者从同一数据库快照读取。SQLite 使用显式读事务，PostgreSQL 在首次读取前设置该事务为 `REPEATABLE READ`。

- [x] 在 `test_workspace_exchange.py` 添加失败测试 `test_workspace_restore_remaps_references`、`test_workspace_manifest_tamper_rejected` 和 `test_export_reads_one_database_snapshot`。含镜头、素材副本、便签和普通连线的工程包恢复后，实体映射完整，素材哈希相同。篡改 `workspace.json`、引用包外素材、画布超限均失败。0.2 包恢复为空画布。
- [x] 执行定向测试并记录失败，确认现有恢复保护未被测试夹具绕过。
- [x] 扩展 `exchange.py`、恢复 Schema 和服务。快照提取、成员数量、累计解压大小、路径及摘要检查沿用现有工程包校验。恢复为新项目时调用 `remap_workspace()` 并通过 Engine 保存。
- [x] 在 `workspace-portable.spec.ts` 使用两个独立浏览器上下文导出、恢复并检查便签内容、连线端点和物料位置。增加导出期间并发编辑测试，导出的快照在两个聚合上各自一致。
- [x] 执行定向测试、原有 `test_restore.py`、`test_exchange.py` 与浏览器恢复流程，更新使用说明后精确提交。

## S3 容器执行协议与资源隔离

### 接口

- `ScriptExecutionPolicy` 定义 `mode`、`image_id`、`network`、`cpu_limit`、`memory_bytes`、`pids_limit`、`timeout_seconds`、`policy_version`。
- 本计划的首个容器策略为 `mode=container`、`network=none`、1 CPU、512 MiB 内存、64 个进程、5–300 秒超时。策略提供实际生效能力检查。
- 容器使用只读根文件系统、非 root UID、移除 capabilities、`no-new-privileges`、只读技能包和输入。可写临时区 64 MiB，输出区 32 MiB，均采用受限 tmpfs。
- `run_container(settings, package, script, arguments, context, policy, checkpoint) -> SandboxResult` 位于 `skill_sandbox.py`。
- `SandboxResult` 包含退出状态、截断标记、标准输出/错误、结果文件摘要和实际策略摘要。

- [x] 在 `test_skill_sandbox.py` 添加真实容器测试 `test_container_cannot_read_host_or_connect`、`test_container_limits_output_and_children` 和 `test_container_result_archive_rejects_links`。运行四类解释器示例，验证脚本无法读取主机探针文件、无法访问测试 HTTP 服务、无法修改只读包、超时后子进程与容器停止。填满 tmpfs 时返回有界错误，输出归档拒绝链接成员及路径穿越。
- [x] 建立 `integration_docker` 标记，先执行测试并保存失败原因。Docker 预检区分客户端、引擎、镜像、Linux 容器模式和资源控制器。
- [x] 实现固定镜像与 `run_container()`。运行前解析不可变镜像 ID，采用参数数组调用容器控制入口。只有服务端策略允许的镜像和挂载进入命令。镜像构建时安装解释器依赖，运行上下文明确包含其版本。
- [x] 实现受信任启动程序。脚本在容器内执行，程序在容器存活期间将有界输出档案交回 Worker。Worker 对归档成员、类型、路径、数量、大小、哈希再次校验，结果保存复用现有归属检查。每流 64 KiB 展示、1 MiB 截止及 32 个文件/单文件 8 MiB/总计 32 MiB 沿用现有协议。
- [x] 增加策略拒绝和故障测试。资源限制未生效、镜像身份变化或 Docker 失联时返回类型化错误。取消、租约过期及 Worker 崩溃后的容器按运行标签和启动记录清理，结果未知状态要求新方案。
- [x] 执行实际 Docker 测试并检查残留容器、宿主探针及测试服务访问日志，精确提交实现和验证说明。

Docker 的只读文件系统、网络与资源选项作为实现依据。Rootless 部署还需检查 cgroup 控制器是否实际生效。[运行配置](https://docs.docker.com/reference/cli/docker/container/run/) · [独立网络](https://docs.docker.com/engine/network/drivers/none/) · [tmpfs](https://docs.docker.com/engine/storage/tmpfs/) · [Rootless 资源条件](https://docs.docker.com/engine/security/rootless/tips/)

## S4 审批界面与执行恢复

### 接口

- 现有脚本预览增加 `execution_policy`、`execution_policy_sha256` 和解释器可用性。
- 批准时重新验证包、脚本、上下文、修订、策略和镜像身份，任务请求固定这些字段。
- 本机原有 `local-user` 执行方式保留明确的逐次权限确认。团队模式采用 `container` 策略，执行入口按项目权限决定。
- 既有未执行预览在协议升级后提示刷新，已完成任务继续显示历史执行方式。

- [x] 添加失败测试 `test_script_approval_pins_container_policy`、`test_repeated_container_approval_reuses_job` 和 `test_unavailable_container_preserves_proposal`。相同方案并发批准返回同一任务。策略或镜像更换导致批准失效。容器模式不可用时展示原因并保留草稿。旧任务显示完整历史信息。
- [x] 定向执行新测试及 `test_skill_execution.py`，记录新增失败。
- [x] 修改预览、批准、Worker 分派与前端类型，复用当前脚本启动意图持久化和租约围栏。执行日志和结果记录实际策略。
- [x] 完成桌面与移动端真实浏览器流程，审阅源码和权限、批准容器任务、取消任务、下载归属正确的结果。验证本机旧入口与新容器入口均有明确状态。
- [x] 执行本子计划和生产技能回归测试，更新 `docs/SKILLS.md`、API、PROGRESS 与 `.ai/`，完成 M3 验收后精确提交。

