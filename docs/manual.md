# User manual

**English** | [简体中文](manual.zh-CN.md)

Below, `<repo>` is the folder you cloned this repository into, and `<repo>\.venv\Scripts\python` is the Python of the venv with the dependencies installed (use that venv's path if you created it elsewhere). For installation, see the [README](../README.md).

## 1. Everyday use

Once [claude-md-snippet.md](claude-md-snippet.md) is in your `~/.claude/CLAUDE.md`, Claude decides by itself when to hand work to a worker. To choose yourself, just say so:

| You want to | Say to Claude |
|---|---|
| Translate or rewrite some text | "Use ask_deepseek to translate this into English" |
| Draft docs or comments | "Have deepseek-drafter draft a README for `xxx.py`" |
| Get a second opinion | "Have gemini-reviewer review `foo()` in `src/xxx.py`" |
| Summarise a pile of files | "Use ask_gemini (hydrogen-medium) to summarise the `services/` folder" |
| Review a whole repository, letting Gemini pick the files | "Have Gemini review `D:\…\some-repo` in repo mode, focusing on …" |
| Review a branch, a checked-out PR or uncommitted work | "Have Gemini review this branch's changes against main" |
| See the Gemini quota | "Check the Gemini quota" |
| Pick the model | Name it, e.g. "Use neon-high to review …" |

After getting a result, Claude should verify it by running code or reading the source, and say which points were verified and which were not. **A worker's answer is only a lead.**

### Two ways to give Gemini material

- **`files`**: a few named files, copied into the temporary directory's `files/` folder (keeping their relative paths, and apart from agy's own `.agents` folder). Up to 700KB in total (about 200k tokens).
- **`repo`**: the whole repository is mounted read-only (`--add-dir`), and the temporary directory gets a file list, `FILES.txt` (the output of `git ls-files`, minus secret files and dependency folders); agy picks what to read from it. No 700KB cap, and nothing is written into the repository.
- **`repo` + `base`**: a review of a change. `base` is a git ref (`main`, `origin/main`); the change runs from the merge base of `base` and `HEAD` to the working tree, so it covers a branch's commits and uncommitted edits together. The temporary directory also gets `DIFF.patch` (the diff) and `CHANGES.txt` (each changed file with its status, new untracked files marked `?`). Secret files and dependency folders are left out of both. Renames count as delete plus add: the side under a secret old name stays out, while the new name is checked like any other file (by name and content, as in plain `repo` mode). The diff passes the content checks (section 4) and shares the 700KB cap with `files`. A GitHub PR has to be checked out first (for example with `gh pr checkout` in a worktree); pass that checkout as `repo`. `base` cannot be combined with `proposal`.

### Skills and structured results

- **Shared skills**: `~/.claude/skills` is the one place for skills Claude and Gemini share. Every call copies those folders (except lead-only `lead-*` ones) into the temporary directory's `.agents/skills/`, so Gemini sees them all and reads the ones it judges relevant. Your own agy plugins and skills are not used (see section 4).
- **`skills`**: the shared skills Claude suggests for this task, passed with their paths, so manual-only skills (`disable-model-invocation`) work too. Gemini may reject a suggestion or use other skills, and reports which it read, rejected and would add.
- **`structured`**: the answer is JSON matching `review_schema.json`: `summary`, `findings` (each with `severity`, `location`, `claim`, `evidence`, `confidence`, optional `suggestion`), `skills_used`, `rejected_suggestions`, `suggested_skills`. Claude uses it for reviews it verifies item by item.
- **`proposal`**: instead of reviewing, Gemini drafts its own independent plan, as JSON matching `proposal_schema.json`: `summary`, `assumptions`, `decisions` (each with `topic`, `choice`, `rationale`, `evidence`, `alternatives`, `confidence`), `steps`, `risks`, `open_questions` and the three skills fields. It implies `structured`. Claude briefs it with the requirement and the code only, never its own plan, writes its own plan first, then compares the two decision by decision.
- **Review loop**: the `lead-gemini-review` skill (installed into `~/.claude/skills` by `install.py`) tells Claude when to ask for a plan or diff review or an independent proposal, how to brief it, how to verify the findings and how to run a re-review.

