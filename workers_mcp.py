"""MCP server "workers": lets Claude hand work to Gemini (via agy, subscription quota) and DeepSeek (API).

Thin wrapper over ask_gemini.py / ask_deepseek.py; all logic and the secret-file denylist live there.
Worker output is a lead, not a fact: verify it before relying on it.
"""
from __future__ import annotations

import anyio
import anyio.to_thread
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ask_deepseek import ask_deepseek as _ask_deepseek
from ask_gemini import Cancel, ask_gemini as _ask_gemini, gemini_quota as _gemini_quota
from worker_common import WorkerError

server = MCPServer("workers")


def _with_meta(out: dict, extra: str = "") -> str:
    """Header line: model, time, tokens and the call id that finds this call in tools/logs/YYYY-MM.jsonl."""
    u = out["usage"]
    tokens = " ".join(f"{k}={v}" for k, v in u.items() if isinstance(v, int))
    reasoning = (u.get("completion_tokens_details") or {}).get("reasoning_tokens")  # DeepSeek nests this one
    if reasoning:
        tokens += f" reasoning_tokens={reasoning}"
    peak = " PEAK-2x-price" if out.get("peak") else ""
    return f"[{out['model']}{extra}] {out['elapsed_s']}s {tokens} id={out['call_id']}{peak}\n\n{out['text']}"


@server.tool()
async def ask_gemini(prompt: str, files: list[str] = [], model: str | None = None, repo: str | None = None,
                     skills: list[str] = [], structured: bool = False, proposal: bool = False) -> str:
    """Ask Gemini (Google AI Pro subscription quota, via the Antigravity CLI) for a read-only review. A guard hook
    lets agy only read files (no commands, no writes, no web) and refuses secret files. Two ways to give material:
      files  absolute paths; copies go into an empty temp dir (total up to 700KB).
      repo   absolute path of a repository; it is mounted read-only and agy picks what to read from a file list,
             so you only write the brief (what to review, what to look for). No size cap; nothing is written into
             the repository. Ask the user before sending a private repository unless their instructions allow it.
    agy runs in its own home: the user's agy plugins and GEMINI.md never apply; reviewer rules do. Every shared skill
    (~/.claude/skills minus lead-* ones) is available to Gemini, which picks what to read.
      skills      names of shared skills you suggest for this task (manual-only ones included); Gemini may reject
                  them or use others, and reports which it read, rejected and would add.
      structured  true: the answer is JSON with summary, findings (severity, location, claim, evidence, confidence),
                  skills_used, rejected_suggestions, suggested_skills. Use it for reviews you will verify item by item.
      proposal    true: instead of reviewing, Gemini drafts its own independent plan, as JSON with summary,
                  assumptions, decisions (each with topic, choice, rationale, alternatives, confidence and optional
                  evidence), steps,
                  risks, open_questions and the skills fields; implies structured. Brief it with the requirement,
                  constraints and material only: never your own plan or leanings, so its plan stays independent.
                  Write your plan before reading its answer, then compare them decision by decision.
    The `[model ...]` tag at the start of the header line adds `denied=N` when the guard refused some of agy's
    attempts, NOT-ISOLATED if agy ignored its own home (the user's plugins and GEMINI.md may then have applied; say
    so when reporting), and JSON-INVALID when a structured answer did not parse (the raw text follows).

    Pick `model` by task (default gemini-4-neon-medium):
      gemini-4-hydrogen-low     classify, extract, short summaries, format conversion
      gemini-4-hydrogen-medium  summarise 10+ files, doc Q&A, explain code
      gemini-4-neon-medium      second opinion on one function or module
      gemini-4-neon-high        multi-file review, architecture review, hard bug hypotheses
      gemini-4-neon-xhigh       maths/statistics correctness, hardest reasoning; tell the user before using
      gemini-4-argon-high       stand-in for neon-high/xhigh when the Gemini pool is low (separate quota)
    Quota is checked automatically before each call: any bucket of the model's pool under 10% -> the call is
    refused, do the work yourself and tell the user; Gemini five-hour bucket under 25% -> neon-high/xhigh are
    refused, retry with gemini-4-argon-high (separate quota) or gemini-4-neon-medium as the error suggests.
    Each call carries ~11k tokens of agent overhead, so batch small questions into one call.
    A call can take up to an hour (Esc stops it at once): neon-high on 100KB+ of input often thinks for 3+
    minutes, and repository reviews on neon-high/xhigh have taken up to ~15. Files may total
    700KB, but for neon-high, neon-xhigh or argon keep each call under ~150KB and split larger reviews by module.
    After a timeout, drop a level or split the input instead of retrying the same call.
    Do not hand over code changes, user decisions or anything involving secrets.
    The answer is a lead: verify numbers and claims yourself before using them.
    """
    # Async so a client cancel (Esc) reaches us at once: the blocking call runs in a thread we abandon,
    # and Cancel kills agy so it stops spending quota. A sync tool would wait for agy to finish.
    cancel = Cancel()
    try:
        out = await anyio.to_thread.run_sync(
            lambda: _ask_gemini(prompt, files, model, cancel=cancel, repo=repo, skills=skills, structured=structured,
                                proposal=proposal),
            abandon_on_cancel=True)
    except anyio.get_cancelled_exc_class():
        cancel.cancel()
        raise
    except WorkerError as e:
        raise ToolError(str(e))
    denied = out.get("denied") or []
    extra = ((f" denied={len(denied)}" if denied else "") + ("" if out.get("isolated") else " NOT-ISOLATED")
             + (" JSON-INVALID" if (structured or proposal) and out.get("data") is None else ""))
    return _with_meta(out, extra)


@server.tool()
def ask_deepseek(prompt: str, files: list[str] = [], model: str | None = None, effort: str = "none",
                 system: str | None = None) -> str:
    """Ask DeepSeek (pay per token, default model deepseek-flash) for cheap bulk text work: drafting, rewriting,
    translating. `files` are absolute paths whose text is sent with the prompt; secret files are refused.

    `effort` sets thinking (thinking tokens are billed as output):
      none  translate, rewrite, format (default)
      low   draft docs and comments
      high / max  rarely needed for this worker
    Output is a draft for you to review; never write it into a repo unchecked.
    """
    try:
        return _with_meta(_ask_deepseek(prompt, files, model, system, effort=effort), f" effort={effort}")
    except WorkerError as e:
        raise ToolError(str(e))


@server.tool()
def gemini_quota() -> str:
    """Remaining Gemini subscription quota per bucket (this check spends no quota). ask_gemini already checks
    quota itself; call this only when the user asks or to plan a series of large reviews."""
    try:
        rows = _gemini_quota()
    except WorkerError as e:
        raise ToolError(str(e))
    return "\n".join(f"{r['group']} | {r['bucket']}: {r['remaining_pct']}% (resets {r['reset']})" for r in rows)


if __name__ == "__main__":
    server.run(transport="stdio")
