# agent-workers-mcp

**English** | [简体中文](README.zh-CN.md)

An MCP server that lets Claude Code act as the lead and hand part of the work to "worker" models:

- **Gemini**, through the Antigravity CLI (`agy`), on your Google AI subscription quota only. It **never falls back to the paid API**. Good for summarising many files, second-opinion reviews, and read-only reviews of a whole repository.
- **DeepSeek**, through its OpenAI-compatible API, billed per token. Good for bulk text work: drafting, rewriting, translating.
- Claude splits the task, writes the brief and verifies the result; a worker's answer is treated as a lead, not a fact.

## Contents

| File | Purpose |
|---|---|
| `workers_mcp.py` | The MCP server (stdio) with three tools: `ask_gemini`, `ask_deepseek`, `gemini_quota` |
| `ask_gemini.py` | Calls agy; also usable from the command line |
| `ask_deepseek.py` | Calls the DeepSeek API; also usable from the command line |
| `agy_guard.py` | agy `PreToolUse` hook that makes every call read-only |
| `reviewer_rules.md` | The only rules agy loads for these calls (it becomes `GEMINI.md` in agy's private home) |
| `review_schema.json` | JSON schema for `structured` review results |
| `worker_common.py` | Shared secret checks, size limit and call log |
| `agents/` | Two Claude Code subagents: `gemini-reviewer` (second-opinion review) and `deepseek-drafter` (drafts and translations) |
| `skills/` | Lead-only Claude Code skills: `lead-gemini-review` (plan and diff review loop with verification) |
| `docs/manual.md` | User manual: usage, model tiers, logs, troubleshooting (Chinese: `docs/manual.zh-CN.md`) |
| `docs/claude-md-snippet.md` | A section to add to `~/.claude/CLAUDE.md` telling Claude when to hand work to the workers |
| `docs/porting.md` | Role model (lead, reviewer worker, executor worker) and notes for adding other agents such as Codex |
| `install.py` | Registers the MCP server and installs the subagents and skills |
| `LICENSE` | MIT license |

## Security design

- **Gemini can only read.** agy runs in plan mode (`--mode plan`) in a fresh temporary directory. Every call writes a workspace-local hook (`.agents/hooks.json`) there, and `agy_guard.py` rules on each action agy attempts:
  - only `read_file` is allowed, and only for files inside the workspace that pass the secret checks below;
  - running commands, writing files, web access and everything else are denied, and so is anything the hook fails on;
  - the hook applies only to calls made by this server; your own use of agy is unaffected.
- **Reviews are isolated from your own agy setup.** agy runs with its own home (`~/.agent-workers/agy-home`, passed with agy's `--gemini_dir`), so your agy plugins, skills and `GEMINI.md` never reach a review; `reviewer_rules.md` does. Your Google sign-in still applies (agy keeps it in the OS keyring). The skills in `~/.claude/skills` are copied into each call's workspace for Gemini to choose from, except lead-only `lead-*` ones. If agy ever ignores its own home, the result says `NOT-ISOLATED`.
- **Secrets stay local.** Files are refused by name (`secrets.toml`, `.env*`, certificates and private keys, cloud credentials…), by folder (`.ssh`, `.aws`…) and by content (`sk-…`, `AIza…`, GitHub/AWS/Slack/Stripe/Hugging Face/npm tokens, JWTs, private-key blocks, connection strings with passwords, hard-coded secret assignments). Errors report only the kind and line number, never the value.
- **No accidental paid API use.** Every variable holding a key or token is removed from agy's environment. Each call also passes `--settings` to turn off the paid fallback (`useG1Credits`) and direct Gemini API access (`modelProvider`), without touching your own agy settings.
- **Quota protection.** Quota is checked before each call (cached for 60 s). A call is refused when any bucket of the model's pool is below 10%; `neon-high` and `neon-xhigh` are refused when the Gemini five-hour bucket is below 25%.
- **The call log stays on your machine.** `logs/YYYY-MM.jsonl` records every call actually sent (prompt, file fingerprints, answer, usage, every guard decision) and keeps 3 months. Requests refused by the security checks are not logged. `logs/` is git-ignored.

## Requirements

- Windows 10/11 (the installer and process handling use `tasklist`, `taskkill` and `.venv\Scripts`)
- Python 3.10+
- Google Antigravity CLI (`agy`), signed in with a Google account that has a Gemini subscription (run `agy` once in a terminal and follow the prompts)
- A DeepSeek API key (optional if you only use Gemini), set as the user environment variable `DEEPSEEK_API_KEY`
- Claude Code (desktop app or CLI)

## Installation

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

Then **quit Claude completely** and run:

```powershell
python install.py
```

> If the folder holding the repository carries a low-integrity label (some tools' sandboxes put one on a whole working folder; `icacls` shows `Low Mandatory Level`), programs started from the repository's `.venv` cannot read `%LOCALAPPDATA%` and so cannot find agy. Create the venv elsewhere, for example `python -m venv $HOME\.venvs\agent-workers-mcp`, and register it with `python install.py --python $HOME\.venvs\agent-workers-mcp\Scripts\python.exe`.

The installer backs up and edits `~/.claude.json` (adding `mcpServers.workers`), copies the two subagents into `~/.claude/agents/` and the skills in `skills/` into `~/.claude/skills/`. After you reopen Claude, new sessions have the three `mcp__workers__*` tools. Finally, add what you need from `docs/claude-md-snippet.md` to your `~/.claude/CLAUDE.md`.

Check both workers from the command line first (if your venv is outside the repository, replace `.\.venv\Scripts\python` with that venv's python):

```powershell
.\.venv\Scripts\python ask_gemini.py --prompt "Reply with exactly: OK" --model gemini-4-hydrogen-low
```
```powershell
.\.venv\Scripts\python ask_deepseek.py --prompt "Explain a moving average in one sentence"
```

## Usage

Just ask Claude, for example:

- "Have Gemini review `D:\path\to\repo` in repo mode, focusing on error handling"
- "Have gemini-reviewer review `bar()` in `src/foo.py`"
- "Get a Gemini review of this plan" (Claude follows the `lead-gemini-review` skill: it suggests skills, asks for structured findings and verifies each one)
- "Use ask_deepseek to translate this into English"
- "Check the Gemini quota"

For choosing models, reading the logs and handling common errors, see [docs/manual.md](docs/manual.md).

## Known limitations

- Tested on Windows only.
- Gemini and DeepSeek model names change. The default models are `DEFAULT_MODEL` at the top of `ask_gemini.py` and `ask_deepseek.py`; the tier table is in the tool descriptions in `workers_mcp.py`.
- In repo mode agy cannot run commands, so it cannot search the code; it picks files to read from `FILES.txt` (the output of `git ls-files`).
- Every Gemini call carries about 11k tokens of fixed agent overhead, so batch small questions into one call.
- Isolation relies on agy's `--gemini_dir`, which is not documented (tested with agy 1.5.14; agy ignores the `GEMINI_HOME` variable in print mode). Results flag `NOT-ISOLATED` if a later agy stops honouring it.

## License

[MIT](LICENSE). This project is only a tool that calls the Antigravity CLI and the DeepSeek API; your use of them is still subject to Google's and DeepSeek's terms of service.
