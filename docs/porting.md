# Roles and porting notes

This project is a small personal tool, not a control plane: one lead agent hands bounded jobs to workers and verifies what comes back. This page records the role model and what to keep in mind when adding another agent, with Codex as the worked example. **Verified** means tested on Windows with the versions named; **to verify** means it has not been tested yet.

## Roles

| Role | Today | Can change files | Owns |
|---|---|---|---|
| Lead | Claude Code | Yes | Decisions, implementation, verification, commits, merges, anything the user must approve |
| Reviewer worker | Gemini via agy (`ask_gemini`) | No | A second opinion with evidence |
| Executor worker | Not implemented (Codex is the candidate) | Only its own worktree | A change for the lead to review |

Skills follow the roles: shared skills sit in one folder that every agent reads; lead-only skills (they call tools only the lead has) are named `lead-*` and are kept from workers.

## Reviewer worker: how `ask_gemini` is isolated (verified, agy 1.5.14)

- **Private home.** agy runs with `--gemini_dir <private home>`, so the user's agy plugins, skills and `GEMINI.md` do not apply and the conversation database is separate. The private home's `GEMINI.md` is `reviewer_rules.md`. The Google sign-in stays in the OS keyring and keeps working. The `GEMINI_HOME` variable has no effect in print mode. Each call checks that the conversation landed in the private home and flags `NOT-ISOLATED` otherwise.
- **Workspace-only reads.** A workspace-local `PreToolUse` hook allows `read_file` inside the workspace (minus its own files and secret files) and `finish` (structured results); everything else is denied.
- **Skills in the workspace.** The shared skills are copied into the workspace's `.agents/skills/`. Suggested skills are named with their paths, because agy hides `disable-model-invocation` skills from the model's list.
- **Structured result.** `--json-schema review_schema.json`; the model hands the JSON back through `finish`.
- **Effect.** Fixed overhead per call fell from about 24k to about 11k tokens, because the user's ~170 plugin skills are no longer listed.

## Sharing skills with each agent

| Agent | Global skills folder | How it gets the shared skills | Status |
|---|---|---|---|
| Claude Code | `~/.claude/skills` | It is the single source | Verified |
| agy, the user's own sessions | `~/.gemini/config/skills/` | `~/.gemini/config/skills.json` with an entry for the shared folder; use an absolute path (agy 1.5.14 on Windows does not expand `~/`); `exclude` keeps `lead-*` out | Verified |
| agy inside `ask_gemini` | none (private home) | Copied into each call's workspace | Verified |
| Codex | `~/.codex/skills` (`CODEX_HOME`) | A copy or link per shared skill, made at install time | To verify |

## Executor worker: Codex (design, not implemented)

The lead keeps every decision that lands work: Codex changes files in a worktree of its own, the lead reviews the diff (optionally with a Gemini review through `lead-gemini-review`), and only the lead commits or merges.

### Flow

1. The lead commits or stashes its own edits, since a new worktree starts from a commit and uncommitted changes are not in it, or passes them as a patch.
2. `ask_codex` creates a worktree and branch from that commit.
3. Codex runs non-interactively in the worktree, may run the project's tests, and returns a summary.
4. `ask_codex` returns the summary, the diff and a check result (see the boundary below).
5. The lead verifies, optionally asks Gemini to review the diff, then merges or discards the worktree.

### Permission boundary

| Rule | How | Status |
|---|---|---|
| Writes only its own worktree | `codex exec -s workspace-write -C <worktree>` | Flags verified in `codex exec --help` (codex-cli 0.144.1); behaviour to verify |
| Network allowed | Sandbox network setting through `-c` | To verify (setting name) |
| No commit, push or merge | No flag for this: instruct it, then after the run reject the result if the worktree's `HEAD` moved or a push was attempted | To verify |
| Never broader than the lead | Do not use `--dangerously-bypass-approvals-and-sandbox` | Policy |
| No secrets in its environment | Scrub key and token variables as `ask_gemini` does, keeping only what Codex's own sign-in needs | To verify |
| Its own rules, not the user's | `--ignore-user-config` and `--ignore-rules` (sign-in still comes from `CODEX_HOME`), or a private `CODEX_HOME` | Flags verified in help; effect to verify |
| Structured result | `--output-schema <file>`, `-o <file>` for the last message, `--json` for the event stream | Flags verified in help |
| Time limit and cancel | Kill the process tree on timeout or cancel, as `ask_gemini` does | Design |

## Patterns taken from t3code

[pingdotgg/t3code](https://github.com/pingdotgg/t3code) is a full control plane for coding agents (server, desktop, phone, symmetric delegation between agents over an injected MCP server). This project borrows patterns from it rather than adopting it:

- A private home per agent instance (t3code sets `GEMINI_HOME` for agy's ACP mode; agy's print mode needs `--gemini_dir`).
- Each re-review is a new call that carries the original brief, the earlier findings, the responses to them and the objections still open.
- Results checked against a schema by the tool, so the caller does not parse prose.
- Delegated work runs in a worktree created from a commit; uncommitted parent edits are not copied.
- A child never runs with broader permissions than its parent.

If the executor line ever needs more than this (several agents delegating to each other, a UI, phones), running t3code itself is the alternative to growing this project.
