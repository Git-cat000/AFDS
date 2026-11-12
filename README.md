# AFDS · AI Film Direction System

本地影视工程工作室，支持可配置的 LLM、视频、图像和语音服务。通过分镜、持久任务队列、版本记录和 FFmpeg 完成从创作到 MP4 的流程。内置 Mock 模式可离线生成带 `DEMO / MOCK` 标识的测试视频。

v0.5 增加工程包恢复为新副本、Worker 在线状态、运行诊断、任务指标与素材流向图。保留角色与世界状态、连续性影响分析、受控语义编辑、关键帧审片、多音轨/字幕成片、OTIO 导出、四类模型设置、ComfyUI 与预算功能。默认可离线使用 Mock；画面输出 H.264，配置音轨时混合 AAC 48 kHz 立体声。

## 环境

- Python 3.11+，本次验证版本为 3.12.7。
- Node.js 22.12+ 或 24 LTS，本次验证版本为 24.15.0。
- FFmpeg 与 ffprobe，需包含 libx264 编码器。
- 首次安装依赖需要网络，安装完毕后的 Mock 工作流可以离线运行。

克隆仓库后，请执行下列安装步骤。FFmpeg 优先从 PATH 或 `.env` 指定路径查找，也支持工作区 `.tools/ffmpeg/**/ffmpeg.exe` 和 `ffprobe.exe`。

## Windows 安装与启动

在项目根目录运行。

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements.lock
.venv\Scripts\python.exe -m pip install --no-deps -e backend
cd frontend
npm ci
cd ..
Copy-Item .env.example .env
.venv\Scripts\python.exe scripts\dev.py --check
.\scripts\dev.ps1
```

打开 `http://127.0.0.1:5173`。API 文档位于 `http://127.0.0.1:8000/docs`。启动器会迁移数据库并启动 API、独立 Worker 和 Vite，按 Ctrl+C 关闭进程树。

可选安装 FFmpeg 后在 `.env` 指定完整路径。

```dotenv
AFDS_FFMPEG=C:/tools/ffmpeg/bin/ffmpeg.exe
AFDS_FFPROBE=C:/tools/ffmpeg/bin/ffprobe.exe
```

## Linux / macOS 安装与启动

先通过系统包管理器安装 FFmpeg，然后运行下列命令。跨平台脚本已提供，本次实际执行环境为 Windows。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.lock
.venv/bin/python -m pip install --no-deps -e backend
cd frontend && npm ci && cd ..
cp .env.example .env
sh scripts/dev.sh
```

## 分别启动三个进程

```powershell
# 终端 1，从项目根目录启动
.venv\Scripts\python.exe scripts\dev.py --check
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 终端 2
.\scripts\run_worker.ps1

# 终端 3
cd frontend
npm run dev
```

所有相对数据库和媒体路径均以项目根目录为基准。直接使用 Alembic 时运行 `.venv\Scripts\alembic.exe -c backend\alembic.ini upgrade head`。

## 在 Studio 中制作演示

1. 在项目页填写名称与创作简述，点击“创建项目”。
2. 在导演助手中点击“生成分镜建议”。可编辑建议里的动作，再点击“批准并加入分镜”。
3. 点击镜头卡片的“编辑”，修改名称、动作、帧数或景别。预览影响后确认提交。
4. 点击“生成全部镜头”。任务由独立 Worker 执行，在“任务队列”查看进度、错误、取消和重试。
5. 每个镜头出现“可用”后，点击“渲染成片”，随后预览或下载 MP4。
6. 时间轴上下箭头改变镜头顺序。镜头时长在属性窗口按整数帧修改。
7. 修改第三镜头后再次生成，内容相同的第一、第二、第四镜头会复用原任务与素材。

锁定镜头须先解锁才能生成。已批准的素材遇到依赖变化时显示“需审阅”，新生成的候选需要在属性窗口显式授权选择。每次提交记录 revision，旧页面冲突时显示提示，关闭编辑窗口后刷新重试。

## 一键生成可播放演示

此演示显式选用内置 Mock 模型，可直接离线运行。

```powershell
.venv\Scripts\python.exe scripts\demo_pipeline.py
```

脚本使用公开 API 路由、持久 SQLite 和单独 Worker 子进程。输出包含四段五秒视频及约二十秒成片，并验证编码、分辨率、帧率和总帧数。项目在 Studio 中可继续打开。

- 最近一次输出位置记录于 `data/demo/latest.json`。
- 每次演示目录为 `data/demo/<project_id>/`。
- 交付文件包含 `output.mp4`、`project.apr.json`、`manifest.json` 和 `validation.json`。
- 原始镜头与缩略图按 SHA-256 存放于 `data/projects/<project_id>/assets/`。

## 测试与质量检查

```powershell
# 根目录。basetemp 指向专用测试目录，运行时会由 pytest 清理。
New-Item -ItemType Directory -Force data | Out-Null
.venv\Scripts\python.exe -m pytest backend\tests -q --basetemp data\pytest-run
.venv\Scripts\ruff.exe check backend scripts
.venv\Scripts\ruff.exe format --check backend scripts

