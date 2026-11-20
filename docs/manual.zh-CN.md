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
| 看 Gemini 额度 | "查一下 Gemini 额度" |
| 指定模型 | 在话里写模型名，例如"用 neon-high 审查……" |

Claude 拿到结果后应当自己跑代码或查原文核验，并说明哪些核验过、哪些没有。**工人的话只是线索。**

### 两种给 Gemini 材料的方式

- **`files`**：指定几个文件，复制进临时目录（保留相对路径）。总量上限 700KB（约 20 万 token）。
- **`repo`**：把整个仓库以只读方式挂进来（`--add-dir`），临时目录里放一份文件清单 `FILES.txt`（`git ls-files` 的结果，去掉密钥文件和依赖目录），agy 按清单自己挑着读。不受 700KB 上限限制，也不往仓库里写任何东西。

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
<仓库>\.venv\Scripts\python <仓库>\ask_gemini.py --quota
```

回答打印在 stdout；用量、调用 id、守门钩子拒绝了哪些操作打印在 stderr。`--file` 可以写多次。DeepSeek 另有 `--system "..."`、`--json`（要求返回 JSON）。

## 3. 查看原始输入和输出

每次**真正发出去**的调用（成功或失败）都在 `<仓库>\logs\YYYY-MM.jsonl` 里记一行：实际发出的提示词、文件指纹（路径、大小、sha256）、完整回答或错误、用量、耗时。

- Gemini 另记 `agy_transcript`（agy 自己的完整记录，含思考过程和每一步操作）、`tool_calls`（钩子对 agy 每次操作的判定）、读仓库模式下的 `repo`，以及额度检查结果 `quota_check`。
- DeepSeek 另记思考内容 `reasoning` 和是否在高峰时段 `peak`。

MCP 结果第一行末尾有 `id=…`，按 id 查：

```powershell
$id = '把 id 粘贴在这里'; Get-ChildItem -LiteralPath '<仓库>\logs' -Filter '*.jsonl' | Select-String -SimpleMatch $id | ForEach-Object { $_.Line | ConvertFrom-Json } | Format-List
```

- 被安全检查拒绝的请求**不记录**（它可能正好含有密钥）。
- 只保留最近 3 个月的日志文件，更早的在写日志时自动删除。`logs/` 不进 git。
- agy 自己在 `~/.gemini/antigravity-cli/` 里也留有记录，由 agy 自己管理，本工具不删也不写。

## 4. 安全规则（写在代码里，Claude 绕不过去）

- **按文件名和位置拒绝**：`secrets.toml`、各种 `.env`、证书和私钥（`*.pem`、`*.key`、`*.p12`、`*.pfx`、`*.jks`、`id_rsa*` 等）、`credentials.json`、`service_account*.json`、`.netrc`、`.npmrc`、`.pypirc`、`.git-credentials`、`.claude.json`；`.streamlit`、`.ssh`、`.aws`、`.kube`、`.gnupg`、`.azure`、`.gcloud` 目录下的文件；`~/.gemini` 和本仓库 `logs/` 里的任何文件。
- **按内容拒绝**：文件内容、提示词、system 提示里出现像 Key 的内容就拒绝。包括各家 Key 和令牌的格式、JWT、`Bearer` 长串、私钥块、带密码的连接串、`xxx_api_key = "长串"` 和 `.env`/YAML 里的无引号写法。变量名以 `_path`、`_file`、`_dir`、`_url`、`_name` 结尾的赋值不算（它们存的是位置）。报错只说类型和行号。
- **Gemini 只读**：每次调用都在临时目录装上守门钩子 `agy_guard.py`。它只放行工作区内、通过上面检查的 `read_file`；执行命令、写文件、访问网页等一律拒绝；钩子自己出错也拒绝。仅靠 `--mode plan` 和空临时目录并不够：实测 agy 仍能读到工作区外的文件，所以需要钩子。
- **不会误走付费 API**：传给 agy 的环境变量去掉了所有名字里带 key、token、secret、password、credential 的变量。每次调用还通过 `--settings` 强制设定 `useG1Credits: false`（订阅额度用完后不改用付费 AI credits）和 `modelProvider: ""`（不直连 Gemini API）。这些设定只对本工具的调用生效，不写入你的 agy 设置。斜杠命令也已禁用。
- **哪些仓库可以发**：由你的 CLAUDE.md 决定（见 snippet 的 Privacy 一行）。代码只守密钥这条底线，不限制目录。

## 5. 费用、额度和时间

- **Gemini**：只用订阅额度。Neon、Hydrogen 共用一个池（周额度 + 5 小时额度），Argon 单独一个周额度池。每次调用固定约 2.5 万 token 的代理开销。
  - 每次调用前，代码会自动按模型所在的额度池查一次额度（结果缓存 60 秒，查额度不耗额度）：池里任何一项低于 10% 就拒绝调用，由 Claude 自己做；Gemini 5 小时额度低于 25% 时拒绝 neon-high 和 xhigh，报错里建议改用 argon-high（独立额度）或 neon-medium。查额度本身失败时照常调用。被额度拒绝的调用不记日志。
  - 单次时间上限是本工具设的（agy 默认不限时）：MCP 调用 15 分钟，命令行 9 分钟（低于 Claude 的 Shell 工具 10 分钟上限）。neon-high 读 100KB 以上材料时光思考就常要 3 分钟以上，用 neon-high、xhigh、argon 时每次的材料最好控制在 150KB 以内。
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
| `print timeout … the response is partial` | 超过时间上限。换小一档模型或拆分材料，不要原样重试：耗时主要在思考上 |
| `prompt too long for the Windows command line` | 提示词超过约 3.2 万字符。把材料写进文件，用 `files` 传 |
| `model … is not allowed` | 以 `-` 开头的模型名和 orchestrator 模型被拒；从分档表里选 |
| `Gemini quota low` 或 `needs the Gemini five-hour quota` | 额度不够，按报错换模型，或者等重置时间过后再用 |
| `invalid model selection` | 模型名写错了；运行 `agy models` 看当前可用的名字 |
| 结果第一行有 `denied=N` | 守门钩子拒绝了 agy 的 N 次操作；在日志的 `tool_calls` 里看具体是哪些、为什么 |
| 一个文件被 "looks like a … key" 拒绝 | 先看报错里的行号确认是不是真 Key。真 Key：从代码里移走并轮换；误报：只发其他文件，或者让 Claude 自己读 |
| `DEEPSEEK_API_KEY is not set` | 设成用户级环境变量后重启 Claude |
| DeepSeek 报 401 | Key 错误或已失效 |
| 503、504 | 服务端过载。Claude 应当自己做，不反复重试 |

## 7. 维护

- **不要随手升级 `mcp` 包**：mcp 1.x 到 2.x 改过接口（`FastMCP` 改名为 `MCPServer`），大版本升级先单独测试。
- **agy 更新**：`agy update`，之后跑一次 `ask_gemini.py --prompt "Reply with exactly: OK" --model gemini-4-hydrogen-low` 确认还能用。
- **模型改名**：改 `ask_gemini.py`、`ask_deepseek.py` 顶部的 `DEFAULT_MODEL`，以及 `workers_mcp.py` 工具描述里的分档表，然后重启 Claude。
- **停用**：退出 Claude 后从 `~/.claude.json` 删掉 `mcpServers.workers`，再删掉 CLAUDE.md 里那一节和 `~/.claude/agents/` 下的两个子代理文件。
