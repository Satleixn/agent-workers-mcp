"""Ask Gemini through the Antigravity CLI (agy), on the Google AI Pro subscription quota.

Never falls back to the paid Gemini API. agy is a full agent (it can run commands and write files), so every call
is a read-only review: agy runs in a fresh temp directory in plan mode, and agy_guard.py (a PreToolUse hook placed
in that directory) allows only read_file on files inside the workspace that pass the secret checks, and denies every
other tool. Two ways to give it material:
  files  copies of the given files go into the temp directory (total capped by MAX_TOTAL_BYTES);
  repo   a repository is mounted read-only with --add-dir and agy picks what to read from FILES.txt, a list of the
         repository's files; nothing is written into the repository.

Model choice (see docs/manual.md):
  gemini-4-hydrogen-low     fast: classify, extract, short summaries
  gemini-4-hydrogen-medium  everyday: summarise 10+ files, doc Q&A, explain code
  gemini-4-neon-medium      second opinion on one function or module (default)
  gemini-4-neon-high        multi-file review, architecture review, hard bug hypotheses
  gemini-4-neon-xhigh       maths/statistics correctness, hardest reasoning (tell the user first)
  gemini-4-argon-high       stand-in for neon-high/xhigh when the Gemini pool runs low (separate quota)
Each call costs ~25k input tokens of agent overhead, so batch small questions into one call.

Usage:
  python ask_gemini.py --prompt "task" [--file a.py --file b.md] [--repo D:\\path\\to\\repo] [--model ...]
  python ask_gemini.py --quota
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from worker_common import WorkerError, check_files, check_text, file_records, is_denied_path, log_call

DEFAULT_MODEL = "gemini-4-neon-medium"
# neon-high reviewing 100KB+ of input can think for 3+ minutes; 540 + 30 s stays under a shell tool's 10-minute cap.
PRINT_TIMEOUT_S = 540
READ_ONLY_NOTE = (
    "The files in your working directory are reference material: read them only, "
    "do not modify, create or delete files, and do not run commands.\n"
)
REPO_NOTE = (
    "The repository at {repo} is mounted read-only. FILES.txt in your working directory lists its files "
    "(path relative to the repository, then size in bytes). Read only the files you need with read_file, using "
    "absolute paths under the repository. You cannot run commands, search, or change anything; reads of secret "
    "files are refused.\n"
)
GUARD_SCRIPT = Path(__file__).resolve().with_name("agy_guard.py")
MAX_INDEX_FILES = 5000
# Directories never listed in FILES.txt (dependencies and build output, not the code under review).
INDEX_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", "dist",
                   "build"}
# Windows caps a whole command line at 32,767 characters, and the prompt travels on it (--print=...).
MAX_CMDLINE_CHARS = 32_000
# Environment variables agy never needs. Dropping them keeps our DeepSeek key away from it, and keeps a
# future GEMINI_API_KEY / GOOGLE_API_KEY from steering it to the paid API instead of the subscription.
_SECRET_ENV = re.compile(r"(?i)api_?key|token|secret|password|credential|^GOOGLE_(?:GENAI|CLOUD)_")
# agy's error when it cannot create its local conversation record; it fails before any model call.
START_FAILURE = "failed to start conversation"
# agy keeps shared per-user state (conversation index, summaries database), so this process runs one at a time.
_agy_lock = threading.Lock()


def _agy() -> str:
    found = shutil.which("agy")
    if found:
        return found
    fallback = Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"
    if fallback.is_file():
        return str(fallback)
    raise WorkerError("agy (Antigravity CLI) not found on PATH or in %LOCALAPPDATA%\\agy\\bin")


def _kill_tree(proc: subprocess.Popen) -> None:
    """Stop agy and every process it started; on Windows proc.kill() alone would leave its children running."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdin=subprocess.DEVNULL, capture_output=True)
    if proc.poll() is None:
        proc.kill()


