# 团队部署与系统恢复

本机版继续支持 SQLite 和独立 Worker。团队部署模板使用 PostgreSQL、同源 HTTPS、应用会话和项目权限。模板已提供，本轮 Docker 冷启动及跨运行环境验收按用户要求暂缓；生产使用前需要在目标环境完成下表中的演练。

## 服务和边界

| 服务 | 职责 |
| --- | --- |
| web | 非 root Nginx、静态前端、TLS、API 代理、SSE |
| api | 权限、Project Engine、任务入队、受保护媒体访问 |
| migrate | 独立 Alembic 升级及共享存储命名空间初始化 |
| database | 独立持久卷中的 PostgreSQL 17 |
| worker / worker-2 | 具备能力和槽位的独立媒体节点，第二节点由 profile 启用 |
| 单独的脚本控制器 | 可信 Docker 控制进程，仅领取外部脚本任务 |

只有 web 发布主机端口，默认绑定 127.0.0.1。对外提供服务时显式设置绑定地址，提供对应域名的有效 TLS 证书，并限制主机防火墙入口。API、数据库及 Worker 不发布公网端口。网络允许 Worker 访问配置的模型服务，不能将其视为出站网络沙箱。

API 和 Worker 以 10001:10001 运行，使用同一逻辑素材根目录。共享目录和可选脚本缓存需提前创建并授权该 UID。临时媒体缓存位于有容量上限的 tmpfs，必要时按影片规格调整大小、媒体超时、内存及 CPU。PostgreSQL 镜像使用自己的 postgres 用户运行数据库进程。

## 准备与构建

1. 将 deploy/production.env.example 复制到仓库外的私有配置目录，填写实际路径、HTTPS origin 和部署网段。代理地址必须位于所选网段，不能与其他主机网络冲突。
2. 在私有目录准备数据库密码文件和数据库 URL 文件。URL 使用 postgresql+psycopg 驱动，服务地址为 database:5432，库与用户均为 afds，密码与数据库密码文件一致。保留口令的 URL 编码。实际口令不放入源码、镜像构建参数或公开环境文件。
3. 提供证书链和私钥文件。私有宿主目录仅允许运维访问，挂载文件须允许相应非 root 容器用户读取。Compose 的文件型 secrets 依赖宿主文件权限；不要假定 uid/gid 映射会替你修改宿主权限。
4. 选择经审核的 Nginx 镜像摘要；需要容器脚本控制器时另选 Docker CLI 镜像摘要。构建器只接受 registry/repository@sha256:<digest>，拒绝浮动标签。

```sh
python scripts/build_deployment.py --nginx-image <nginx-image-with-digest>
# 可选：同时构建可信脚本控制器
python scripts/build_deployment.py --nginx-image <nginx-image-with-digest> --docker-cli-image <docker-cli-image-with-digest>
```

构建记录位于本机 data/deployment/images.json，包含源码摘要、上游摘要和实际镜像身份。将 backend/web 身份填入私有环境文件。Dockerfile 中的标签默认值便于开发，正式构建使用上述强制摘要入口。发布前应对部署方选用的上游镜像进行漏洞检查。

```sh
docker compose --env-file <private-environment> -f deploy/compose.yaml config --quiet
docker compose --env-file <private-environment> -f deploy/compose.yaml --profile second-worker up -d
docker compose --env-file <private-environment> -f deploy/compose.yaml ps
docker compose --env-file <private-environment> -f deploy/compose.yaml exec api python -m app.admin create-user owner --admin
```

管理员 CLI 交互读取密码，不通过命令行参数传递。成员账号按 docs/TEAM.md 创建，项目所有者在成员界面授权。模板不会创建演示账号。远程节点使用 deploy/worker.compose.yaml，在可信网络连接数据库并挂载同一素材命名空间；每个节点使用独立稳定 ID。

## 启动与健康

生产配置强制 team 模式、PostgreSQL、Secure Cookie、HTTPS origin 及明确的可信代理 IP/网段。数据库 URL 可由 AFDS_DATABASE_URL_FILE 注入。代理覆盖转发头，Uvicorn 只信任配置的代理来源。不要直接公开 API 端口或将可信代理设为任意来源。

迁移成功后 API 和 Worker 才启动。GET /api/v1/health 表示 API 进程响应；GET /api/v1/ready 在当前 Alembic head、共享命名空间和读写探针通过后返回 200，未就绪返回 503，公开响应不包含内部路径及数据库详情。Worker 健康检查使用自身注册 ID、心跳及存储状态；管理员运行概览提供详细诊断、能力和排空操作。SSE 关闭代理缓冲并使用长读取超时。

部署行为依据 [Docker Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/) 和 [Nginx 代理文档](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)。TLS 终止和跨主机挂载仍需在目标环境验证。

## 外部脚本节点

