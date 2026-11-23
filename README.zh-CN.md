# agent-workers-mcp

[English](README.md) | **简体中文**

让 Claude 当主脑、把一部分工作交给"工人"模型的 MCP 服务：

- **Gemini**：通过 Antigravity CLI（`agy`）调用，只用 Google 订阅额度，**不会自动改走付费 API**。适合读大量文件后做总结、第二意见审查、整个仓库的只读审查。
- **DeepSeek**：直接调 OpenAI 兼容 API，按 token 付费。适合起草、改写、翻译这类批量文字工作。
- Claude 负责拆任务、写任务说明、核验结果；工人的回答只当线索。

## 组成

| 文件 | 作用 |
|---|---|
| `workers_mcp.py` | MCP 服务（stdio），提供 `ask_gemini`、`ask_deepseek`、`gemini_quota` 三个工具 |
| `ask_gemini.py` | 调用 agy；也可以直接在命令行用 |
| `ask_deepseek.py` | 调用 DeepSeek API；也可以直接在命令行用 |
| `agy_guard.py` | agy 的 `PreToolUse` 钩子：每次调用都强制只读 |
| `reviewer_rules.md` | 这些调用里 agy 唯一加载的规则（成为 agy 私有 home 里的 `GEMINI.md`） |
| `review_schema.json` | `structured` 审查结果的 JSON schema |
| `worker_common.py` | 共用的密钥检查、大小上限、调用日志 |
| `agents/` | 两个 Claude Code 子代理：`gemini-reviewer`（第二意见审查）、`deepseek-drafter`（起草和翻译） |
| `skills/` | 只给主脑用的 Claude Code skill：`lead-gemini-review`（计划审和 diff 审的循环，含逐条核实） |
| `docs/manual.zh-CN.md` | 操作说明书：用法、模型分档、日志、常见问题（英文版 `docs/manual.md`） |
| `docs/claude-md-snippet.md` | 建议加进 `~/.claude/CLAUDE.md` 的一节，告诉 Claude 什么时候交给工人 |
| `docs/porting.md` | 角色模型（主脑、审查型工人、执行型工人）和接入其他 agent（如 Codex）的要点（英文） |
| `install.py` | 注册 MCP 服务，安装子代理和 skill |
| `LICENSE` | MIT 许可证 |

## 安全设计

- **Gemini 只能读**：agy 在新建的临时目录里以 `--mode plan` 运行；每次调用都在临时目录写一份工作区本地钩子（`.agents/hooks.json`），由 `agy_guard.py` 判定 agy 的每一次操作：
  - 只放行 `read_file`，而且文件必须在工作区内，并通过下面的密钥检查；
  - 执行命令、写文件、访问网页等其他一切操作都拒绝；钩子自己出错也拒绝。
  - 这个钩子只对本服务发起的调用生效，不影响你自己平时用 agy。
- **审查与你自己的 agy 配置隔离**：agy 用自己的 home 运行（`~/.agent-workers/agy-home`，通过 agy 的 `--gemini_dir` 传入），你的 agy 插件、skill 和 `GEMINI.md` 都不会进入审查，生效的是 `reviewer_rules.md`。Google 登录照常可用（agy 把它存在系统钥匙串里）。`~/.claude/skills` 里的 skill（`lead-*` 这类只给主脑用的除外）每次复制进工作区，由 Gemini 自己挑选。万一 agy 没用上自己的 home，结果会标 `NOT-ISOLATED`。
- **密钥不外发**：按文件名（`secrets.toml`、`.env*`、证书和私钥、云服务凭据……）、目录（`.ssh`、`.aws`……）和内容（`sk-…`、`AIza…`、GitHub/AWS/Slack/Stripe/Hugging Face/npm 令牌、JWT、私钥块、带密码的连接串、硬编码的密钥赋值）拦截。报错只给类型和行号，不显示内容。
- **不会误走付费 API**：传给 agy 的环境变量去掉了所有 Key 和令牌；每次调用都用 `--settings` 关掉付费兜底（`useG1Credits`）和 Gemini API 直连（`modelProvider`），不改你自己的 agy 设置。
- **额度保护**：每次调用前自动查一次额度（缓存 60 秒）。模型所在额度池里任何一项低于 10% 就拒绝；Gemini 5 小时额度低于 25% 时，拒绝 neon-high 和 xhigh。
- **调用日志只在本机**：`logs/YYYY-MM.jsonl` 记下每次真正发出的调用（提示词、文件指纹、回答、用量、钩子的每次判定），保留 3 个月；被安全检查拒绝的请求不记录。`logs/` 不进 git。