### Model tiers (Claude chooses from this table by default)

| Tier | Model | Good for |
|---|---|---|
| 0 | Claude itself | Code changes, decisions that are yours to make, anything touching secrets, small jobs |
| 1 | `deepseek-flash` (effort `none` for translating and rewriting, `low` for drafting) | Bulk text |
| 2 | `gemini-4-hydrogen-low` | Classification, extraction, short summaries |
| 3 | `gemini-4-hydrogen-medium` | Summarising 10+ files, questions about documents |
| 4 | `gemini-4-neon-medium` (default) | Reviewing a single function or module |
| 5 | `gemini-4-neon-high` | Cross-file review, architecture review |
| 6 | `gemini-4-neon-xhigh` | Correctness arguments for maths and statistics; Claude should tell you before using it |
| Fallback | `gemini-4-argon-high` | Stands in for tiers 5 and 6 when the Gemini pool runs low (Argon has its own quota) |

## 2. Command-line use (without Claude)

```powershell
<repo>\.venv\Scripts\python <repo>\ask_deepseek.py --prompt "Translate into French: ..." --effort none
```
```powershell
<repo>\.venv\Scripts\python <repo>\ask_gemini.py --prompt "Summarise this file" --file 'D:\path\a.py' --model gemini-4-hydrogen-low
```
```powershell
<repo>\.venv\Scripts\python <repo>\ask_gemini.py --prompt "Describe this repository's module structure" --repo 'D:\path\some-repo' --model gemini-4-hydrogen-medium
```
```powershell
<repo>\.venv\Scripts\python <repo>\ask_gemini.py --prompt "Review the animations" --file 'D:\path\modal.css' --skill review-animations --structured
```
```powershell
<repo>\.venv\Scripts\python <repo>\ask_gemini.py --prompt "Review this change, focusing on error handling" --repo 'D:\path\some-repo' --base main --model gemini-4-neon-high --structured
```
```powershell
<repo>\.venv\Scripts\python <repo>\ask_gemini.py --prompt "Requirement: ... Constraints: ... Draft your plan" --repo 'D:\path\some-repo' --model gemini-4-neon-high --proposal
```
```powershell
<repo>\.venv\Scripts\python <repo>\ask_gemini.py --quota
```

The answer goes to stdout; usage, the call id and any actions the guard hook refused go to stderr. `--file` and `--skill` can be given several times; `--structured` asks for the JSON review result, `--proposal` for Gemini's own plan as JSON. `--timeout` sets the time limit in seconds: the default of 540 keeps a foreground run under the 10-minute limit of Claude's shell tool; for long reviews start the script in the background with `--timeout 3600`. DeepSeek also takes `--system "..."` and `--json` (asks for a JSON reply).

## 3. Inspecting the raw inputs and outputs

Every call that is **actually sent** (successful or not) adds one line to `<repo>\logs\YYYY-MM.jsonl`: the prompt as sent, file fingerprints (path, size, sha256), the full answer or error, usage and elapsed time.

- Gemini calls also record `agy_transcript` (agy's own full record, including its thinking and every step), `tool_calls` (the guard's decision on each action agy tried, including which `SKILL.md` files Gemini read), `repo` in repo mode, `base`, `merge_base`, `diff` (size and sha256) and `diff_excluded` (files left out) when `base` is set, `quota_check`, `skills_suggested`, `structured`, `proposal` and `isolated`.
- DeepSeek calls also record the thinking text `reasoning` and whether the call fell in peak hours (`peak`).

The first line of an MCP result ends with `id=…`. To look a call up by id:

```powershell
$id = 'paste the id here'; Get-ChildItem -LiteralPath '<repo>\logs' -Filter '*.jsonl' | Select-String -SimpleMatch $id | ForEach-Object { $_.Line | ConvertFrom-Json } | Format-List
```

- Requests refused by the security checks are **not logged** (they may contain the very secret that was caught).
- Only the last 3 months of log files are kept; older ones are deleted when a call is logged. `logs/` is git-ignored.
- agy keeps its own records of these calls under `~/.agent-workers/agy-home/antigravity-cli/` (its private home). agy manages them; this tool only keeps that home's `GEMINI.md` equal to `reviewer_rules.md`.

