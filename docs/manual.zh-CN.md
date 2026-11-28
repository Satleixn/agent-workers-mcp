# 操作说明书

[English](manual.md) | **简体中文**

下文用 `<仓库>` 指你克隆本仓库的目录，用 `<仓库>\.venv\Scripts\python` 指装好依赖的 venv 里的 Python（venv 建在别处时换成那个路径）。安装步骤见 [README](../README.zh-CN.md)。

## 1. 日常怎么用

把 [claude-md-snippet.md](claude-md-snippet.md) 加进 `~/.claude/CLAUDE.md` 之后，Claude 会自己判断什么时候交给工人。想指定时，直接这样说：

| 想做的事 | 对 Claude 说 |
|---|---|
| 翻译、改写一段文字 | "用 ask_deepseek 把这段翻成英文" |
| 起草文档或注释 | "让 deepseek-drafter 给 `xxx.py` 起草 README" |
| 第二意见 | "让 gemini-reviewer 审查 `src/xxx.py` 里的 `foo()`" |
| 读一堆文件后做总结 | "用 ask_gemini（hydrogen-medium）总结 `services/` 目录" |
| 审查整个仓库，让 Gemini 自己挑文件 | "让 Gemini 以读仓库模式审查 `D:\…\某仓库`，重点看……" |
| 审查一个分支、已检出的 PR 或尚未提交的改动 | "让 Gemini 审这个分支相对 main 的改动" |
| 看 Gemini 额度 | "查一下 Gemini 额度" |
| 指定模型 | 在话里写模型名，例如"用 neon-high 审查……" |

Claude 拿到结果后应当自己跑代码或查原文核验，并说明哪些核验过、哪些没有。**工人的话只是线索。**

### 两种给 Gemini 材料的方式

- **`files`**：指定几个文件，复制进临时目录下的 `files/`（保留相对路径，与 agy 自己的 `.agents` 目录分开）。总量上限 700KB（约 20 万 token）。
- **`repo`**：把整个仓库以只读方式挂进来（`--add-dir`），临时目录里放一份文件清单 `FILES.txt`（`git ls-files` 的结果，去掉密钥文件和依赖目录），agy 按清单自己挑着读。不受 700KB 上限限制，也不往仓库里写任何东西。
- **`repo` + `base`**：审查一次改动。`base` 是一个 git 引用（`main`、`origin/main`）；改动的范围是从 `base` 与 `HEAD` 的合并基点到工作区，分支上的提交和尚未提交的修改都包括在内。临时目录里还会多出 `DIFF.patch`（diff 本身）和 `CHANGES.txt`（每个改动文件及其状态，新建且未跟踪的文件标 `?`）。两者都不含密钥文件和依赖目录。改名按"删除 + 新增"处理：旧名是密钥文件名的那一侧照样排除，新名则和其他文件一样按文件名和内容检查（与普通 `repo` 模式相同）。diff 要通过内容检查（第 4 节），并与 `files` 共用 700KB 上限。GitHub 上的 PR 需要先检出（例如在 worktree 里运行 `gh pr checkout`），再把那个检出目录作为 `repo`。`base` 不能与 `proposal` 同时使用。

### Skill 与结构化结果

- **共享 skill**：`~/.claude/skills` 是 Claude 和 Gemini 共用 skill 的唯一来源。每次调用都把其中的目录（`lead-*` 这类只给主脑用的除外）复制到临时目录的 `.agents/skills/`，Gemini 能看到全部，自己判断读哪些。你自己 agy 里的插件和 skill 不会被用到（见第 4 节）。
- **`skills`**：Claude 为本次任务建议的共享 skill，连同路径一起给出，所以手动 skill（`disable-model-invocation`）也能用。Gemini 可以驳回建议或改用别的 skill，并报告读了哪些、驳回了哪些、建议增加哪些。
- **`structured`**：回答是符合 `review_schema.json` 的 JSON，包括 `summary`、`findings`（每条含 `severity`、`location`、`claim`、`evidence`、`confidence`，可选 `suggestion`）、`skills_used`、`rejected_suggestions`、`suggested_skills`。Claude 需要逐条核实的审查都用它。
- **`proposal`**：Gemini 不做审查，而是自己独立出一份方案，回答是符合 `proposal_schema.json` 的 JSON，包括 `summary`、`assumptions`、`decisions`（每条含 `topic`、`choice`、`rationale`、`evidence`、`alternatives`、`confidence`）、`steps`、`risks`、`open_questions`，以及三个 skills 字段。它隐含 `structured`。Claude 只给它需求和代码，不给自己的方案；Claude 先写好自己的方案，再按决策点和 Gemini 的方案逐条对比。
- **审查循环**：`lead-gemini-review` skill（由 `install.py` 安装到 `~/.claude/skills`）告诉 Claude 什么时候做计划审、diff 审或独立方案、怎么写 brief、怎么核实发现、怎么复审。

