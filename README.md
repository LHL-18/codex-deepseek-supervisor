# Codex × DeepSeek Supervisor

在 **Codex 对话中指挥 DeepSeek 完成局部开发任务**，由 Codex 负责拆解、审查、返工、应用修改与实际验收。

这是一个 Codex Skill 和 Python 本地调用入口。日常直接在 Codex 里使用，附带的本地协作看板为可选工具。

```text
用户 → Codex 拆任务 → DeepSeek 返回建议或补丁
          ↑                  ↓
          └── 审查 / 返工 ────┘
          ↓
      Codex 应用修改 → 运行验证 → 交付并退出协作
```

DeepSeek 通过 API 返回文本，不直接读写项目文件或执行命令。文件操作和测试由 Codex 完成；这不是把 DeepSeek 注册为 Codex 原生子代理。

## 包含什么

- `skills/deepseek-supervisor/`：可安装的 Skill、界面元数据和命令分发脚本。
- `tools/deepseek_worker.py`：DeepSeek API 调用、输入与输出上限、超时、用量和回执。
- `tools/collaboration_mode.py`：默认关闭、按任务启用的调用开关。
- `tools/collaboration_trace.py`：实际任务交接、审查、变更和命令验证记录。
- `tools/collaboration_dashboard.*`：可选的本地流程看板。
- `tools/test_*.py`：不调用真实模型的离线测试。

仅包含通用协作工具。仓库不包含 API Key、真实请求、源码上下文、健康应用数据或原项目 Git 历史。

## 环境

- Python 3.10 或更新版本；运行工具只使用标准库。
- Git，以及可以使用文件和终端工具的 Codex 环境。
- 自己的 DeepSeek API Key 和可用额度。默认模型为 `deepseek-flash`。
- Node.js 只用于可选的 JavaScript 语法检查，不是运行调用器或看板的必要条件。

本地 Codex Skill 的安装不需要另外填写 OpenAI API Key；DeepSeek API 的费用由你自己的 DeepSeek 账户承担。

## 1. 安装 Skill

将 `skills/deepseek-supervisor` 整个目录复制到 Codex 的技能目录：

- 设置了 `CODEX_HOME`：`CODEX_HOME/skills/deepseek-supervisor`。
- 未设置：用户目录下的 `.codex/skills/deepseek-supervisor`。

例如在 PowerShell 中，从本仓库根目录执行：

```powershell
$skillTarget = if ($env:CODEX_HOME) { Join-Path $env:CODEX_HOME 'skills' } else { Join-Path $env:USERPROFILE '.codex\skills' }
New-Item -ItemType Directory -Path $skillTarget -Force | Out-Null
Copy-Item -LiteralPath '.\skills\deepseek-supervisor' -Destination $skillTarget -Recurse
```

已有同名技能时，先比较版本再决定是否覆盖。技能列表暂时未出现名称时，可直接让 Codex 读取安装目录中的 `SKILL.md` 并按说明执行。

## 2. 为目标项目配置调用入口

可以在本仓库中试用，也可以将本仓库的 `tools/` 源码复制到你要修改的 Git 项目中。Skill 会从目标项目或它的父目录寻找调用入口，不会自动跳转到其他项目。

目标项目需要保留这些配套文件，不能只复制 `deepseek_worker.py`：

```text
tools/
  deepseek_worker.py
  collaboration_mode.py
  collaboration_trace.py
  collaboration_progress.py
  collaboration_dashboard.py       # 可选看板
  collaboration_dashboard.html
  collaboration_dashboard.css
  collaboration_dashboard.js
```

把 `.env.deepseek.example` 复制到目标项目根目录，重命名为 `.env.deepseek.local`，在本地编辑器里填写自己的 Key。示例中的 Key 必须保持空白。

将本仓库 `.gitignore` 的凭证和运行记录规则加入目标项目已有的 `.gitignore`，不要覆盖项目原来的忽略规则。必须至少忽略：