## 要求

- Windows 10/11（安装脚本和进程管理用到了 `tasklist`、`taskkill`、`.venv\Scripts`）
- Python 3.10+
- Google Antigravity CLI（`agy`），已经用有 Gemini 订阅的 Google 账号登录（在终端运行一次 `agy` 按提示登录）
- DeepSeek API Key（只用 Gemini 的话可以不要），设为用户级环境变量 `DEEPSEEK_API_KEY`
- Claude Code（桌面版或 CLI）

## 安装

```powershell
git clone https://github.com/Satleixn/agent-workers-mcp.git
```
```powershell
cd agent-workers-mcp
```
```powershell
python -m venv .venv
```
```powershell
.\.venv\Scripts\pip install -r requirements.txt
```

然后**完全退出 Claude**，运行：

```powershell
python install.py
```

> 如果仓库所在的目录带有低完整性标签（某些工具的沙箱会给整个工作目录加上这个标签，用 `icacls` 能看到 `Low Mandatory Level`），从仓库里的 `.venv` 启动的程序读不到 `%LOCALAPPDATA%`，也就找不到 agy。这时把 venv 建在别处，例如 `python -m venv $HOME\.venvs\agent-workers-mcp`，再用 `python install.py --python $HOME\.venvs\agent-workers-mcp\Scripts\python.exe` 注册。

它会备份并修改 `~/.claude.json`（加入 `mcpServers.workers`），把两个子代理复制到 `~/.claude/agents/`，把 `skills/` 里的 skill 复制到 `~/.claude/skills/`。重新打开 Claude 后，新会话里就有 `mcp__workers__*` 三个工具。最后，把 `docs/claude-md-snippet.md` 的内容按需加进你的 `~/.claude/CLAUDE.md`。

先在命令行确认两边都能用（venv 不在仓库里时，把 `.\.venv\Scripts\python` 换成你那个 venv 的 python）：

```powershell
.\.venv\Scripts\python ask_gemini.py --prompt "Reply with exactly: OK" --model gemini-4-hydrogen-low
```
```powershell
.\.venv\Scripts\python ask_deepseek.py --prompt "用一句话说明什么是移动平均线"
```

## 用法

平时直接对 Claude 说，例如：

- "让 Gemini 以读仓库模式审查 `D:\path\to\repo`，重点看错误处理"
- "让 gemini-reviewer 审查 `src/foo.py` 里的 `bar()`"
- "让 Gemini 审一下这个计划"（Claude 按 `lead-gemini-review` skill 执行：建议 skill、要求结构化结论、逐条核实）
- "用 ask_deepseek 把这段翻成英文"
- "查一下 Gemini 额度"

模型怎么选、日志怎么查、常见报错怎么处理，见 [docs/manual.zh-CN.md](docs/manual.zh-CN.md)。

## 已知限制

- 只在 Windows 上测试过。
- Gemini 和 DeepSeek 的模型名会变，默认模型写在 `ask_gemini.py`、`ask_deepseek.py` 顶部的 `DEFAULT_MODEL`，分档表在 `workers_mcp.py` 的工具描述里。
- 读仓库模式下 agy 不能执行命令，所以不能全文搜索；它按 `FILES.txt`（`git ls-files` 的结果）挑文件读。
- 每次 Gemini 调用固定有约 1.1 万 token 的代理开销，小问题要合并成一次问。
- 隔离依赖 agy 的 `--gemini_dir`，这个参数没有公开文档（在 agy 1.5.14 上实测；print 模式下 `GEMINI_HOME` 环境变量无效）。将来的 agy 如果不再支持它，结果会标 `NOT-ISOLATED`。

## 许可证

[MIT](LICENSE)。本项目只是调用 Antigravity CLI 和 DeepSeek API 的工具，使用时仍须遵守 Google 和 DeepSeek 各自的服务条款。
