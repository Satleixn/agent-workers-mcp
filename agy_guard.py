"""agy PreToolUse hook that keeps every ask_gemini run a read-only review.

ask_gemini.py writes <temp workspace>/.agents/hooks.json pointing here, with a guard.json next to it, so the guard
applies only to our own agy runs (a global hook would change the user's own agy sessions too).

For each tool call agy wants to make:
  read_file  allowed only for a file under one of the configured roots that passes the same secret checks as
             files we send ourselves (name and directory denylist, content scan, size limit);
  anything else (run_command, file writes, web access, ...) is denied.
Any error in this script also ends in a denial: agy treats a failed hook as a block (tested 2026-11-20), and the
script prints an explicit deny before exiting when it can.

Usage (by agy only): python agy_guard.py <path to guard.json>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from worker_common import WorkerError, check_files  # noqa: E402

READ_TOOLS = {"read_file"}


def decide(payload: dict, cfg: dict) -> tuple[str, str, str | None]:
    """Return (decision, reason, path) for one PreToolUse payload."""
    call = payload.get("toolCall") or {}
    name = call.get("name") or ""
    args = call.get("args") or {}
    if name not in READ_TOOLS:
        return "deny", f"{name or 'this tool'} is not allowed: this is a read-only review", None
    raw = args.get("Path")
    if not raw:
        return "deny", "read_file without a Path", None
    path = Path(raw).resolve()
    roots = [Path(r).resolve() for r in cfg["roots"]]
    if not any(path.is_relative_to(root) for root in roots):
        return "deny", "outside the workspace", str(path)
    if path.is_relative_to(Path(cfg["guard_dir"]).resolve()):
        return "deny", "the review's own configuration is not readable", str(path)
    try:
        check_files([path])
    except WorkerError as e:
        return "deny", str(e), str(path)
    return "allow", "", str(path)


def main() -> None:
    try:
        cfg_path = Path(sys.argv[1])
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        decision, reason, path = decide(payload, cfg)
        tool = (payload.get("toolCall") or {}).get("name")
        with open(cfg_path.with_name("guard_log.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"tool": tool, "path": path, "decision": decision, "reason": reason},
                               ensure_ascii=False) + "\n")
    except Exception as e:  # fail closed
        decision, reason = "deny", f"guard error: {type(e).__name__}"
    print(json.dumps({"decision": decision, "reason": reason}))


if __name__ == "__main__":
    main()