媒体 Worker 和 API 不挂载 Docker socket。脚本控制器使用 deploy/script-worker.compose.yaml，加入主部署网络并显式配置宿主 socket 的组 ID。控制器拥有 Docker 管理权限，必须属于可信部署边界；导入脚本仅在已批准的受限任务容器内运行。

脚本控制器的共享目录和缓存采用与 Docker 宿主完全相同的绝对路径。Docker bind mount 由 daemon 解析，路径不一致会导致任务输入缺失。先通过 scripts/build_sandbox.py 构建固定策略镜像，再在私有配置填写其实际身份。脚本任务仍需逐次预览和人工批准，运行未知时需要重新审批。

```sh
docker compose --env-file <private-environment> -f deploy/script-worker.compose.yaml up -d
```

脚本节点停止、重建或迁移前先排空。失联控制器的清理只处理对应 Docker daemon 身份的容器。主部署的维护操作还需停止独立脚本控制器及所有远程 Worker。

团队 API 从已注册、在线且存储检查通过的脚本节点解析固定镜像策略，预览和批准均复核节点资格；API 无需 Docker CLI 或 socket。实际控制器在执行前再次验证镜像和策略。外置脚本缓存可与素材根目录分开，暂存包仍受配置根目录及符号链接/重解析路径校验约束。

## 系统备份与恢复

工程 ZIP 用于恢复创作内容副本，不含账号及成本账本。系统快照用于恢复同一部署的数据库、用户权限、修订、队列、费用义务和不可变素材，属于私有运维数据，不应上传 GitHub。

操作前停止 web/API、所有本机及远程 Worker 和脚本控制器。PostgreSQL 保持可连接。CLI 的 --stopped 表示操作者已完成停写，无法替代停写操作。升级前先备份，备份成功后再运行 Alembic。

维护环境需安装项目 Python 依赖；PostgreSQL 模式另需匹配服务器主版本的 pg_dump/pg_restore。基础 API/Worker 镜像未内置这两个维护工具。维护环境通过受保护的内部网络访问数据库，不为备份开放公网数据库端口。

```sh
# 在已配置 AFDS_DATA_DIR / AFDS_DATABASE_URL_FILE 的维护环境中
python -m app.deployment backup /protected-backups/new-snapshot --stopped
```

快照目录必须是素材根目录之外的新目录。SQLite 使用在线备份接口生成单库文件，PostgreSQL 使用 custom dump。快照包含 projects、uploads、skills、state，逐文件记录大小和 SHA-256，最后写入完成清单。缓存、镜像收据、运行日志及 secrets 目录不在快照中。

供应商加密密钥 secrets/provider.key 必须通过独立受保护流程备份；本工具不会读取或复制它。恢复已有加密连接时须在启动前将对应密钥安全放回，不能生成新密钥替代旧密钥。数据库连接口令、TLS 私钥和部署配置同样独立管理。

```sh
# 将目标配置指向新的空素材目录和同类型的空数据库
python -m app.deployment restore /protected-backups/new-snapshot --stopped
python -m app.deployment health
```

恢复先验证清单、路径、哈希、文件数、数据库类型及迁移版本，再写入空目标，拒绝覆盖已有数据。失败时保留恢复中标记，readiness 不通过，需检查并重新选择空目标。恢复后使旧会话失效，保留账号和项目权限；费用未知或已提交任务的义务继续保留。完成独立密钥恢复后启动服务，重新登录并核对权限、素材哈希和影片。升级后需要回滚时，使用匹配旧备份版本的应用恢复到新数据库及目录。

## 验收状态

本机自动化覆盖配置拒绝、迁移/存储就绪、哈希及路径校验、离线 SQLite 系统恢复和清除旧会话。团队浏览器验收脚本为 frontend/tests/e2e/team-deployment.spec.ts，隔离测试配置使用 AFDS_TEST_TEAM_BROWSER=1、AFDS_TEST_DEPLOYMENT_BROWSER=1 及 playwright.deployment.config.ts；只有测试启动器创建 DEMO 账号。

| 项目 | 本轮状态 |
| --- | --- |
| 本机应用回归与本机契约模型 | 98 项前端单元测试、12 项本机浏览器流程通过，覆盖本机模型契约 |
| 五编辑客户端、两个 Windows Worker、SQLite | 独立综合流程通过，含连续撤销/重做、多轨成片和工程恢复；同一编辑账号的五个上下文，不代表 PostgreSQL 并发能力 |
| PostgreSQL 0010/0011 迁移和备份恢复 | 本轮暂缓，需目标环境演练 |
| Linux Compose 冷启动、重启、TLS 和镜像运行 | 用户要求暂缓 Docker 验收 |
| Windows/Linux 跨运行环境出片与故障接管 | 本轮暂缓 |
| 真实模型质量、延迟和实际账单 | 未配置真实凭据，待部署方验证 |