class Cancel:
    """Lets another thread stop a running ask_gemini() call: cancel() kills agy at once, or keeps it from starting."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self.cancelled = False

    def attach(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._proc = proc
            already = self.cancelled
        if already:
            _kill_tree(proc)

    def cancel(self) -> None:
        with self._lock:
            self.cancelled = True
            proc = self._proc
        if proc is not None and proc.poll() is None:
            _kill_tree(proc)


def _run_agy(args: list[str], cwd: str, cancel: Cancel | None = None, wait_s: int = PRINT_TIMEOUT_S + 30) -> dict:
    """Run agy in print mode with JSON output and return the parsed result object.

    Retries once when agy cannot create its local conversation ("its database was initialized by another opener",
    seen 2026-11-20): that happens before any model call, spends no quota, and a fresh start succeeds.
    """
    try:
        return _run_agy_once(args, cwd, cancel, wait_s)
    except WorkerError as e:
        if START_FAILURE not in str(e):
            raise
        print("agy failed to start a conversation; retrying once", file=sys.stderr)
        return _run_agy_once(args, cwd, cancel, wait_s)


def _run_agy_once(args: list[str], cwd: str, cancel: Cancel | None = None, wait_s: int = PRINT_TIMEOUT_S + 30) -> dict:
    """One agy run in print mode with JSON output; returns the parsed result object."""
    cmd = [_agy(), *args, "--output-format", "json"]
    if len(subprocess.list2cmdline(cmd)) > MAX_CMDLINE_CHARS:
        raise WorkerError(f"prompt too long for the Windows command line (limit about {MAX_CMDLINE_CHARS} "
                          "characters); put the material in files instead")
    env = {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k)}
    with _agy_lock:
        if cancel is not None and cancel.cancelled:
            raise WorkerError("cancelled before agy started")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as e:
            raise WorkerError(f"could not start agy: {e.strerror}")
        if cancel is not None:
            cancel.attach(proc)
        try:
            stdout, stderr = proc.communicate(timeout=wait_s)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            proc.communicate()
            raise WorkerError(f"agy did not finish within {wait_s}s")
    if cancel is not None and cancel.cancelled:
        raise WorkerError("cancelled by the client; agy was stopped")
    result = None
    for line in reversed(stdout.splitlines()):
        if line.startswith("{"):
            try:
                result = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if proc.returncode != 0 or result is None:
        detail = (result or {}).get("error") or stderr.strip() or stdout.strip()
        err = WorkerError(f"agy exited with code {proc.returncode}: {detail[:2000]}")
        err.conversation_id = (result or {}).get("conversation_id")
        raise err
    return result


def _transcript(conversation_id: str | None) -> str | None:
    """agy's own full record of a call: prompt as received, thinking, tool calls and reply."""
    if not conversation_id:
        return None
    return str(Path.home() / ".gemini" / "antigravity-cli" / "brain" / conversation_id
               / ".system_generated" / "logs" / "transcript_full.jsonl")


def _copy_names(files: list[tuple[Path, bytes]]) -> dict[str, tuple[Path, bytes]]:
    """Name each temp copy by its path below the files' common folder, so services/utils.py and tests/utils.py
    stay apart. Files with no common folder short of a drive root keep their bare names."""
    paths = [p for p, _ in files]
    try:
        base = Path(os.path.commonpath([p.parent for p in paths])) if paths else None
    except ValueError:  # different drives
        base = None
    if base is not None and base.parent == base:  # only a drive root in common
        base = None
    names: dict[str, tuple[Path, bytes]] = {}
    for i, (p, data) in enumerate(files):
        name = p.relative_to(base).as_posix() if base else p.name
        names[name if name not in names else f"{i}_{p.name}"] = (p, data)
    return names