### 模型分档（Claude 默认按这个表选）

| 档 | 模型 | 适合 |
|---|---|---|
| 0 | Claude 自己做 | 改代码、需要你拍板的事、涉及密钥的内容、小活 |
| 1 | `deepseek-flash`（翻译、改写用 effort `none`，起草用 `low`） | 批量文字 |
| 2 | `gemini-4-hydrogen-low` | 分类、抽取、短摘要 |
| 3 | `gemini-4-hydrogen-medium` | 读 10 个以上文件做总结、文档问答 |
| 4 | `gemini-4-neon-medium`（默认） | 审查单个函数或模块 |
| 5 | `gemini-4-neon-high` | 跨文件审查、架构评审 |
| 6 | `gemini-4-neon-xhigh` | 数学、统计方法的正确性论证；Claude 用之前应先告诉你 |
| 备用 | `gemini-4-argon-high` | Gemini 池额度低时代替第 5、6 档（Argon 有独立额度） |

## 2. 命令行直接用（不经过 Claude）

```powershell
<仓库>\.venv\Scripts\python <仓库>\ask_deepseek.py --prompt "把这句话翻成英文：……" --effort none
```
```powershell
<仓库>\.venv\Scripts\python <仓库>\ask_gemini.py --prompt "概括这个文件" --file 'D:\路径\a.py' --model gemini-4-hydrogen-low
```
```powershell
<仓库>\.venv\Scripts\python <仓库>\ask_gemini.py --prompt "说明这个仓库的模块结构" --repo 'D:\路径\某仓库' --model gemini-4-hydrogen-medium
```
```powershell
<仓库>\.venv\Scripts\python <仓库>\ask_gemini.py --prompt "审查这些动画" --file 'D:\路径\modal.css' --skill review-animations --structured
```
```powershell
<仓库>\.venv\Scripts\python <仓库>\ask_gemini.py --prompt "审查这次改动，重点看错误处理" --repo 'D:\路径\某仓库' --base main --model gemini-4-neon-high --structured
```
```powershell
<仓库>\.venv\Scripts\python <仓库>\ask_gemini.py --prompt "需求：…… 约束：…… 请给出你的方案" --repo 'D:\路径\某仓库' --model gemini-4-neon-high --proposal
```
```powershell
<仓库>\.venv\Scripts\python <仓库>\ask_gemini.py --quota
```

回答打印在 stdout；用量、调用 id、守门钩子拒绝了哪些操作打印在 stderr。`--file` 和 `--skill` 可以写多次；`--structured` 要求返回 JSON 审查结果，`--proposal` 要求以 JSON 返回 Gemini 自己的方案。`--timeout` 设时间上限（秒）：默认 540，保证在前台运行时不超过 Claude Shell 工具的 10 分钟上限；长审查请放在后台运行，并加上 `--timeout 3600`。DeepSeek 另有 `--system "..."`、`--json`（要求返回 JSON）。

## 3. 查看原始输入和输出

每次**真正发出去**的调用（成功或失败）都在 `<仓库>\logs\YYYY-MM.jsonl` 里记一行：实际发出的提示词、文件指纹（路径、大小、sha256）、完整回答或错误、用量、耗时。

- Gemini 另记 `agy_transcript`（agy 自己的完整记录，含思考过程和每一步操作）、`tool_calls`（钩子对 agy 每次操作的判定，也能看出 Gemini 读了哪些 `SKILL.md`）、读仓库模式下的 `repo`，设了 `base` 时的 `base`、`merge_base`、`diff`（大小和 sha256）和 `diff_excluded`（被排除的文件数），额度检查结果 `quota_check`，以及 `skills_suggested`、`structured`、`proposal`、`isolated`。
- DeepSeek 另记思考内容 `reasoning` 和是否在高峰时段 `peak`。

MCP 结果第一行末尾有 `id=…`，按 id 查：

```powershell
$id = '把 id 粘贴在这里'; Get-ChildItem -LiteralPath '<仓库>\logs' -Filter '*.jsonl' | Select-String -SimpleMatch $id | ForEach-Object { $_.Line | ConvertFrom-Json } | Format-List
```

- 被安全检查拒绝的请求**不记录**（它可能正好含有密钥）。
- 只保留最近 3 个月的日志文件，更早的在写日志时自动删除。`logs/` 不进 git。
- agy 把这些调用的记录留在 `~/.agent-workers/agy-home/antigravity-cli/`（它的私有 home）里，由 agy 自己管理；本工具只负责让这个 home 里的 `GEMINI.md` 与 `reviewer_rules.md` 保持一致。

