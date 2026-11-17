# AFDS 团队协作、远程执行与生产部署实施计划

> 执行代理使用 `superpowers:executing-plans` 逐项实施。平台依赖采用真实 PostgreSQL、独立 Worker 与容器测试验证。

## 目标、架构与技术栈

提供项目成员权限、实时协作、远程 Worker、预算约束模型选择及可恢复的团队部署。沿用模块化 FastAPI 与独立 Worker，增加 PostgreSQL 方言、应用层账号、持久事件推送和节点能力匹配。技术栈增加 Psycopg、Argon2 密码哈希及 Linux/Docker 部署。

## 需求与约束

需求、平台假设和全局约束见[总计划](2026-11-16-product-completion.md)。T1 前完成 A4、S4，复用共享画布、合成与沙箱协议。团队平台按 PostgreSQL 17 与 Linux 容器编排设计，镜像及新增依赖在实际兼容性验收时固定版本和内容身份。

## 文件职责

| 文件 | 操作与责任 |
| --- | --- |
| `backend/app/config.py`、`db.py`、`project_engine.py`、`execution/queue.py`、`budget.py` | 扩展数据库方言、原子修订、租约和预算锁 |
| `backend/app/identity_schema.py`、`identity_service.py`、`identity_api.py`、`access.py`、`admin.py` | 新增应用账号、会话、项目授权和管理入口 |
| `backend/migrations/versions/0009_identity.py` | 新增用户、会话、成员关系、协作操作与临时 presence 存储 |
| `backend/app/main.py`、全部项目及全局 `*_api.py` | 接通显式的权限分类和当前操作者 |
| `backend/app/collaboration_schema.py`、`collaboration_service.py`、`collaboration_api.py` | 新增操作级合并、事件补拉、在线状态与撤销 |
| `frontend/src/SessionProvider.tsx`、`ProjectMembers.tsx`、`useProjectEvents.ts`、`useCanvasBoard.ts`、`api.ts` | 新增会话、成员、同步与冲突恢复 |
| `backend/app/worker_registry.py`、`execution/worker.py`、`operations_service.py` | 新增节点注册、能力、排空与诊断 |
| `backend/migrations/versions/0010_worker_nodes.py` | 扩展节点能力、状态与任务分配索引 |
| `backend/app/routing_schema.py`、`routing_service.py`、`routing_api.py`、`provider_settings.py` | 新增候选连接、路由策略与决策 |
| `backend/migrations/versions/0011_provider_candidates.py` | 新增候选连接及版本引用 |
| `frontend/src/ModelRouting.tsx`、`ProviderSettings.tsx`、`OperationsDashboard.tsx`、`JobList.tsx` | 展示候选、路由理由、执行节点与失败情况 |
| `deploy/Dockerfile`、`deploy/compose.yaml`、`deploy/worker.compose.yaml`、`deploy/nginx.conf.template`、`deploy/.env.example` | 新增团队镜像、编排、远程节点模板与反向代理 |
| `docs/DEPLOYMENT.md`、`docs/TEAM.md` | 新增安装、权限、节点、备份恢复和故障处理说明 |
| `backend/tests/test_postgres_concurrency.py`、`test_access.py`、`test_collaboration.py`、`test_worker_registry.py`、`test_routing.py` | 新增并发、权限、协作、节点和路由回归 |
| `frontend/tests/session.test.tsx`、`collaboration.test.tsx`、`routing.test.tsx`、`tests/e2e/team.spec.ts`、`tests/e2e/team-deployment.spec.ts` | 新增应用账号及真实部署验收 |

## 重点情况

1. SQLite 序列化事务迁移到 PostgreSQL 后，修订、预算和幂等仍有原子保证。T1、T5。
2. 非成员枚举项目、查看任务、下载媒体、读取技能源码或修改全局连接。T2。
3. 两名用户编辑同一节点、撤销他人后续修改、断线重连和撤销成员权限。T3。
4. Worker 租约过期后回写旧结果，以及共享存储不可用。T4。
5. 模型费用未知、币种不同、提交结果未知及团队部署恢复。T5、T6。

## T1 PostgreSQL 与并发事务

### 接口