## 4. Security rules (in the code, so Claude cannot get around them)

- **Refused by name and location**: `secrets.toml`, every kind of `.env`, certificates and private keys (`*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.jks`, `id_rsa*` and so on), `credentials.json`, `service_account*.json`, `.netrc`, `.npmrc`, `.pypirc`, `.git-credentials`, `.claude.json`; files under `.streamlit`, `.ssh`, `.aws`, `.kube`, `.gnupg`, `.azure` and `.gcloud` folders; anything in `~/.gemini`, `~/.agent-workers/agy-home` or this repository's `logs/`.
- **Refused by content**: anything that looks like a key in file contents, the prompt or the system prompt. This covers the key and token formats of the major providers, JWTs, long `Bearer` strings, private-key blocks, connection strings with passwords, `xxx_api_key = "long string"`, and the unquoted forms in `.env` and YAML files. Assignments to names ending in `_path`, `_file`, `_dir`, `_url` or `_name` do not count (they hold locations). Errors give only the kind and the line number.
- **Gemini is read-only**: each call installs the guard hook `agy_guard.py` in its temporary directory. It allows only `read_file` on files inside the workspace that pass the checks above (the guard's own files under `.agents/guard` and `.agents/hooks.json` excepted), plus, on `structured` and `proposal` calls only, `finish`, which agy uses to hand back the result; running commands, writing files, web access and everything else are denied, and so is anything the hook fails on. `--mode plan` plus an empty temporary directory is not enough on its own: in tests agy still read files outside the workspace, hence the hook.
- **Isolated from your own agy**: agy runs with `--gemini_dir ~/.agent-workers/agy-home`, a home with no plugins whose `GEMINI.md` is `reviewer_rules.md`. Your agy plugins, skills and `GEMINI.md` therefore never apply to these calls, and they no longer share agy's conversation database with your own sessions. Sign-in is unaffected (agy keeps it in the OS keyring). `--gemini_dir` is undocumented (tested with agy 1.5.14; the `GEMINI_HOME` variable has no effect in print mode), so every call checks that agy really used that home and flags `NOT-ISOLATED` if not.
- **No accidental paid API use**: every variable whose name contains key, token, secret, password or credential is removed from agy's environment. Each call also uses `--settings` to force `useG1Credits: false` (no paid AI credits once the subscription quota runs out) and `modelProvider: ""` (no direct Gemini API). These settings apply only to this tool's calls and are never written to your agy settings. Slash commands are disabled too.
- **Git for `base` reviews** runs with `--no-ext-diff --no-textconv` and `core.fsmonitor=false`, so neither an external diff tool nor a text converter from the repository's config runs. Clean filters (for example git-lfs) still apply: they come from your own git config, never from a clone, and turning them off would break LFS diffs. Point `base` only at repositories whose `.git/config` you trust, as you would before running `git diff` in them yourself.
- **Which repositories may be sent** is decided by your CLAUDE.md (see the Privacy line in the snippet). The code enforces only the no-secrets rule and does not restrict folders.

## 5. Cost, quota and time

- **Gemini** uses subscription quota only. Neon and Hydrogen share one pool (a weekly and a five-hour allowance); Argon has a separate weekly pool. Every call carries about 11k tokens of fixed agent overhead (about 24k before agy got its own home and stopped loading your plugins' skill lists).
  - Before each call the code checks the quota of the model's pool (cached for 60 s; checking spends no quota). If any bucket in the pool is below 10% the call is refused and Claude does the work itself. If the Gemini five-hour bucket is below 25%, neon-high and xhigh are refused and the error suggests argon-high (separate quota) or neon-medium. If the quota check itself fails, the call goes ahead. Calls refused for quota are not logged.
  - The time limit per call is set by this tool (agy has none by default): 1 hour through MCP; on the command line 9 minutes by default (under the 10-minute limit of Claude's shell tool), up to 1 hour with `--timeout 3600` when run in the background. Repository reviews on neon-high or xhigh have taken up to about 15 minutes, and a dropped connection can cost agy several more minutes before it retries. With neon-high, xhigh or argon still keep each call under about 150KB.
  - The quota check runs `agy /quota` with its own 60-second limit and does not wait for a call already in progress.
  - Pressing Esc in Claude stops agy at once, so no more quota is spent.
- **DeepSeek** is billed per token, and thinking tokens are billed as output. DeepSeek charges double on weekdays 01:00–04:00 and 06:00–10:00 UTC; calls in those hours have `PEAK-2x-price` at the end of the result's first line. DeepSeek's website has the current prices.

## 6. Troubleshooting

| Symptom | What to do |
|---|---|
| No `mcp__workers__*` tools in a new session | Check that `~/.claude.json` has `mcpServers.workers`; quit Claude completely and reopen it |
| Code changed but behaviour did not | The MCP server loads the code when Claude starts, so restart Claude. The command line needs no restart |
| A new or edited subagent file has no effect | Subagents load when a session starts; open a new session |
| Gemini cannot find agy | Run `agy --version` in a terminal; reinstall the Antigravity CLI if that fails |
| Gemini reports a sign-in or permission problem | Run `agy` in a terminal and sign in again with your Google account |
| `failed to start conversation` | An occasional local start-up problem in agy, before any model call, so no quota is spent. The script retries once automatically |
| `print timeout … the response is partial` | The time limit was reached. Check agy's record (`agy_transcript` in the log) for an `ERROR_MESSAGE` step after a long gap: that is a network drop, and retrying once is fine. Otherwise use a lower tier or split the material, since the time went into thinking |
| `prompt too long for the Windows command line` | The prompt is over about 32,000 characters. Put the material in files and pass them with `files` |
| `model … is not allowed` | Model names starting with `-` and orchestrator models are refused; pick one from the tier table |
| `Gemini quota low` or `needs the Gemini five-hour quota` | Not enough quota. Switch model as the error suggests, or wait until the reset time |
| `invalid model selection` | Wrong model name; run `agy models` for the current names |
| The result's first line has `denied=N` | The guard hook refused N of agy's actions; the log's `tool_calls` shows which ones and why |
| The result's first line has `NOT-ISOLATED` | agy did not use its private home (an agy update may have changed `--gemini_dir`), so your own agy plugins and `GEMINI.md` may have applied. Check `agy --help` and the newest log under `~/.gemini/antigravity-cli/log` for the data directory it used |
| `unknown or lead-only skill(s)` | The name is not a folder with a `SKILL.md` in `~/.claude/skills`, or starts with `lead-`; the error lists the shared skills |
| The result's first line has `JSON-INVALID` | A `structured` answer did not parse; the raw text follows. If the log's `tool_calls` shows a denied `finish`, the running server predates this version, so restart Claude |
| A file is refused with "looks like a … key" | Check the line number in the error to see whether it is a real key. A real key: move it out of the code and rotate it. A false positive: send only the other files, or let Claude read it |
| `DEEPSEEK_API_KEY is not set` | Set it as a user environment variable, then restart Claude |
| DeepSeek returns 401 | The key is wrong or no longer valid |
| 503 or 504 | The service is overloaded. Claude should do the work itself rather than keep retrying |

## 7. Maintenance

- **Don't upgrade the `mcp` package casually**: its interface changed between 1.x and 2.x (`FastMCP` became `MCPServer`), so test a major upgrade on its own first.
- **Updating agy**: run `agy update`, then `ask_gemini.py --prompt "Reply with exactly: OK" --model gemini-4-hydrogen-low` to confirm it still works.
- **Renamed models**: change `DEFAULT_MODEL` at the top of `ask_gemini.py` and `ask_deepseek.py`, and the tier table in the tool descriptions in `workers_mcp.py`, then restart Claude.
- **Uninstalling**: after quitting Claude, remove `mcpServers.workers` from `~/.claude.json`, then remove the section from your CLAUDE.md, the two subagent files in `~/.claude/agents/`, the `lead-gemini-review` folder in `~/.claude/skills/`, and `~/.agent-workers/` (agy's private home for these calls).