## 4. 安全规则（写在代码里，Claude 绕不过去）

- **按文件名和位置拒绝**：`secrets.toml`、各种 `.env`、证书和私钥（`*.pem`、`*.key`、`*.p12`、`*.pfx`、`*.jks`、`id_rsa*` 等）、`credentials.json`、`service_account*.json`、`.netrc`、`.npmrc`、`.pypirc`、`.git-credentials`、`.claude.json`；`.streamlit`、`.ssh`、`.aws`、`.kube`、`.gnupg`、`.azure`、`.gcloud` 目录下的文件；`~/.gemini`、`~/.agent-workers/agy-home` 和本仓库 `logs/` 里的任何文件。
- **按内容拒绝**：文件内容、提示词、system 提示里出现像 Key 的内容就拒绝。包括各家 Key 和令牌的格式、JWT、`Bearer` 长串、私钥块、带密码的连接串、`xxx_api_key = "长串"` 和 `.env`/YAML 里的无引号写法。变量名以 `_path`、`_file`、`_dir`、`_url`、`_name` 结尾的赋值不算（它们存的是位置）。报错只说类型和行号。
- **Gemini 只读**：每次调用都在临时目录装上守门钩子 `agy_guard.py`。它只放行工作区内、通过上面检查的 `read_file`（钩子自己在 `.agents/guard` 和 `.agents/hooks.json` 里的文件除外），只有 `structured` 和 `proposal` 调用时才额外放行 `finish`：agy 用它交回结果。执行命令、写文件、访问网页等一律拒绝；钩子自己出错也拒绝。仅靠 `--mode plan` 和空临时目录并不够：实测 agy 仍能读到工作区外的文件，所以需要钩子。
- **与你自己的 agy 隔离**：agy 以 `--gemini_dir ~/.agent-workers/agy-home` 运行。这个 home 里没有插件，`GEMINI.md` 就是 `reviewer_rules.md`，所以你的 agy 插件、skill 和 `GEMINI.md` 都不会作用于这些调用，它们也不再和你自己的会话共用 agy 的对话数据库。登录不受影响（agy 把它存在系统钥匙串里）。`--gemini_dir` 没有公开文档（在 agy 1.5.14 上实测；print 模式下 `GEMINI_HOME` 环境变量无效），所以每次调用都会检查 agy 是否真的用了这个 home，没用上就标 `NOT-ISOLATED`。
- **不会误走付费 API**：传给 agy 的环境变量去掉了所有名字里带 key、token、secret、password、credential 的变量。每次调用还通过 `--settings` 强制设定 `useG1Credits: false`（订阅额度用完后不改用付费 AI credits）和 `modelProvider: ""`（不直连 Gemini API）。这些设定只对本工具的调用生效，不写入你的 agy 设置。斜杠命令也已禁用。
- **`base` 审查时的 git**：以 `--no-ext-diff --no-textconv` 和 `core.fsmonitor=false` 运行，仓库配置里的外部 diff 工具和文本转换程序都不会执行。clean 过滤器（例如 git-lfs）仍然生效：它来自你自己的 git 配置，clone 不会带来，关掉反而会让 LFS 文件的 diff 出错。所以只对 `.git/config` 可信的仓库用 `base`，就像你自己在里面运行 `git diff` 之前一样。
- **哪些仓库可以发**：由你的 CLAUDE.md 决定（见 snippet 的 Privacy 一行）。代码只守密钥这条底线，不限制目录。

## 5. 费用、额度和时间

- **Gemini**：只用订阅额度。Neon、Hydrogen 共用一个池（周额度 + 5 小时额度），Argon 单独一个周额度池。每次调用固定约 1.1 万 token 的代理开销（agy 有了自己的 home、不再加载你那些插件的 skill 列表之前，约 2.4 万）。
  - 每次调用前，代码会自动按模型所在的额度池查一次额度（结果缓存 60 秒，查额度不耗额度）：池里任何一项低于 10% 就拒绝调用，由 Claude 自己做；Gemini 5 小时额度低于 25% 时拒绝 neon-high 和 xhigh，报错里建议改用 argon-high（独立额度）或 neon-medium。查额度本身失败时照常调用。被额度拒绝的调用不记日志。
  - 单次时间上限是本工具设的（agy 默认不限时）：MCP 调用 1 小时；命令行默认 9 分钟（低于 Claude 的 Shell 工具 10 分钟上限），在后台运行并加 `--timeout 3600` 时可以到 1 小时。用 neon-high、xhigh 读仓库审查用过约 15 分钟，网络断连时 agy 还可能多耗几分钟才重试。用 neon-high、xhigh、argon 时每次的材料仍然最好控制在 150KB 以内。
  - 查额度用 `agy /quota`，有自己的 60 秒上限，不会等正在进行的调用。
  - 在 Claude 里按 Esc 会立即结束 agy，不再消耗额度。