- `Settings.database_url` 接受原 SQLite URL 和 `postgresql+psycopg` URL。SQLite 连接选项和 PRAGMA 仅在对应方言使用。
- `Database.transaction()` 保持既有上下文管理器接口，根据方言采用正确的事务开始方式。
- `ProjectEngine.load(connection, project_id, *, for_update=False)` 在 PostgreSQL 写事务中取得项目行锁，`save()` 的 revision 条件写入提供额外竞争检查。
- `Queue.claim(owner)` 在 PostgreSQL 使用锁定领取与 `SKIP LOCKED`，预算预留和项目修改在同一事务中序列化。

- [x] 添加失败测试 `test_two_mutations_commit_one_revision`、`test_two_workers_claim_once`、`test_concurrent_budget_reservations_respect_limit`。两个相同 base revision 的写入只有一个成功，单任务只有一个租约，预算预留总额不超过上限。
- [x] 对真实 PostgreSQL 数据库执行 `test_postgres_concurrency.py`，同时对 SQLite 执行同类用例，记录当前方言与事务行为缺失。
- [x] 实现方言设置、连接、迁移和事务。逐项检查 Alembic、JSON 投影、主键自增、唯一约束、金额累加及时间字段在两种数据库上的行为，保留精确微单位计算。
- [x] 将所有写事务入口接入项目行锁或同等原子条件，包括队列入队、批准、预算、恢复、清理、选片和 Worker 回写。外部服务调用放在持锁事务之外。
- [x] 验证并发幂等冲突返回已存在任务或明确的 409，测试事务回滚清除失败的预算预留和部分元数据。执行旧工程迁移及完整数据库回归后精确提交。

SQLAlchemy 的 PostgreSQL 行锁和 `SKIP LOCKED` 支持作为领取与写入实现依据。[SQLAlchemy 2.0 查询构造](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.Select.with_for_update)

## T2 应用账号、项目权限与操作者归属

### 接口与角色

- `Principal` 包含 `user_id`、`session_id`、`is_admin` 和运行模式。
- `require_project_access(request, project_id, action) -> Principal` 位于 `access.py`，在 HTTP 及事件入口统一校验。
- 项目角色为 owner、editor、reviewer、viewer。owner 管理成员和预算，editor 编辑和提交项目政策允许的任务，reviewer 提交审片与选片批准，viewer 查看和下载项目资料。角色动作枚举写入权限矩阵并用于测试。
- 全局连接和节点管理由管理员执行，项目所有者从已授权候选中选择可用模型。导入技能包及源码按项目授权，内置技能通过公共只读定义提供。
- 登录会话采用随机不透明令牌，数据库保存其摘要，密码使用 Argon2id。生产部署使用 HttpOnly、Secure、SameSite Cookie 与写请求 CSRF 校验。
- `python -m app.admin create-user` 通过交互式输入建立首位管理员。旧项目通过 `claim-local-projects --owner <username>` 明确分配归属。

- [ ] 添加 `test_role_action_matrix` 和 `test_route_inventory_requires_access_policy`。遍历 HTTP 路由，除健康、版本、登录及必要静态入口外，每条路由有权限分类。跨项目读取、导出、任务恢复、源码预览及下载均验证拒绝。
- [ ] 添加 `test_last_owner_cannot_be_removed_concurrently`、会话轮换/注销、CSRF、登录限速和旧项目归属测试，执行后记录失败。
- [ ] 实现 0009 迁移、账号服务、会话与授权依赖。将操作者 ID 传入 Engine、计划、预算审批和任务溯源。按角色授权成员变更，并记录审计事件。
- [ ] 接通登录、注销、成员管理和权限反馈 UI。本机模式映射明确的本机主体，团队部署要求有效会话，团队设置随私有部署配置提供。
- [ ] 验证权限撤销对后续请求、事件连接和排队任务的影响，Worker 在启动前复核任务的当前执行权限。已提交远端任务沿用当前取消与计费状态说明。
- [ ] 执行角色矩阵及多浏览器流程，核对导出包的项目内容、操作者展示与私有账号信息边界，更新 `docs/TEAM.md` 后精确提交。