cd frontend
npm run lint
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

Playwright 会启动隔离测试 API、真实 Worker 和前端，验证创建项目、批准分镜、编辑第三镜头、生成素材、音频导入与字幕、渲染下载、审片证据、工程包导出与恢复、运行概览及刷新恢复。基础截图保存至 `data/studio-desktop.png` 和 `data/studio-mobile.png`；Phase 6 截图为 `data/phase6-{continuity-desktop,timeline-desktop,timeline-mobile,review-desktop}.png`，Phase 7 截图为 `data/phase7-{restore,operations}-{desktop,mobile}.png`。

测试覆盖旧项目迁移、密钥保护、四类 HTTP 协议、任务恢复、防重复提交、媒体文件和浏览器设置流程。模型协议测试使用本机 HTTP 服务，真实服务的模型权限、参数支持与计费需要用户配置后验证。

v0.5 在 Windows 上完成 203 项后端测试、17 项前端测试和 5 项浏览器测试；3 项真实服务验收和 1 项需要 Windows 符号链接权限的测试明确跳过。Ruff、格式检查、ESLint、TypeScript/Vite 构建和启动预检通过。恢复验收从独立数据库与素材目录读取工程包并重新成片；浏览器恢复样片为 H.264 320×180、24 fps、48 帧/2 秒及 AAC 48 kHz 立体声，工程包经原生 OTIO 解析及素材哈希验证。

## 配置模型服务

1. 打开“模型与工具”，选择 LLM、视频生成、图像生成或语音合成。
2. 填写基础 URL（通常带 `/v1`）和 API Key。可先点“测试连接 / 获取模型”，再选择或手动填写模型 ID。
3. 勾选“启用此连接”，保存设置。密钥输入框随后清空，刷新后只显示保存状态。
4. 在“工作室默认模型”选择默认连接，或在项目分镜页单独选择导演 LLM 与视频模型。
5. 外部生成前勾选内容发送与费用授权。服务费用显示为未知，最终费用由服务商决定。
6. “素材库”提供参考图像和语音旁白生成，输出可预览和下载。

| 类别 | 使用的接口 | 配置重点 |
| --- | --- | --- |
| LLM | `POST /chat/completions` | 支持 JSON 输出的模型，服务不支持 JSON mode 时可在高级选项关闭 |
| 视频 | `POST /videos`、`GET /videos/{id}`、`GET /videos/{id}/content` | 提供此兼容协议的第三方服务，时长与尺寸应符合所选模型 |
| 图像 | `POST /images/generations` | 返回 base64 图像或可公开下载的 HTTPS 图像 URL |
| 语音 | `POST /audio/speech` | 服务支持的模型和音色，音色可在高级选项填写 |

