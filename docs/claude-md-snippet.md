# CLAUDE.md snippet

Add this section to `~/.claude/CLAUDE.md` (adjust the privacy line to your own repositories). It tells Claude when to hand work to the workers and when to keep it.

```markdown
## Multi-model workers (MCP server `workers`)

Tools: `ask_gemini` (Antigravity subscription quota), `ask_deepseek` (paid API), `gemini_quota`. Subagents: `gemini-reviewer` (second-opinion review), `deepseek-drafter` (drafts and translations).

- **Hand off**: summarising 10+ files, second opinions on a design or function, bulk drafting, rewriting or translation. For a whole-repository review, call `ask_gemini` with `repo` (absolute path): Gemini picks and reads the files itself under a read-only guard, so you only write the brief and verify the answer. Pick the model from the tier table in the `ask_gemini` / `ask_deepseek` tool descriptions, and tell the user before using `gemini-4-neon-xhigh`. `ask_gemini` checks quota itself and refuses calls the quota cannot afford; follow the model suggested in its error, or do the work yourself.
- **Call through MCP**: use the `ask_gemini` tool (up to an hour per call). Run `ask_gemini.py` from a shell only when there is a reason to, and then with `run_in_background` and `--timeout 3600`; its 540-second default is only for foreground runs.
- **Review loop**: for plan and diff reviews and independent proposals follow the `lead-gemini-review` skill (when to review, model, skill suggestions, `structured` output, verification, re-review rounds).
- **Skills**: `~/.claude/skills` is the single source shared with Gemini; `ask_gemini` copies it into Gemini's workspace (agy runs in its own home, without your agy plugins or GEMINI.md). Name lead-only skills (they call tools only Claude has) `lead-*` so workers never get them.
- **Keep**: code changes, decisions the user must make, anything touching secrets, and small tasks a few tool calls finish. Batch small questions into one call (each Gemini call carries ~11k tokens of agent overhead).
- **Verify**: worker output is a lead, not a fact. Re-check numbers and claims by running code or reading the source, and say in the reply which findings were verified and which were not.
- **Privacy**: never send keys or secret files (the tools also refuse them). Ask the user before sending code from a private repository, except: <list the repositories you have cleared>.
- **Failure**: if a worker errors or times out, do the work yourself; do not retry repeatedly.
```