- **DeepSeek**：按 token 计费，思考 token 按输出计费。官方在工作日 01:00–04:00 和 06:00–10:00（UTC）收双倍价格，这段时间调用时结果第一行末尾会标 `PEAK-2x-price`。价格以 DeepSeek 官网为准。

## 6. 常见问题

| 现象 | 处理 |
|---|---|
| 新会话里没有 `mcp__workers__*` 工具 | 确认 `~/.claude.json` 里有 `mcpServers.workers`；完全退出 Claude 再打开 |
| 改了代码，但行为没变 | MCP 服务在 Claude 启动时加载代码，改完要重启 Claude。命令行不用重启 |
| 新建或修改了子代理文件，没生效 | 子代理只在会话启动时加载，开新会话 |
| Gemini 报找不到 agy | 在终端运行 `agy --version`；不行就重装 Antigravity CLI |
| Gemini 报登录或权限问题 | 在终端运行 `agy`，按提示用 Google 账号重新登录 |
| `failed to start conversation` | agy 偶发的本地启动问题，发生在调用模型之前，不耗额度。脚本会自动重试一次 |
| `print timeout … the response is partial` | 超过时间上限。先看日志里的 `agy_transcript`：长时间空档之后跟着一步 `ERROR_MESSAGE`，说明是网络断了，可以重试一次。否则换小一档模型或拆分材料，因为耗时主要在思考上 |
| `prompt too long for the Windows command line` | 提示词超过约 3.2 万字符。把材料写进文件，用 `files` 传 |
| `model … is not allowed` | 以 `-` 开头的模型名和 orchestrator 模型被拒；从分档表里选 |
| `Gemini quota low` 或 `needs the Gemini five-hour quota` | 额度不够，按报错换模型，或者等重置时间过后再用 |
| `invalid model selection` | 模型名写错了；运行 `agy models` 看当前可用的名字 |
| 结果第一行有 `denied=N` | 守门钩子拒绝了 agy 的 N 次操作；在日志的 `tool_calls` 里看具体是哪些、为什么 |
| 结果第一行有 `NOT-ISOLATED` | agy 没用上它的私有 home（可能是 agy 更新后改了 `--gemini_dir`），你自己的 agy 插件和 `GEMINI.md` 可能生效了。查 `agy --help`，并看 `~/.gemini/antigravity-cli/log` 下最新的日志里实际用的数据目录 |
| `unknown or lead-only skill(s)` | 这个名字在 `~/.claude/skills` 里不是带 `SKILL.md` 的目录，或者以 `lead-` 开头；报错里列出了可用的共享 skill |
| 结果第一行有 `JSON-INVALID` | `structured` 的回答没能解析成 JSON，后面跟的是原始文本。如果日志的 `tool_calls` 里 `finish` 被拒，说明正在运行的还是旧版服务，重启 Claude |
| 一个文件被 "looks like a … key" 拒绝 | 先看报错里的行号确认是不是真 Key。真 Key：从代码里移走并轮换；误报：只发其他文件，或者让 Claude 自己读 |
| `DEEPSEEK_API_KEY is not set` | 设成用户级环境变量后重启 Claude |
| DeepSeek 报 401 | Key 错误或已失效 |
| 503、504 | 服务端过载。Claude 应当自己做，不反复重试 |

## 7. 维护

- **不要随手升级 `mcp` 包**：mcp 1.x 到 2.x 改过接口（`FastMCP` 改名为 `MCPServer`），大版本升级先单独测试。
- **agy 更新**：`agy update`，之后跑一次 `ask_gemini.py --prompt "Reply with exactly: OK" --model gemini-4-hydrogen-low` 确认还能用。
- **模型改名**：改 `ask_gemini.py`、`ask_deepseek.py` 顶部的 `DEFAULT_MODEL`，以及 `workers_mcp.py` 工具描述里的分档表，然后重启 Claude。
- **停用**：退出 Claude 后从 `~/.claude.json` 删掉 `mcpServers.workers`，再删掉 CLAUDE.md 里那一节、`~/.claude/agents/` 下的两个子代理文件、`~/.claude/skills/` 下的 `lead-gemini-review` 目录，以及 `~/.agent-workers/`（agy 给这些调用用的私有 home）。