def _restore_paths(text: str, work: str, copies: dict[str, Path]) -> str:
    """Point links at the deleted temp copies (file:///C:/.../agy_x/a.py or C:\\...\\agy_x\\a.py) back to the originals.
    Longest names go first, so a.pyc is never rewritten through its prefix a.py."""
    for name in sorted(copies, key=len, reverse=True):
        orig = copies[name]
        for work_prefix, sep, target in ((work.replace("\\", "/"), "/", orig.as_posix()), (work, "\\", str(orig))):
            pattern = re.escape(work_prefix + sep + name.replace("/", sep))
            text = re.sub(pattern, lambda _m, t=target: t, text, flags=re.IGNORECASE)
    return text


def _install_guard(work: Path, roots: list[Path]) -> Path:
    """Put the read-only guard hook into the temp workspace (workspace-local hooks apply only to this run)."""
    agents = work / ".agents"
    agents.mkdir()
    cfg = agents / "guard.json"
    cfg.write_text(json.dumps({"roots": [str(r) for r in roots], "guard_dir": str(agents)}), encoding="utf-8")
    command = subprocess.list2cmdline([sys.executable, str(GUARD_SCRIPT), str(cfg)])
    hooks = {"workers-read-only-guard": {"PreToolUse": [
        {"matcher": "*", "hooks": [{"type": "command", "command": command, "timeout": 30}]}]}}
    (agents / "hooks.json").write_text(json.dumps(hooks), encoding="utf-8")
    return agents / "guard_log.jsonl"


