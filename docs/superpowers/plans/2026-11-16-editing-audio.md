# AFDS 高级剪辑与音频分析实施计划

> 执行代理使用 `superpowers:executing-plans` 逐项实施。每项以正常 UI 入口和实际媒体结果验收。

## 目标、架构与技术栈

提供多视频轨、画中画、转场、自动节拍候选及带时间戳的转写字幕。Project Engine 保存合成描述，Worker 将同一规范用于预览和最终渲染，导出与恢复保持工程语义。沿用 Pydantic、FFmpeg、原生 OTIO、React 和既有模型适配器模式，节拍分析按任务引入经验证的 NumPy 版本。

## 需求与约束

需求依据和全局约束见[总计划](2026-11-16-product-completion.md)。前置任务为 S1、S2。计划提出最多 8 条视频轨，其中主轨 1 条、叠加轨 7 条，保留最多 15 条音轨。影片时间使用整数帧与有理数帧率。APR 目标版本为 0.5.0，继续装载 0.1–0.4 工程。

## 文件职责

| 文件 | 操作与责任 |
| --- | --- |
| `backend/app/timeline_models.py`、`timeline_service.py`、`timeline_api.py` | 扩展合成模型、事务和编辑入口 |
| `backend/app/composition.py`、`composition_render.py` | 新增规范化合成描述、裁切与 FFmpeg 图编译 |
| `backend/app/schemas.py`、`project_engine.py`、`execution/queue.py`、`execution/worker.py` | 扩展兼容装载、主轨重建、指纹和任务分派 |
| `backend/app/video_import.py` | 新增受限本机视频上传的 Worker 规范化流程 |
| `backend/app/audio_analysis.py`、`analysis_service.py`、`analysis_api.py`、`transcription_adapter.py` | 新增节拍、转写、候选结果与审批 |
| `backend/app/provider_settings.py`、`provider_http.py`、`budget.py` | 扩展转写能力、连接版本和预算使用 |
| `frontend/src/TimelineEditor.tsx`、`CompositionPreview.tsx`、`AudioAnalysisPanel.tsx` | 扩展轨道编辑、真实预览和分析候选交互 |
| `frontend/src/types.ts`、`api.ts`、`ProviderSettings.tsx` | 扩展前端协议和转写设置 |
| `backend/app/exchange.py`、`restore_service.py` | 多轨、转场和引用的导出恢复 |
| `backend/tests/test_composition.py`、`test_composition_media.py`、`test_audio_analysis.py`、`test_transcription_contracts.py` | 新增合成、媒体、分析和协议回归 |
| `frontend/tests/composition.test.tsx`、`audio-analysis.test.tsx`、`tests/e2e/advanced-editing.spec.ts` | 新增 UI 与浏览器端到端验收 |

## 重点情况

1. 旧版音轨更新请求在新工程中继续保存叠加轨与转场。A1。
2. 重叠转场、分数帧率、不同源时长及画中画结束时刻。A1、A2。
3. 静音、弱节奏、转写缺少时间戳和受保护字幕。A3。
4. 预览期间切换工程及恢复后的素材、镜头和转场 ID。A2、A4。
5. 素材失效、锁定镜头与不同剪辑器对效果元数据的支持。A1、A4。

## A1 多轨合成模型与编辑事务

### 接口

- `VideoLayerPlacement` 定义 `id`、`asset_id`、可选 `asset_version_id`、`start_frame`、`source_start_frame`、`duration_frames`、`x`、`y`、`width`、`height`、`opacity`。
- 几何值使用输出画幅的归一化比例，宽高范围 0.05–1，位置使矩形处于画幅内，透明度范围 0–1。叠加轨顺序决定覆盖顺序。
- `VideoLayerTrack` 定义轨道 ID、名称、隐藏状态和最多 100 个片段。叠加素材支持工程所有权范围内的视频及静态图像。
- `VideoTransition` 定义 `id`、`from_clip_id`、`to_clip_id`、`kind`、`duration_frames`。首批类型为 `dissolve`、`fadeblack`、`wipeleft`。
- `TimelineData` 保留已有主轨和音轨字段，新增 `video_layers` 和 `transitions`。旧主轨片段缺少 ID 时按项目 ID 与镜头 ID 派生稳定 ID。
- `CompositionUpdateV2` 包含 `base_revision`、完整音轨/叠加轨、字幕、标记与转场。新增 `PUT /projects/{id}/timeline/composition-v2`，原 composition 入口继续只更新其原有字段。
- `build_composition(apr: dict) -> CompositionSpec` 位于 `composition.py`，解析素材版本、哈希、轨道、转场、音频、字幕、画幅及输出帧数，作为队列请求的规范来源。