密码哈希使用受维护库的校验和参数升级入口，并按部署资源限制登录并发。[argon2-cffi 用法](https://argon2-cffi.readthedocs.io/en/stable/howto.html)

## T3 实时协作、冲突与操作级撤销

### 接口

- `WorkspaceOperation` 具有 `operation_id`、`entity_id`、`expected_entity_version`、`kind` 和经过校验的值。首批包括移动节点、编辑便签、增删物料及连接。
- `WorkspaceService.apply_operations(project_id, operations, actor) -> WorkspaceSnapshot` 在事务中提交最多 64 个操作，重复 operation ID 返回原结果。
- 共享画布继续维护全局 `workspace_revision`，同时保存实体版本。不同对象的旧整体修订可合并，同一对象版本不符时返回 409 与该对象当前版本。
- `GET /projects/{id}/events/stream` 使用 SSE，从持久事件游标补拉，首版数据库轮询间隔 500 ms，心跳 15 秒，每页最多 100 条。
- `PUT /projects/{id}/presence` 保存有期限的在线状态，2 秒更新、15 秒失效。显示用户及正在编辑对象。
- `undo_operation(project_id, operation_id, actor)` 提交有条件的逆操作，校验当前对象版本及相关引用。

- [ ] 添加失败测试 `test_disjoint_node_edits_merge`、`test_same_node_edit_conflicts`、`test_undo_preserves_other_user_edits`。断线期间持久事件按游标补齐，重复事件应用具有幂等结果。
- [ ] 执行定向测试，确认现有整份画布保存缺少对象级合并时失败。
- [ ] 实现操作记录、实体版本、presence 过期和 SSE 短事务读取。成员或会话失效时关闭事件连接，重新连接先检查权限。超出事件保留窗口时返回重新装载快照指令。
- [ ] 前端接通增量事件和未提交草稿。拖动过程中显示临时编辑状态，结束后提交位置，撤销作用于本人的已提交操作。影片内容编辑继续使用 Engine 的项目修订与预览审批。
- [ ] 在两个真实浏览器上下文与两个 API 实例上验证同步、冲突选择、断网恢复及撤权。隔离测试网络下，提交至另一浏览器可见的目标为 2 秒内，记录实测延迟与运行环境。
- [ ] 执行协作回归及原画布键盘、连线、撤销和恢复测试，精确提交。

## T4 节点能力与远程 Worker

### 接口

- `WorkerNode` 包含 ID、标签、能力、并发槽位、心跳、运行版本、排空状态和共享存储检查结果。
- `WorkerRegistry.register(node_info) -> WorkerNode` 与 `eligible(node, job) -> bool` 位于 `worker_registry.py`。
- Worker CLI 增加节点标签、允许的 operation/能力和并发槽位参数，`Queue.claim()` 按已注册能力及槽位领取。
- 可信远程节点通过内部网络连接 PostgreSQL，并挂载同一逻辑 Asset Store。上传暂存区和不可变素材属于该共享命名空间，节点临时缓存单独管理。
- 脚本控制节点具备容器运行能力，其任务容器使用 S3 策略。管理入口固定操作权限和配置来源。

- [ ] 添加 `test_worker_capability_filters_claim`、`test_stale_worker_cannot_publish` 和 `test_missing_shared_storage_blocks_claim`。不匹配的节点不能领取，旧 lease/attempt 回写失败，单任务输出只注册一次。
- [ ] 在真实 PostgreSQL 上启动两个独立 Worker 节点并执行竞争测试，记录失败。
- [ ] 实现 0010 迁移、注册/心跳、排空、能力检查及原子槽位分配。启动时写入并读取共享存储探针，路径依旧经过 storage-root 包含检查。
- [ ] 在运行概览展示节点、能力、当前任务、最后心跳和不可用原因。远端恢复沿用已提交任务的持久状态，脚本启动未知时进入重新审批流程。
- [ ] 终止一个节点，验证排队任务由合格节点继续执行，旧节点复活后受到租约围栏约束。执行第二个独立运行环境的素材读取与最终出片，记录节点拓扑和挂载方式。
- [ ] 更新远程节点模板与部署说明，完成节点和 Worker 回归后精确提交。

## T5 预算约束的模型路由

### 接口

- `ProviderCandidate` 包含稳定连接 ID、能力、不可变配置版本、模型、币种、估算费用、质量等级及其人工/测评来源、优先级和健康状态。
- `RoutingPolicy` 支持 `preferred`、`lowest_estimated_cost`、`quality_first`、`balanced`。资格筛选先检查能力、项目授权、同币种预算、已知/未知费用政策及可用状态。
- `RouteDecision` 包含选择的连接和模型版本、候选及淘汰理由、估算依据、政策摘要、预算预留和来源修订。
- `RoutingService.preview(project_id, capability, request) -> RouteDecision` 和 `reserve(connection, apr, decision) -> dict` 定义在 `routing_service.py`。
- `balanced` 采用项目设定的最低质量等级、延迟上限、优先级与估算费用进行确定性排序，缺少质量或延迟观测的候选按政策明确处理。
- 现有单连接配置迁移为每类默认候选，旧调用入口解析到该默认 ID。

- [ ] 添加 `test_routing_filters_capability_and_currency`、`test_unknown_cost_requires_policy`、`test_two_routes_share_budget_atomically`、`test_uncertain_submission_preserves_route`。候选排序结果可复现，预算不足时在请求提交前失败。
- [ ] 执行路由失败测试，确认当前单连接实现与预算竞争缺少目标行为。
- [ ] 实现 0011 迁移、候选注册、不可变连接版本和政策选择。保持密钥只在服务端解密，APR 保存可解释的选择与来源摘要。
- [ ] 将路由接入规划、图像、视频、语音、转写和审片的实际请求入口。预览固定候选范围和费用上界，入队时重新校验并原子预留。
- [ ] 实现提交前的候选故障处理。出现远端提交意图或结果未知后保留当前路由与预留，使用既有人工对账/恢复入口继续处理。新增请求必须拥有可追踪的请求身份和预算记录。
- [ ] 完成模型候选管理、项目政策和任务路由理由 UI，使用本机契约服务覆盖选择、拒绝、超时与恢复。更新费用和路由说明后精确提交。

## T6 生产部署、备份恢复与综合交付

### 接口和运行布局

- Compose 提供反向代理、静态前端、API、PostgreSQL 和媒体 Worker，脚本控制节点单独配置能力和容器运行入口。
- 前端与 API 使用同源 HTTPS，代理传递请求 ID 和 SSE，生产 Cookie、CSRF 和可信代理配置经过启动校验。
- 模板中使用占位配置，实际数据库凭据、会话及供应商密钥经部署平台提供，发布清单记录镜像身份和迁移版本。
- 运行健康区分存活、数据库迁移、Worker 就绪、存储和容器能力。部署升级先生成可恢复备份，再应用 Alembic。
- 系统备份保存数据库与不可变素材一致性信息，私有部署密钥按独立恢复步骤处理。工程 ZIP 继续提供创作内容的可移植恢复。

- [ ] 添加 `team-deployment.spec.ts`，从干净数据目录完成启动、管理员登录、成员授权、双客户端协作、远程任务、多轨出片与工程包恢复。未登录访问受保护入口返回 401。
- [ ] 先执行部署冒烟测试，记录镜像、服务和配置尚未就绪的失败。
- [ ] 实现 Dockerfile、Compose、远程 Worker 模板和反向代理。明确挂载路径、启动依赖、迁移作业、健康检查、TLS 输入、SSE 超时和非 root 运行用户。
- [ ] 在隔离 Linux/Docker 环境执行冷启动、服务重启、迁移、数据库/素材备份与恢复演练。恢复后核对用户权限、项目 revision、资产哈希及成片。跨主机部署补充共享存储挂载检查及第二节点证据。
- [ ] 执行安全与负载用例，覆盖越权、路径、CSRF、成员撤销、5 个编辑客户端和 2 个 Worker 的竞争。记录硬件、片段规格、节点数、同步延迟、队列耗时与失败率。
- [ ] 完整运行 PostgreSQL/Docker 集成测试、后端、Ruff、前端 lint/Vitest/build、全部 Playwright 和实际媒体检查。编写 DEPLOYMENT/TEAM 使用指南，更新 API、README、PROGRESS 与 `.ai/`，逐项列出未具备外部条件的验收项目后精确提交。

## 发布核查

运行产物、数据库、导入包、私有配置、凭据、依赖、缓存及生成媒体由本地或部署环境管理。按既有授权发布相关源码、测试、镜像构建与公开使用说明，逐一核对远端提交和路径。演练证据使用脱敏摘要，并准确说明实际验证的节点拓扑与服务范围。