def _guard_log(path: Path) -> list[dict]:
    """What agy tried to do and what the guard decided, for the call log."""
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _repo_index(repo: Path) -> list[str]:
    """`path<TAB>bytes` lines for FILES.txt: tracked files of a git repository (else a walk), minus secret files
    and dependency folders. The guard still checks every file agy actually reads."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, check=True,
                             stdin=subprocess.DEVNULL).stdout.decode("utf-8", errors="replace")
        rels = [r for r in out.split("\0") if r]
    except (OSError, subprocess.CalledProcessError):
        rels = []
        for dirpath, dirnames, filenames in os.walk(repo):
            dirnames[:] = [d for d in dirnames if d not in INDEX_SKIP_DIRS]
            rels += [str(Path(dirpath, f).relative_to(repo)) for f in filenames]
    lines = []
    for rel in sorted(rels):
        p = (repo / rel).resolve()
        if any(part in INDEX_SKIP_DIRS for part in Path(rel).parts) or not p.is_file() or is_denied_path(p):
            continue
        lines.append(f"{Path(rel).as_posix()}\t{p.stat().st_size}")
        if len(lines) >= MAX_INDEX_FILES:
            lines.append(f"(list cut at {MAX_INDEX_FILES} files)")
            break
    return lines


def ask_gemini(prompt: str, files=(), model: str | None = None, cancel: Cancel | None = None,
               timeout_s: int = PRINT_TIMEOUT_S, repo: str | None = None) -> dict:
    """Run one prompt; return {"text", "model", "elapsed_s", "usage", "call_id"}. Raises WorkerError on any failure.
    `cancel` lets another thread stop the call (the MCP server uses it when the client cancels). `timeout_s` is
    agy's own limit; the command line keeps PRINT_TIMEOUT_S, the MCP server passes a longer one. `repo` mounts a
    repository read-only for agy to explore (see the module docstring)."""
    model = model or DEFAULT_MODEL
    if model.startswith("-") or "orchestrator" in model:
        raise WorkerError(f"model {model!r} is not allowed; pick one from the tier table")
    check_text(prompt, "prompt")
    checked = check_files(files)
    repo_path = None
    if repo:
        repo_path = Path(repo).expanduser().resolve()
        if not repo_path.is_dir():
            raise WorkerError(f"not a directory: {repo_path}")
        if is_denied_path(repo_path / "x"):
            raise WorkerError(f"refused: {repo_path} is a secret location and is never mounted")
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="agy_", ignore_cleanup_errors=True) as work:
        copies: dict[str, Path] = {}
        for name, (p, data) in _copy_names(checked).items():
            dest = Path(work) / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            copies[name] = p
        guard_log = _install_guard(Path(work), [Path(work)] + ([repo_path] if repo_path else []))
        full = prompt
        if copies:
            full = READ_ONLY_NOTE + "Files: " + ", ".join(copies) + "\n\n" + full
        extra = []
        if repo_path:
            (Path(work) / "FILES.txt").write_text("\n".join(_repo_index(repo_path)) + "\n", encoding="utf-8")
            full = REPO_NOTE.format(repo=repo_path) + "\n" + full
            extra = ["--add-dir", str(repo_path)]
        record = {"worker": "gemini", "model": model, "prompt": full, "files": file_records(checked),
                  "repo": str(repo_path) if repo_path else None}
        try:
            result = _run_agy(
                [f"--print={full}", "--model", model, "--mode", "plan", "--disable-slash-commands", *extra,
                 "--print-timeout", f"{timeout_s}s"],
                cwd=work,
                cancel=cancel,
                wait_s=timeout_s + 30,
            )
            if result.get("status") != "SUCCESS" or result.get("finished_reason"):
                reason = result.get("finished_reason") or result.get("status")
                err = WorkerError(f"agy run did not complete ({reason}): {result.get('error', '')}".rstrip(": "))
                err.conversation_id = result.get("conversation_id")
                raise err
        except WorkerError as e:
            cid = getattr(e, "conversation_id", None)
            log_call({**record, "elapsed_s": round(time.monotonic() - start, 1), "error": str(e),
                      "tool_calls": _guard_log(guard_log), "conversation_id": cid, "agy_transcript": _transcript(cid)})
            raise
        tool_calls = _guard_log(guard_log)
    cid = result.get("conversation_id")
    out = {
        "text": _restore_paths(result.get("response", ""), work, copies),
        "model": model,
        "elapsed_s": round(time.monotonic() - start, 1),
        "usage": result.get("usage", {}),
        "call_id": cid,
    }
    log_call({**record, "elapsed_s": out["elapsed_s"], "usage": out["usage"], "response": out["text"],
              "tool_calls": tool_calls, "conversation_id": cid, "agy_transcript": _transcript(cid)})
    out["denied"] = [c for c in tool_calls if c.get("decision") != "allow"]
    return out


def gemini_quota() -> list[dict]:
    """Remaining subscription quota per bucket; this query itself spends no quota."""
    with tempfile.TemporaryDirectory(prefix="agy_", ignore_cleanup_errors=True) as work:
        result = _run_agy(["--print=/quota"], cwd=work)
    rows = []
    for group in result.get("command", {}).get("data", {}).get("groups", []):
        for b in group.get("buckets", []):
            rows.append({
                "group": group.get("name"),
                "bucket": b.get("name"),
                "remaining_pct": round(100 * b.get("remaining_fraction", 0)),
                "reset": b.get("reset_time"),
            })
    if not rows:
        raise WorkerError("agy /quota returned no quota data")
    return rows


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Ask Gemini via the Antigravity CLI (subscription quota only).")
    ap.add_argument("--prompt")
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--repo", help="mount this repository read-only and let agy pick the files to read")
    ap.add_argument("--quota", action="store_true", help="print remaining quota and exit")
    a = ap.parse_args()
    try:
        if a.quota:
            for r in gemini_quota():
                print(f"{r['group']} | {r['bucket']}: {r['remaining_pct']}% (resets {r['reset']})")
            return 0
        if not a.prompt:
            ap.error("--prompt is required unless --quota is given")
        out = ask_gemini(a.prompt, a.file, a.model, repo=a.repo)
    except WorkerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(out["text"])
    for d in out["denied"]:
        print(f"guard denied {d.get('tool')} {d.get('path') or ''}: {d.get('reason')}", file=sys.stderr)
    u = out["usage"]
    print(
        f"[{out['model']}] {out['elapsed_s']}s  in={u.get('input_tokens')} out={u.get('output_tokens')} "
        f"thinking={u.get('thinking_tokens')} cache_read={u.get('cache_read_tokens')} id={out['call_id']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