### 转场时间规则

主轨相邻片段的转场使用显式重叠。重叠帧数从顺序拼接总长扣除，下一片段起点为上一片段终点减重叠时长。单个转场长度至多为相邻较短片段时长的一半，同一片段的入场加出场重叠不得超过片段时长，三片段同时重叠时校验失败。

添加或更改转场先生成时序预览，列出主轨起点、成片总长和受影响的音频/字幕/标记。关联内容采用其现有绝对帧位置，越界时用户在同一预览中明确选择裁剪或调整，批准后原子提交。锁定和批准镜头的源设定变更仍经过既有保护流程。

- [x] 添加失败测试 `test_legacy_composition_update_preserves_layers`、`test_transition_overlap_changes_duration`、`test_fractional_fps_round_trip`。两个 48 帧片段重叠 12 帧时输出为 84 帧。非法几何、外项目素材、越界裁切及三片段重叠返回 422。
- [x] 执行 `.venv/Scripts/python.exe -m pytest backend/tests/test_composition.py -q --basetemp data/composition-red`，记录新字段和事务缺失导致的失败。
- [x] 实现模型、旧 APR 装载与 `build_composition()`。修改 `rebuild_timeline()` 保持稳定片段 ID，主轨调整后重新校验层、转场及锚点。合成描述改变使成片失效，独立片段源素材依赖继续精确维护。
- [x] 实现 V2 编辑与转场预览/批准入口，预览摘要固定原修订及关联调整。将规范化描述、素材哈希和渲染器版本纳入队列指纹。
- [x] 执行定向测试和既有 `test_audio_timeline.py`、`test_engine.py`、`test_phase6_regressions.py`。确认旧 API 和受保护镜头行为后精确提交。

## A2 画中画、真实转场与预览

### 接口

- `render_composition(settings, spec: CompositionSpec, sources: dict, output, checkpoint) -> dict` 位于 `composition_render.py`，输出实际媒体元数据。
- `compile_video_graph(spec: CompositionSpec) -> tuple[list[str], str]` 只接受已校验的结构参数，返回滤镜参数与视频输出标签。
- `render_preview` 任务复用合成规范，按选择的帧区间及较低分辨率生成带来源修订的预览视频。
- 视频上传入口采用流式上限 256 MiB、使用权确认及 Worker 检测/规范化，按素材 ID 参与后续剪辑。

- [x] 添加失败媒体测试 `test_pip_pixels_and_end_frame` 和 `test_transition_frames_are_blended`。使用红/蓝测试视频检查画中画内外像素与结束后的画面，检查转场中间帧及 84 帧输出。视频源有不同帧率时验证统一帧率、裁切和时间戳。
- [x] 执行定向媒体测试，确认真实输出缺少对应效果时失败。
- [x] 实现规范化、裁切、缩放、透明度、`overlay` 和转场图编译，使用本机 FFmpeg 支持的明确枚举。视频源音频按独立音轨规则处理，最终音频继续使用既有混音管线，字幕在相同时间基准上合成。
- [x] 实现本机视频导入、预览任务与队列取消/租约处理。增加恶意媒体、无视频流、上传中断及临时文件清理测试。预览缓存固定工程、帧区间、合成指纹和输出规格。
- [x] 完成轨道新增、素材拖入、排序、裁切、画幅参数、转场预览和批准 UI。`CompositionPreview` 显示真实 Worker 预览，工程切换或修订改变时标记已有预览来源。
- [x] 执行媒体测试、前端合成测试及浏览器操作，保存 ffprobe、抽帧和桌面/移动截图证据后精确提交。