```gitignore
.env.deepseek.local
tools/.deepseek-runs/
```

也支持 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`、`DEEPSEEK_TIMEOUT` 和 `DEEPSEEK_MAX_TOKENS` 环境变量，优先于本地配置文件。

## 3. 在 Codex 中调用

直接发给 Codex：

```text
$deepseek-supervisor 修复日期解析函数的时区问题。
让 DeepSeek 提出补丁，你负责审查和测试，最多调用三次，完成后退出协作。
```

Codex 会为这个任务启用协作、选择必要源码并调用 DeepSeek，检查返回内容后再修改项目和运行相关测试。需要返工时会发送明确的反馈。

普通任务保持 Codex 独立模式。退出时直接说：

```text
结束协作，后续由你独立处理。
```

每次授权只针对指定任务，最长 8 小时。关闭开关会阻止新请求；已经发送的请求仍可能返回。

询问技能用法、读取状态或打开看板不会调用模型。只有工作中的 Codex 发起调用器命令时，才会向 DeepSeek 提交请求。

## 可选：本地协作看板

```powershell
python tools/collaboration_dashboard.py --port 8765
```

在本机打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)。保持运行服务的终端开启，按 Ctrl+C 停止服务。

看板展示任务级开关、调用记录、实际发送内容、原始回复、耗时、用量、审查依据和验证输出。每 2 秒刷新，并可跟随最新调用。

进度展示当前阶段、负责方、等待时长和已确认的阶段数。它不猜测模型内部的生成百分比。只有实际记录的步骤才算已确认；缺失步骤显示“未记录”。Codex 的审查和变更说明需要显式记录，看板不会自动观察所有聊天操作。

看板开关只改变调用授权，不会向 Codex 自动发送任务。调用成功、代码修改、测试通过和最终验收是不同的状态。

## 直接运行命令

通常由 Skill 代为运行；这里也可以手动验证工作流：

```powershell
python tools/collaboration_mode.py status
python tools/collaboration_mode.py start --task '审查一个指定模块'
python tools/deepseek_worker.py --task '只读审查这个模块的边界条件，不生成补丁。' --file path/to/source.py --max-tokens 2000
python tools/collaboration_mode.py end --reason '本次协作结束'
```

`--file` 接受目标项目内 Git 已跟踪的文本源码。多行任务用 `--task-file`。完整审查、验证及验收命令参见 [命令参考](skills/deepseek-supervisor/references/bridge-commands.md)。

## 隐私与用量边界

- Key 只保存在本地环境或被 Git 忽略的文件中，不写进技能或仓库源码。
- 运行记录保存在 `tools/.deepseek-runs/`，包含任务、所选源码上下文和回复，应留在本地。
- 已知 Key 和常见 Key 格式会被遮蔽；这不能替代提交源码前的人工筛选。
- 只发送本次任务所需的代码，不发送其他凭证、个人资料或生产数据库。
- 默认输出上限为 4096 tokens，可设 1～8192；任务和上下文合计上限为 120 KB。
- 默认 socket 超时 90 秒，无自动 HTTP 重试；超时不保证服务端未计费。
- token 上限用于控制用量，不是货币硬预算。具体费用以 DeepSeek 账单为准。
- 测试和凭证排除不能追回已经泄露的密钥；若密钥曾发到公开位置，应在服务商控制台撤销并更换。

## 离线测试

```powershell
python -m unittest discover -s tools -p 'test_*.py' -v
node --check tools/collaboration_dashboard.js
```

这些测试通过模拟响应检查默认关闭、会话边界、凭证遮蔽、进度真实性、失败处理和本机控制接口，不发送真实 API 请求。

## 仓库与应用范围

本仓库是独立导出的协作工具，提交历史不继承原开发项目。复制工具到其他项目之前，请阅读该项目自己的开发和数据处理要求。