OpenAI 官方 Videos API 已于 2026 年 9 月 24 日停止服务，AFDS 的视频入口用于仍提供上述兼容协议的第三方服务。服务使用其他协议时，需要对应适配器。具体状态见[官方视频接口说明](https://developers.openai.com/api/reference/resources/videos)。

连接测试读取 `/models`，验证连接、鉴权与模型列表。部分视频服务未实现此端点，可按服务商说明手动填写模型 ID，生成请求仍须单独验证。LLM、图像和语音请求格式参照[Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[Images](https://developers.openai.com/api/reference/resources/images/methods/generate)和[Speech](https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create)。

### 密钥与连接版本

- API Key 使用本机 `data/secrets/provider.key` 加密后保存于数据库。备份时应同时保存数据库与该密钥文件，并限制本机访问。
- 编辑连接时留空 Key 可沿用已保存值。高级选项可以清除当前连接的 Key。
- 已排队任务绑定提交时的连接版本，旧版本加密凭据用于恢复任务。停用连接可阻止待执行任务调用服务。
- 保存相同配置会复用原版本。API 响应、APR 和 Job 快照均不包含明文密钥。
- 只有显式开启内网选项的连接可以访问本机或内网地址。所有 HTTP 请求有总时限，连接固定到经过校验的 IP，证书验证保留原始主机名。
- `data/`、`.env`、本机密钥、开发记忆和编辑器配置均在 Git 忽略范围内。

### 远端任务恢复

视频任务保存服务商返回的任务 ID。重试已有任务会继续查询结果。提交响应丢失时，系统阻止自动重复提交，可在任务页填写已知的远端视频 ID 恢复。

远端任务失败或需要重新生成时，展开“远端任务恢复与重新生成”，核对服务商状态与账单后，勾选再次计费授权并重新提交。旧远端任务 ID 保留在任务历史中。

当前视频兼容协议没有统一的服务端取消接口。取消会停止本地执行或轮询，界面会提示远端任务可能继续运行和计费。

## ComfyUI 工作流

1. 在视频或图像设置中将“接口协议”切换为 ComfyUI，填写服务器基础 URL，例如 `http://127.0.0.1:8188`。本机服务需在高级选项开启内网访问，无鉴权服务的 Key 可留空。
2. 从 ComfyUI 导出 API 格式工作流，在设置界面导入 JSON。填写便于识别的工作流名称，选择提示词输入和保存输出节点；种子、宽高、帧数和帧率映射可选。
3. 保存后测试连接。AFDS 通过 `/object_info` 核对所需节点、输入字段及输出节点；元数据通过后仍标记为待生成验证，只有首次实际输出媒体校验通过才标记就绪。生成前会再次检查。
4. 启用连接，在工作室选择对应视频模型，或从素材库生成图像。输出进入原有素材存储并验证实际媒体；视频按项目规格规范化。

图像常用历史字段为 `images`；部分视频节点使用 `gifs` 或 `videos`，应按所用节点选择。接口依据 [ComfyUI 官方服务端路由](https://docs.comfy.org/development/comfyui-server/comms_routes)。当前通过历史轮询恢复 `prompt_id`，取消仅停止本地任务，不会调用共享服务器的全局中断或清空队列。可在任务页填写已知 `prompt_id` 恢复图像或视频任务。

当前工作流支持文本生成图像/视频，工作流中的固定参考文件应已存在 ComfyUI 服务端。AFDS 不安装模型或自定义节点，也不自动上传角色参考图。连接检查不代表实际模型已完成生成验收。

## 预算、隐私与费用对账

- 在模型连接高级选项中填写“单次请求预估费用”和币种，用于每次请求的预算预留；留空表示未知。自部署服务可根据实际情况填 `0`。
- 项目“任务队列”中的“预算与隐私”可以设置预算上限、禁止向模型服务发送内容、禁止未知费用。已有费用记录后币种固定，不自动换汇。
- 批量镜头提交会原子地预留费用，超出上限时整批撤销。缓存复用和已有远端任务恢复不重复预留；明确授权重新生成会创建新费用记录。
- 未提交即取消可释放预留。已提交、失败、取消或提交结果不确定的远端请求仍需对账；不把它们自动记为免费。
- 待对账条目可填写服务商账单中的实际费用并确认，包括实际为 `0` 的情况。对账操作有 revision 检查、归属检查和审批记录，每条只可确认一次。
- LLM 调用前持久记录提交意图。API 意外退出时，超过请求超时加 15 秒的恢复窗口后可核对该条目；恢复远端媒体 ID 会保留或重新建立既有费用义务。
- 未知费用无法计入金额上限。即使配置了预估费用，实际收费也可能超过预算；系统记录真实对账金额并提示超支。预估金额不会自动当作实际费用。

历史项目保留原有的逐次内容发送授权方式，可在预算面板进一步收紧策略。模型不可用或配置不匹配时明确报错，由用户选择 Mock，不自动切换付费服务。

## 真实服务验收（可选）

普通测试完全离线，真实 LLM/视频/VLM 验收用例默认显示 `SKIPPED`。当前尚无真实服务凭据或可用 ComfyUI，不能把本机契约测试视为真实出片验收。后续先在设置界面保存连接，再仅在明确允许该服务生成和计费时运行：

```powershell
# 从项目根目录执行，只选择本次允许实际调用的类别。
$env:AFDS_EXTERNAL_ACCEPTANCE = "1"
$env:AFDS_EXTERNAL_KINDS = "llm,video"
$env:AFDS_EXTERNAL_BUDGET = "5"
.venv\Scripts\python.exe -m pytest backend/tests/test_external_acceptance.py -m integration_external -q -rs --basetemp data/external-acceptance
Remove-Item Env:AFDS_EXTERNAL_ACCEPTANCE, Env:AFDS_EXTERNAL_KINDS, Env:AFDS_EXTERNAL_BUDGET
```

验收使用隔离的临时工程和数据库，读取本机已保存连接，密钥不写入源码或输出。预算币种沿用连接设置；远端视频可能继续计费，测试成功与否均应核对服务商账单。

## 源码目录

- `backend/app/schemas.py` 包含 APR 及请求校验。
- `backend/app/project_engine.py` 管理事务、版本、预览、审批和依赖失效。
- `backend/app/execution/` 管理持久队列、租约、心跳、重试和结果发布。
- `backend/app/media.py` 与 `storage.py` 管理真实媒体和内容寻址存储。
- `backend/app/provider_settings.py`、`provider_http.py`、`llm_adapter.py` 和 `remote_media.py` 管理连接、加密凭据和模型协议。
- `frontend/src/` 包含项目、分镜、时间轴、素材、任务与模型页面。
- `docs/API.md` 记录 REST 接口和请求示例。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| Provider 不可用 | 在设置页确认连接已启用、URL 和模型 ID 正确；媒体处理另需 FFmpeg 与 ffprobe |
| 连接测试返回 404 | 检查 URL 是否包含正确版本前缀，以及服务是否实现 `/models` |
| 配置冲突 | 刷新设置页，重新填写修改项后保存 |
| 提交结果不确定 | 在服务商控制台核对任务 ID，通过任务页恢复或显式授权新请求 |
| 任务一直排队 | 确认独立 Worker 正在运行。退出的 Worker 租约默认 30 秒后可恢复 |
| 任务失败 | 在任务页检查错误码，修复依赖、磁盘或输入后重试 |
| revision 冲突 | 关闭旧编辑窗口，刷新项目，重新预览变更 |
| 缓存媒体缺失或损坏 | 从备份恢复不可变素材，或修改镜头规格生成新素材。系统会报告 `CACHE_MEDIA_INVALID` |
| 无法播放旧成片 | 检查 `data/` 是否被移动或删除，素材通过内部 ID 解析 |
| 端口占用 | 关闭此前启动的 AFDS 服务，或分别启动并同步调整 Vite 代理 |
| pytest 临时目录无权限 | 使用 README 中的项目内专用 `--basetemp` |

## 当前边界

- 单用户本机使用，SQLite 单活动媒体任务。服务绑定 `127.0.0.1`。
- 一条连续视频主轨、最多 15 条音轨及字幕。支持排序、时长、音频裁切/增益/静音；多视频叠加、画中画和转场编辑尚未提供。
- JSON 导入恢复结构并重新分配实体 ID，素材需要重新生成；工程 ZIP 可恢复素材与剪辑为独立新副本，见下方流程。
- 最小回滚支持当前最新 revision 的逆向提交，历史素材与任务保留。
- 事件使用持久游标 REST 查询与轮询，尚未提供 SSE。
- 真实服务尚未使用用户凭据进行计费调用验收。当前通过本机契约服务验证接口实现。
- 多人协作、远程 Worker、Docker/PostgreSQL 生产部署属于后续产品化阶段。
- 费用按用户输入的预估值预留，实际金额由用户对账，尚未自动同步服务商账单。
- MockDirector 使用四拍规则，镜头卡片视频带明确测试标识。
- 当前 Starlette 对 httpx TestClient 发出弃用提示，测试功能正常，依赖已锁定。

后续可按具体服务商增加专用协议适配器，并完善自动账单同步和生产部署。

## Phase 6 使用流程

1. 在“角色与连续性”创建角色不变量、地点、世界状态，为镜头关联出场角色和输入/输出状态，按需连接前后镜头。提交前显示受影响镜头；全局变更须批准，已锁定或批准的选中素材会保留并标记待审。
2. “受控镜头修改”支持显式字段修改，或使用已配置的 LLM 将指令转换成受限计划。先查看差异、影响和保护镜头，再批准执行。LLM 不能直接写工程、调用工具或自动生成收费素材。
3. 在“音画时间轴”导入具有使用权的 WAV/MP3/FLAC/Ogg，或选择已生成的语音。导入限 32 MB、最长一小时，由 Worker 检测并转为 WAV。新增音轨，设置片段起点、源起点、持续帧、增益和静音；可同时混合音乐与配音。
4. 添加字幕、节拍/对白标记，或输入 BPM 网格。勾选待对齐的镜头边界，预览后批准时长计划；总时长保持不变。受保护镜头默认不调整，需要另行授权。音轨/字幕编辑只使成片失效，镜头时长变化才使相关素材失效。
5. 渲染后下载 MP4。在“审片与证据”运行本机检查，查看媒体信息和关键帧。若需要 VLM，在 LLM 连接高级选项启用图像审片，并在每次提交时确认发送关键帧、相邻镜头与文本设定及费用授权。
6. 点击“导出 OTIO”或“导出工程包”，在任务页下载。工程 ZIP 包含 APR、原生 OTIO、哈希清单和工程素材；下载前校验归属与完整性，不包含服务密钥或本地内部文件。

APR 当前版本为 `0.2.0`，读取/导入兼容 `0.1.0`。JSON 导入仍为结构导入：重分配实体 ID，清除媒体选择与音频素材关联；工程 ZIP 支持携带素材和剪辑交换，也可按下方流程恢复。

OTIO 使用原生 [OpenTimelineIO](https://opentimelineio.readthedocs.io/en/latest/) 序列化。音频重叠转换为并行轨，裁切及时间位置保留；字幕、增益和静音也保存在 AFDS 元数据中，接收编辑器可能需要手工应用，不能保证所有剪辑软件完整复现。导出最大 2 GB。

本机审片不会伪造语义通过。VLM 是抽样关键帧与文本设定的建议报告，包含证据帧、时间戳和置信度；不代表全时段动作、口型或音画同步已验证，也不会自动重做镜头。当前无真实服务凭据，真实 VLM/语义模型验收仍待完成；可在外部验收类别中显式选择 `vision`。节拍/对白锚点由用户填写或 BPM 网格生成，未提供自动节拍检测或语音转写。连续性工程控制不能保证模型的视觉一致性。

## 工程包恢复与本机运行概览

1. 在原项目点击“导出工程包”，任务完成后下载 ZIP。仅支持 AFDS 当前 `export_version=0.2.0` 格式，压缩文件及解压总量均不超过 2 GB。
2. 返回项目列表，在“从工程包恢复”选择 ZIP 并填写新副本名称，确认恢复。页面会进入新项目的任务队列；独立 Worker 校验清单、素材哈希、媒体与 OTIO 后发布。
3. 恢复保留已选素材、锁定/批准状态、音轨、字幕和标记，重分配工程及实体 ID，可直接重新渲染。原工程保持不变。网络响应不确定时保持文件与名称不变重试，以复用同一恢复请求。
4. 恢复不带入连接密钥、旧任务/修订/审片历史或费用账本。新副本预算为零，禁止远端内容发送与未知费用；原工程的历史账单仍需对账。需要新生成时应先设置新副本的预算与隐私策略。
5. 恢复失败或取消时，任务及空的新副本仍可查看；修复输入后可重试或归档该副本。恢复期间编辑目标工程会使发布因版本冲突而停止。

侧栏“运行概览”显示数据库、Worker、FFmpeg/ffprobe、OTIO 和剩余磁盘空间；不选择工程也能使用。Worker 每 5 秒更新空闲心跳，超过 15 秒无心跳显示离线。该页面每 5 秒刷新，也可手动刷新，不调用外部模型。

选择工程后可查看所有任务状态计数、最近 200 项任务的耗时统计、已登记素材占用和预算。素材流向图最多展示最近 50 项任务和 100 个输入素材，实线表示素材流向，虚线表示显式任务依赖，并提供文字关系表；截断会明确提示。素材关系不表示排队阻塞，图外的旧素材会显示为已有素材。运行诊断不执行取消、删除或重新生成。

工程 ZIP 是可迁移的创作备份。完整保留本机设置和账本仍需另行安全备份数据库与密钥文件。中断恢复可能留下未登记的不可变文件，系统不会自动清理，以免删除其他任务使用的素材。