FFmpeg 的 overlay、xfade 和时间基准约束作为编译器依据。可用滤镜在启动诊断中检测。[FFmpeg 滤镜文档](https://ffmpeg.org/ffmpeg-filters.html)

## A3 自动节拍与转写候选

### 接口

- `detect_beats(samples, sample_rate: int, fps_num: int, fps_den: int) -> BeatAnalysis` 位于 `audio_analysis.py`。Worker 将选定音频规范为 16 kHz 单声道，分析范围上限 30 分钟。
- `BeatAnalysis` 包含候选帧、估计 BPM、可选置信度、来源素材哈希与范围。首个实现使用有界频谱/能量起音和周期估计，静音返回空候选。
- `TranscriptionSegment` 包含 `start_ms`、`end_ms`、`text`。`TranscriptionResult` 包含语言、带时间戳分段和来源范围，校验单调性、持续时间及文本大小。
- `TranscriptionAdapter.transcribe(request, output_dir, checkpoint) -> TranscriptionResult` 接入 OpenAI-compatible 文件转写接口，连接设置新增 `transcription` 能力和时间戳能力声明。
- `AnalysisService.enqueue(project_id, body) -> dict` 创建 `analyze_beats` 或 `transcribe_audio` 任务。结果保存在有界任务记录及归属正确的结果文件。
- `AnalysisService.preview_apply(project_id, job_id, body) -> dict` 和 `commit_apply(project_id, job_id, body) -> dict` 固定结果哈希、APR 修订和人工选项，将候选写入 markers 或 captions。

- [x] 添加失败测试 `test_click_track_frames_within_one_frame`、`test_silence_has_no_beats`、`test_transcription_preserves_manual_captions`。120 BPM 合成点击音轨在已知起点下，节拍误差至多一帧。丢失时间戳、倒序/越界分段及结果超过 500 条待用字幕时提供明确错误或要求缩小范围。
- [x] 执行 `test_audio_analysis.py` 和 `test_transcription_contracts.py`，记录缺失能力导致的失败。
- [x] 实现本机节拍 Worker 分支及来源信息。转写复用连接的加密保存、请求授权、预算预留、超时、已提交请求不确定状态和错误映射。服务返回的实际响应先验证再形成候选。
- [x] 在音频面板提供分析范围、语言、转写连接和候选预览。字幕写入默认新增用户选定区域的候选，替换手工字幕时列出具体条目并由用户批准，409 时保留草稿。
- [x] 使用本机契约服务验证上传、模型参数、时间戳、费用和失败恢复，再运行真实浏览器审批流程。真实转写质量在用户配置并授权服务后另行验收。
- [x] 更新 Provider/分析 API 与使用说明，精确提交 A3 文件。

转写格式与时间戳支持由具体模型能力决定，协议适配器通过配置和响应验证表达该能力。[文件转写文档](https://developers.openai.com/api/docs/guides/speech-to-text)

## A4 开放时间轴、恢复与全链路验收

### 接口

- `CompositionSpec` 是 OTIO 转换和恢复后渲染的共同输入。
- OTIO 视频层使用原生 Track/Stack 和 Gap 表示，转场使用原生 Transition。画幅、透明度及 AFDS 参数带有版本化元数据，导出说明列出目标编辑器的效果映射情况。
- 转场重叠映射时设置 `in_offset=floor(duration/2)`、`out_offset=duration-in_offset`。前片段名义范围尾部扣除 out_offset，后片段名义范围头部扣除 in_offset，并保留完整媒体 available_range 作为转场 handles。每个片段同时累计入场和出场裁切。
- ZIP 0.3 包含新 APR、工作台快照与完整所需素材，恢复重新映射片段、镜头、素材和引用。

- [x] 添加失败测试 `test_multitrack_otio_round_trip`、`test_odd_transition_handles_keep_duration` 和 `test_restored_composition_renders_same_frames`，原生 OTIO 解析后核对层级、间隔、转场、音轨和总长。两个 48 帧片段加 12 帧转场时验证 84 帧，加 11 帧转场时验证 85 帧。缺失素材、失效选片或跨项目引用阻止出片并定位相关片段。
- [x] 执行新的导出恢复测试及既有 `test_exchange.py`、`test_restore.py`，记录差异。
- [x] 修改导出恢复、素材清单和生产检查，确保叠加轨及转场引用被纳入快照和哈希清单。时间轴中镜头失效时列出引用该镜头的相关片段。
- [x] 在 `advanced-editing.spec.ts` 从旧工程完成多轨、画中画、转场、节拍候选、契约服务转写、字幕批准、预览、成片及恢复再渲染。核对 H.264、AAC 48 kHz 立体声、精确帧数、抽帧效果及原生 OTIO。
- [x] 执行完整后端、Ruff、前端 lint/Vitest/build 和 Playwright，保存环境与日志。更新 README、API、PROGRESS 与 `.ai/`，完成 M2 后精确提交。

规划阶段已在本机原生 OTIO 0.18.1 做最小验证。两个完整 48 帧 Clip 插入 12 帧 Transition 后 Track 仍为 96 帧，按上述 handles 规则裁切名义范围后为 84 帧，因此导出任务包含显式的时长映射测试。[OTIO Transition 定义](https://opentimelineio.readthedocs.io/en/latest/tutorials/otio-serialized-schema.html)
