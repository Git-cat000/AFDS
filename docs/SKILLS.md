# 制作技能与脚本

AFDS v0.7 将项目的制作方法保存为按阶段选择的指导快照。技能库提供可复用原始包，APR 保存项目已选的指导，Project Engine 处理配置预览和批准，独立 Worker 运行检查与脚本。

## 制作阶段

| 阶段 ID | 工作台用途 |
| --- | --- |
| `story` | 剧本编排与戏剧节拍 |
| `continuity` | 角色、场景、世界状态和连续性 |
| `shots` | 景别、运镜、动作与时长 |
| `image` | 图像、构图与关键帧参考 |
| `video` | 视频生成意图与动作衔接 |
| `audio` | 对白、配音、音乐和环境声 |
| `editing` | 时间轴与音画节奏 |
| `review` | 审片证据及人工确认 |
| `delivery` | 渲染与成片交付 |

每阶段可选择三项技能及各自的文本参考资源。选择后先预览配置，再批准提交。修改配置用于后续创作请求，已有选片可继续使用。任务请求包含实际指导文本与技能哈希，素材来源保留所用技能的身份和版本。

剧本、镜头语义编辑、图像、视频和视觉审片会编译相关阶段指导。语音正文通过 `input` 发送，表演指导使用独立 `instructions` 字段，需服务与模型支持。该字段最大4096字符，已知的 `tts-1` 和 `tts-1-hd` 会在提交前返回能力错误。[语音接口规范](https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create)

## 导入其它项目的技能

支持单个 `SKILL.md`、完整目录和一个技能根目录的 ZIP。可保留 `references/`、`scripts/`、`assets/` 及包内其它资源。填写来源说明、指定适用阶段后导入。原始文件、声明的许可与兼容性可在详情查看，脚本可单独审阅。

技能使用 YAML frontmatter 描述名称与用途，正文保存制作方法。文件组织遵循 Agent Skills 格式。[Agent Skills 规范](https://agentskills.io/specification)

```text
dialogue-framing/
  SKILL.md
  references/
    framing.md
  scripts/
    inspect.py
```

```markdown
---
name: dialogue-framing
description: 为对话镜头组织视线、动作轴线与景别变化。
metadata:
  version: "1.0"
license: MIT
compatibility: Python 3.11+
---
# 对话镜头

建立人物与空间关系，再安排近景与反应镜头。
参考 references/framing.md 中的动作轴线说明。
```

名称采用小写字母、数字和连字符，最多64字符。原始包按相对路径及文件 SHA256 标识，重复导入可增加阶段分类。变更文件产生新的包身份。项目已选指导固定原有内容，适用资源可随 APR、JSON 导入与工程包恢复保留。

ZIP 最大16MiB，解压后32MiB，最多256项，单文件最大8MiB。支持普通相对路径资源。导入时校验路径、目录冲突、链接和凭据文件。技能正文和选中的文本资料另有模型上下文上限，配置页会显示超限错误。指导正文最多16000字符，单参考文档12000字符，每项技能合计24000字符。

## 运行外部脚本

选择脚本后填写 JSON 字符串数组参数。

```json
["--style", "dialogue", "  preserve spaces  "]
```

脚本解释器支持本机可用的 Python、Node.js、PowerShell 和 Bash。解释器与依赖由本机环境提供，包的兼容性说明保留在详情中。

先生成本次运行预览，再审阅源码、包与脚本哈希、参数数组、超时及工程上下文。预览固定工程修订和内容指纹。勾选本机权限确认后点击“批准并运行本次脚本”。同一预览重复批准返回原任务。重新运行需创建并批准新的预览。

脚本以当前本机用户的操作系统权限运行，具备该用户的文件与网络访问权限。脚本调用的服务及费用由脚本管理，任务费用状态记为未知。AFDS 费用账本统计已注册模型接口的请求。传给子进程的环境仅包含运行所需的系统变量及上下文文件入口。

每次运行有独立工作目录，包含技能包资源、`input.json` 和 `outputs/`。工程上下文同时通过标准输入提供，`AFDS_CONTEXT_FILE` 指向输入 JSON。参数按数组逐项传递并保留空格。包中根级 `input.json` 和 `outputs/` 由运行协议使用。

```python
import json
import pathlib
import sys

context = json.load(sys.stdin)
project = context["project"]
result = {
    "stage": context["stage"],
    "revision": project["revision"],
    "shot_count": len(project["shots"]),
    "arguments": sys.argv[1:],
}
pathlib.Path("outputs/report.json").write_text(
    json.dumps(result, ensure_ascii=False), encoding="utf-8"
)
print("制作检查结果已生成")
```

上下文包含制作阶段、项目修订、镜头、场景、角色、时间轴和素材元数据。大工程上下文会标出范围裁剪信息。可在预览中选择仅提供当前镜头。脚本形成的制作建议可经作者审阅后，通过现有制作界面应用。

超时范围5–300秒。标准输出和错误输出各显示前64KiB，单流超过1MiB会结束运行并记录错误。`outputs/` 可保存32个结果文件，单文件8MiB、合计32MiB。结果按哈希保存，并由所属项目的下载入口提供。常见凭据模式会从日志中遮蔽，并在导入或保存结果文件时检查。

Windows 使用作业对象管理正常子进程树，Unix 使用进程组。取消、超时与丢失租约会终止本次进程。已启动脚本在 Worker 中断后标为结果未确认，重新预览和批准后方可再次执行。

## 制作检查与运行记录

每层的“检查”按钮提交离线 Worker 任务。检查依据工程中可观察的事实，包括简述、场景、动作描述、选片状态、音轨、字幕、人工批准和当前成片状态。检查结果与来源修订一同保存，工程更新后显示旧修订提示。画面与声音质量结合关键帧审片、观看和试听确认。

也可直接检查导出的 APR JSON。

```powershell
.venv\Scripts\python.exe scripts\check_production.py project.apr.json --stage delivery
```

技能窗口展示运行状态、检查项、日志及下载文件，任务队列保留请求与溯源。外部导入包与运行输出保存在本机数据目录，公开源码发布使用项目实现和内置技能。
