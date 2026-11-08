# AFDS · AI Film Direction System

本地影视工程工作室，支持可配置的 LLM、视频、图像和语音服务。通过分镜、持久任务队列、版本记录和 FFmpeg 完成从创作到 MP4 的流程。内置 Mock 模式可离线生成带 `DEMO / MOCK` 标识的测试视频。

v0.3 在四类 OpenAI 兼容接口基础上增加 ComfyUI 工作流接入、项目预算、隐私策略与费用对账。默认仍可使用离线 Mock。成片默认输出无声 H.264、1280×720、24 fps，图像和语音素材可单独预览与下载。

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

Playwright 会启动隔离测试 API、真实 Worker 和前端，验证创建项目、批准分镜、编辑第三镜头、生成素材、渲染、下载、刷新后恢复。截图保存至 `data/studio-desktop.png` 和 `data/studio-mobile.png`。

测试覆盖旧项目迁移、密钥保护、四类 HTTP 协议、任务恢复、防重复提交、媒体文件和浏览器设置流程。模型协议测试使用本机 HTTP 服务，真实服务的模型权限、参数支持与计费需要用户配置后验证。

v0.3 在 Windows 上完成后端、前端与浏览器回归；真实服务验收用例在未配置时明确跳过。Ruff、ESLint、TypeScript/Vite 构建均纳入交付检查。

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

普通测试完全离线，真实 LLM/视频验收用例默认显示 `SKIPPED`。当前尚无真实服务凭据或可用 ComfyUI，不能把本机契约测试视为真实出片验收。后续先在设置界面保存连接，再仅在明确允许该服务生成和计费时运行：

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
- 单条连续视频轨、无声输出。支持排序和镜头帧数修改。
- APR 导入恢复结构并重新分配实体 ID，素材需要重新生成。导出 JSON 不包含二进制媒体。
- 最小回滚支持当前最新 revision 的逆向提交，历史素材与任务保留。
- 事件使用持久游标 REST 查询与轮询，尚未提供 SSE。
- 真实服务尚未使用用户凭据进行计费调用验收。当前通过本机契约服务验证接口实现。
- 时间轴音频混合、OTIO、多轨、VLM 审片、多人协作、Docker/PostgreSQL 部署留待后续阶段。
- 费用按用户输入的预估值预留，实际金额由用户对账，尚未自动同步服务商账单。
- MockDirector 使用四拍规则，镜头卡片视频带明确测试标识。
- 当前 Starlette 对 httpx TestClient 发出弃用提示，测试功能正常，依赖已锁定。

后续可按具体服务商增加专用协议适配器，并接入音画混合与成本结算。
