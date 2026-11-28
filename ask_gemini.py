"""Ask Gemini through the Antigravity CLI (agy), on the Google AI Pro subscription quota.

Never falls back to the paid Gemini API. agy is a full agent (it can run commands and write files), so every call
is a read-only review: agy runs in a fresh temp directory in plan mode, and agy_guard.py (a PreToolUse hook placed
in that directory) allows only read_file on files inside the workspace that pass the secret checks, and denies every
other tool. Two ways to give it material:
  files  copies of the given files go into the temp directory (total capped by MAX_TOTAL_BYTES);
  repo   a repository is mounted read-only with --add-dir and agy picks what to read from FILES.txt, a list of the
         repository's files; nothing is written into the repository.
         With `base` (a git ref), the call reviews a change instead: the diff from the merge base of `base` and HEAD
         to the working tree goes into the workspace as DIFF.patch, and CHANGES.txt lists the changed files.

Isolation: agy runs with its own home (AGY_HOME, passed with --gemini_dir), so the user's agy plugins, skills and
GEMINI.md never reach a review; that home's GEMINI.md is reviewer_rules.md. The shared skills (SKILLS_DIR, minus
lead-only "lead-*" ones) are copied into the workspace's .agents/skills for Gemini to choose from; `skills` names the
ones the caller suggests. `structured` asks for the result as JSON matching review_schema.json; `proposal` asks
Gemini for its own independent plan instead, as JSON matching proposal_schema.json.

Model choice (see docs/manual.md):
  gemini-4-hydrogen-low     fast: classify, extract, short summaries
  gemini-4-hydrogen-medium  everyday: summarise 10+ files, doc Q&A, explain code
  gemini-4-neon-medium      second opinion on one function or module (default)
  gemini-4-neon-high        multi-file review, architecture review, hard bug hypotheses
  gemini-4-neon-xhigh       maths/statistics correctness, hardest reasoning (tell the user first)
  gemini-4-argon-high       stand-in for neon-high/xhigh when the Gemini pool runs low (separate quota)
Each call costs ~11k input tokens of agent overhead (~24k before agy got its own home), so batch small questions.

Usage:
  python ask_gemini.py --prompt "task" [--file a.py --file b.md] [--repo D:\\path\\to\\repo [--base main]]
                       [--model ...] [--skill name ...] [--structured] [--proposal]   (--proposal implies --structured)
                       [--timeout 3600]   (long runs: start it in the background; the default suits the foreground)
  python ask_gemini.py --quota
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
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

from worker_common import (AGY_HOME, DENY_DIRS, DENY_NAMES, MAX_TOTAL_BYTES, WorkerError, check_files, check_text,
                           file_records, is_denied_path, log_call)

DEFAULT_MODEL = "gemini-4-neon-medium"
# Time limit for one call. Repository reviews on neon-high/xhigh have taken up to ~15 minutes, and agy can lose
# several more to a dropped connection before it retries (2026-11-21), so the MCP server allows an hour.
PRINT_TIMEOUT_S = 3600
# The command line defaults to less: 540 + 30 s ends under a shell tool's 10-minute foreground cap, so the script
# still stops agy itself if it is run in the foreground. Run it in the background with --timeout 3600 for long reviews.
CLI_TIMEOUT_S = 540
# The quota query needs no model call; a short limit keeps a stalled one from holding up the call behind it.
QUOTA_TIMEOUT_S = 60
# Our child processes get a hidden console instead of a new visible one when the parent has none.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
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
# Becomes AGY_HOME/GEMINI.md, the only rules agy loads for our runs. agy's CLI ignores GEMINI_HOME; --gemini_dir
# (undocumented, agy 1.5.14) moves its whole home, and the login stays in the OS keyring (tested 2026-11-23).
REVIEWER_RULES = Path(__file__).resolve().with_name("reviewer_rules.md")
REVIEW_SCHEMA = Path(__file__).resolve().with_name("review_schema.json")
PROPOSAL_SCHEMA = Path(__file__).resolve().with_name("proposal_schema.json")
# Single source of the skills shared by the lead (Claude) and Gemini. Lead-only skills (orchestration that calls
# tools only the lead has) start with LEAD_ONLY_PREFIX and are never copied.
SKILLS_DIR = Path.home() / ".claude" / "skills"
LEAD_ONLY_PREFIX = "lead-"
SKILLS_MAX_BYTES = 5_000_000  # all shared skills together, copied into every call's workspace
SKILLS_NOTE = (
    "Shared skills are in your workspace under .agents/skills/ (one folder per skill, each with a SKILL.md). "
    "{suggested}Decide for yourself which skills apply: read a skill's SKILL.md before following it; you may use "
    "skills nobody suggested and reject suggestions. {report}\n"
)
SKILLS_REPORT_TEXT = ("End your answer with a short 'Skills' section: skills you read, suggestions you rejected "
                      "and why, skills you would add.")
SKILLS_REPORT_JSON = "Report them in skills_used, rejected_suggestions and suggested_skills."
STRUCTURED_NOTE = "Answer only with JSON that matches the required schema; give each finding its location and evidence.\n"
PROPOSAL_NOTE = (
    "This is an independent proposal: draft your own plan for the task from the brief and the material, without "
    "guessing at any approach the lead may have in mind. Answer only with JSON that matches the required schema; for "
    "each decision give the alternatives you weighed and the evidence it rests on.\n"
)
DIFF_NOTE = (
    "This is a review of a change. {diff} CHANGES.txt in your working directory lists the {n} changed files, one per "
    "line as status, tab, path relative to the repository (D = deleted: do not read it; ? = new and untracked: not in "
    "the diff, read it in full). {excluded}Review the change: read the changed files and the code they call or that "
    "calls them; report problems in unchanged code only where the change exposes or relies on them. Cite repository "
    "paths with line numbers in the new version.\n"
)
DIFF_PATCH_TEXT = ("DIFF.patch in your working directory is `git diff` from {base} (merge base {sha}) to the "
                   "repository's working tree, committed and uncommitted changes together.")
NO_PATCH_TEXT = "The change from {base} (merge base {sha}) is only new untracked files, so there is no DIFF.patch."
MAX_INDEX_FILES = 5000
# Directories never listed in FILES.txt (dependencies and build output, not the code under review).
INDEX_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", "dist",
                   "build"}
GIT_TIMEOUT_S = 120
# Flags for every diff of a `base` review. No program from the repository's config runs (external diff, textconv);
# a rename shows as delete + add, so the side under a secret old name stays out (the new name is checked like any
# other file, by name and content, as in plain repo mode) and every --name-status entry has two fields;
# a submodule shows only as a commit change, whatever diff.submodule says; and a `repo` that is a subdirectory of a
# larger repository gets only its own changes, with paths relative to it (the guard's root).
DIFF_FLAGS = ("--no-ext-diff", "--no-textconv", "--no-color", "--no-renames", "--submodule=short", "--relative")
# Windows caps a whole command line at 32,767 characters, and the prompt travels on it (--print=...).
MAX_CMDLINE_CHARS = 32_000
# Environment variables agy never needs. Dropping them keeps our DeepSeek key away from it, and keeps a
# future GEMINI_API_KEY / GOOGLE_API_KEY from steering it to the paid API instead of the subscription.
_SECRET_ENV = re.compile(r"(?i)api_?key|token|secret|password|credential|^GOOGLE_(?:GENAI|CLOUD)_")
# Per-run settings (never saved): keep agy on the subscription even if the user's own agy settings say otherwise.
# useG1Credits would spend paid AI credits once the plan quota runs out; modelProvider "gemini" would call the
# paid Gemini API with GEMINI_API_KEY.
AGY_SETTINGS = json.dumps({"useG1Credits": False, "modelProvider": ""})
# agy's error when it cannot create its local conversation record; it fails before any model call.
START_FAILURE = "failed to start conversation"
# agy keeps shared per-user state (conversation index, summaries database), so this process runs one at a time.
_agy_lock = threading.Lock()
# Quota queries skip _agy_lock, so updating agy's home has a lock of its own.
_home_lock = threading.Lock()
# Quota floors, checked before every call against the model's own pool (a quota query spends no quota).
QUOTA_STOP_PCT = 10        # any live bucket of the pool below this: refuse, Claude does the work itself
QUOTA_HIGH_TIER_PCT = 25   # Gemini five-hour bucket below this: refuse the costliest models
HIGH_TIER_MODELS = {"gemini-4-neon-high", "gemini-4-neon-xhigh"}
QUOTA_CACHE_S = 60
_quota_cache: tuple[float, list[dict]] | None = None
_quota_lock = threading.Lock()


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
                       stdin=subprocess.DEVNULL, capture_output=True, creationflags=_NO_WINDOW)
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


def _run_agy(args: list[str], cwd: str, cancel: Cancel | None = None, wait_s: int = PRINT_TIMEOUT_S + 30,
             lock: bool = True) -> dict:
    """Run agy in print mode with JSON output and return the parsed result object.

    Retries once when agy cannot create its local conversation ("its database was initialized by another opener",
    seen 2026-11-20): that happens before any model call, spends no quota, and a fresh start succeeds.
    """
    try:
        return _run_agy_once(args, cwd, cancel, wait_s, lock)
    except WorkerError as e:
        if START_FAILURE not in str(e):
            raise
        print("agy failed to start a conversation; retrying once", file=sys.stderr)
        return _run_agy_once(args, cwd, cancel, wait_s, lock)


def _run_agy_once(args: list[str], cwd: str, cancel: Cancel | None = None, wait_s: int = PRINT_TIMEOUT_S + 30,
                  lock: bool = True) -> dict:
    """One agy run in print mode with JSON output; returns the parsed result object. `lock=False` skips the
    one-at-a-time lock, for runs that start no conversation (the quota query)."""
    _prepare_home()
    cmd = [_agy(), f"--gemini_dir={AGY_HOME}", *args, "--settings", AGY_SETTINGS, "--output-format", "json"]
    if len(subprocess.list2cmdline(cmd)) > MAX_CMDLINE_CHARS:
        raise WorkerError(f"prompt too long for the Windows command line (limit about {MAX_CMDLINE_CHARS} "
                          "characters); put the material in files instead")
    env = {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k)}
    with _agy_lock if lock else contextlib.nullcontext():
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
                creationflags=_NO_WINDOW,
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


def _prepare_home() -> None:
    """Create our agy home and keep its GEMINI.md equal to reviewer_rules.md. It holds no plugins or skills. The file
    is replaced in one step, so an agy starting meanwhile never reads half of it."""
    try:
        rules = REVIEWER_RULES.read_text(encoding="utf-8")
        with _home_lock:
            AGY_HOME.mkdir(parents=True, exist_ok=True)
            target = AGY_HOME / "GEMINI.md"
            if target.is_file() and target.read_text(encoding="utf-8") == rules:
                return
            tmp = AGY_HOME / f"GEMINI.md.{os.getpid()}.tmp"
            tmp.write_text(rules, encoding="utf-8")
            os.replace(tmp, target)
    except OSError as e:
        raise WorkerError(f"could not prepare agy's home {AGY_HOME}: {e.strerror or e}")


def _isolated(conversation_id: str | None) -> bool:
    """True when agy kept the conversation in our home, i.e. --gemini_dir still works. If a future agy ignored the
    flag, the run would load the user's own plugins and GEMINI.md, so the result says so instead of failing silently."""
    return bool(conversation_id) and (AGY_HOME / "antigravity-cli" / "conversations" / f"{conversation_id}.db").is_file()


def _transcript(conversation_id: str | None) -> str | None:
    """agy's own full record of a call: prompt as received, thinking, tool calls and reply."""
    if not conversation_id:
        return None
    home = AGY_HOME if _isolated(conversation_id) else Path.home() / ".gemini"
    return str(home / "antigravity-cli" / "brain" / conversation_id
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


def _install_guard(work: Path, roots: list[Path], structured: bool) -> Path:
    """Put the read-only guard hook into the temp workspace (workspace-local hooks apply only to this run). Its own
    files sit in .agents/guard and .agents/hooks.json, which agy may not read; .agents/skills stays readable.
    `finish` (how agy hands back a --json-schema result) is allowed only for structured calls."""
    agents = work / ".agents"
    guard_dir = agents / "guard"
    guard_dir.mkdir(parents=True)
    cfg = guard_dir / "guard.json"
    cfg.write_text(json.dumps({"roots": [str(r) for r in roots],
                               "deny": [str(guard_dir), str(agents / "hooks.json")],
                               "results": ["finish"] if structured else []}), encoding="utf-8")
    command = subprocess.list2cmdline([sys.executable, str(GUARD_SCRIPT), str(cfg)])
    hooks = {"workers-read-only-guard": {"PreToolUse": [
        {"matcher": "*", "hooks": [{"type": "command", "command": command, "timeout": 30}]}]}}
    (agents / "hooks.json").write_text(json.dumps(hooks), encoding="utf-8")
    return guard_dir / "guard_log.jsonl"


def _is_link(p: Path) -> bool:
    return p.is_symlink() or getattr(p, "is_junction", lambda: False)()


def _shared_skills() -> list[str]:
    """Names of the skills in SKILLS_DIR that Gemini may use: every folder with a SKILL.md, minus lead-only ones
    (matched case-insensitively, on the folder's own name and on the folder a link points to)."""
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(p.name for p in SKILLS_DIR.iterdir()
                  if (p / "SKILL.md").is_file()
                  and not any(n.lower().startswith(LEAD_ONLY_PREFIX) for n in (p.name, p.resolve().name)))


def _install_skills(work: Path, names: list[str]) -> list[str]:
    """Copy the shared skills into the workspace's .agents/skills, where agy discovers them; return the names copied.
    agy reads each SKILL.md's frontmatter itself, not through the guard, so a skill whose SKILL.md fails the secret
    check is skipped. Links inside a skill, secret files and dependency folders are not copied (a copied link would
    lose the name and folder the denylist checks), and the guard still checks every file Gemini reads."""
    total = 0
    installed = []
    for name in names:
        src = SKILLS_DIR / name
        try:
            check_text((src / "SKILL.md").read_text(encoding="utf-8", errors="replace"), f"skill {name}/SKILL.md")
        except (WorkerError, OSError) as e:
            print(f"skipping skill {name}: {getattr(e, 'strerror', None) or e}", file=sys.stderr)
            continue
        try:
            for dirpath, dirnames, filenames in os.walk(src):
                here = Path(dirpath)
                dirnames[:] = [d for d in dirnames if d not in INDEX_SKIP_DIRS and not _is_link(here / d)]
                for f in filenames:
                    p = here / f
                    if _is_link(p) or is_denied_path(p.resolve()):
                        continue
                    total += p.stat().st_size
                    if total > SKILLS_MAX_BYTES:
                        raise WorkerError(f"the shared skills in {SKILLS_DIR} exceed {SKILLS_MAX_BYTES} bytes; move "
                                          "large files out of them")
                    target = work / ".agents" / "skills" / name / p.relative_to(src)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(p, target)
        except OSError as e:
            raise WorkerError(f"could not copy skill {name}: {e.strerror or e}")
        installed.append(name)
    return installed


def _restore_json_paths(value, work: str, copies: dict[str, Path]):
    """_restore_paths on every string inside parsed JSON, where Windows paths are escaped in the raw text."""
    if isinstance(value, str):
        return _restore_paths(value, work, copies)
    if isinstance(value, list):
        return [_restore_json_paths(v, work, copies) for v in value]
    if isinstance(value, dict):
        return {k: _restore_json_paths(v, work, copies) for k, v in value.items()}
    return value


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
                             stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW).stdout.decode("utf-8", errors="replace")
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


def _git(repo: Path, *args: str, fail: str, cap: int | None = None) -> bytes:
    """Run git in `repo` and return its stdout bytes; any failure raises WorkerError("<fail>: <git's message>").
    stdout goes to a temporary file, so with `cap` no more than cap + 1 bytes are ever read into memory."""
    cmd = ["git", "-c", "core.fsmonitor=false", "-c", "core.quotepath=off", "-C", str(repo), *args]
    with tempfile.TemporaryFile() as out:
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.PIPE,
                                    creationflags=_NO_WINDOW)
        except OSError as e:
            raise WorkerError(f"could not run git: {e.strerror or e}")
        try:
            _, err = proc.communicate(timeout=GIT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            proc.communicate()
            raise WorkerError(f"{fail}: git did not finish within {GIT_TIMEOUT_S}s")
        if proc.returncode != 0:
            msg = err.decode("utf-8", errors="replace").strip()
            raise WorkerError(f"{fail}: {msg[:500]}" if msg else fail)
        out.seek(0)
        return out.read() if cap is None else out.read(cap + 1)


def _diff_excludes() -> list[str]:
    """Pathspecs that keep secret files and dependency folders out of a `base` review's listings and diff. They are a
    fixed set built from the denylists, so the command line stays short however many files changed; _diff_material
    still checks every path that comes through with is_denied_path."""
    return ([f":(exclude,glob,icase)**/{name}" for name in DENY_NAMES]
            + [f":(exclude,glob,icase)**/{d}/**" for d in (*DENY_DIRS, *sorted(INDEX_SKIP_DIRS))])


def _skipped(repo: Path, rel: str) -> bool:
    """True for a path a review must not show: a secret file, or one inside a dependency or build folder."""
    return (any(part.lower() in INDEX_SKIP_DIRS for part in Path(rel).parent.parts)  # folders only, like the globs
            or is_denied_path((repo / rel).resolve()))


def _diff_material(repo: Path, base: str, budget: int) -> tuple[bytes, str, list[str], int]:
    """The change from `base` to the working tree of `repo`, for a review: the diff bytes, the merge base's sha,
    `status<TAB>path` lines for CHANGES.txt (new untracked files as `?`), and how many changed files were left out as
    secret or dependency files. Refuses (WorkerError) a bad ref, a diff over `budget` bytes, a diff holding something
    shaped like a secret, and a change with nothing left to review. Writes nothing into the repository."""
    if not base.strip() or base.startswith("-") or any(ord(c) < 32 for c in base):
        raise WorkerError(f"base must be a git ref such as main or origin/main, got {base!r}")
    sha = _git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{base}^{{commit}}",
               fail=f"base {base!r} is not a commit in {repo}").decode().strip()
    merge_base = _git(repo, "merge-base", sha, "HEAD",
                      fail=f"no merge base between {base} and HEAD in {repo} (unrelated history or shallow clone?)"
                      ).decode().strip()
    excludes = _diff_excludes()

    def names(*args: str) -> list[str]:
        out = _git(repo, *args, fail=f"git could not list the change from {base}")
        return [n for n in out.decode("utf-8", errors="replace").split("\0") if n]

    fields = names("diff", *DIFF_FLAGS, "--name-status", "-z", merge_base, "--", ".", *excludes)
    changed = list(zip(fields[0::2], fields[1::2]))  # --no-renames: every entry is status, path
    # A file dropped from the index but still on disk (git rm --cached) is both D and untracked; keep it once, as D.
    untracked = [p for p in names("ls-files", "--others", "--exclude-standard", "-z", "--", ".", *excludes)
                 if p not in {path for _, path in changed}]
    leaked = [p for _, p in changed if _skipped(repo, p)] + [p for p in untracked if _skipped(repo, p)]
    if leaked:  # the glob pathspecs and is_denied_path disagree; refuse rather than send
        raise WorkerError(f"refused: {leaked[0]} is a secret or dependency file that git's exclude patterns let "
                          "through; nothing was sent")
    all_changed = set(names("diff", *DIFF_FLAGS, "--name-only", "-z", merge_base, "--", "."))
    all_untracked = set(names("ls-files", "--others", "--exclude-standard", "-z", "--", "."))
    excluded = len((all_changed | all_untracked) - {p for _, p in changed} - set(untracked))
    if not changed and not untracked:
        raise WorkerError(f"nothing to review between {base} and the working tree of {repo}"
                          + (f" ({excluded} secret or dependency files left out)" if excluded else ""))
    diff = b""
    if changed:
        diff = _git(repo, "diff", *DIFF_FLAGS, merge_base, "--", ".", *excludes,
                    fail=f"git could not diff the change from {base}", cap=max(budget, 0))
        if len(diff) > budget:
            raise WorkerError(f"the diff from {base} is over the {budget}-byte budget left after `files` (limit "
                              f"{MAX_TOTAL_BYTES} bytes in all); use a closer base or split the review")
        text = diff.decode("utf-8", errors="replace")
        check_text(text, "the diff")
        # Again without each line's leading +, - or space: the unquoted-assignment pattern is anchored at the start
        # of a line and misses "+API_KEY=..." otherwise. Line numbers stay the same.
        check_text(re.sub(r"(?m)^[ +\-]", "", text), "the diff")
    lines = [f"{status}\t{path}" for status, path in changed] + [f"?\t{path}" for path in untracked]
    return diff, merge_base, lines, excluded


def _last_json(text: str):
    """The last of the JSON values in `text`. agy prints every result Gemini submits through `finish`: one that
    failed the --json-schema check is followed by the retry that passed (seen 2026-11-23), so the last one is the
    accepted result. Raises json.JSONDecodeError when the text is not a sequence of JSON values or the last one is
    not an object (both schemas want an object)."""
    decoder, pos, last = json.JSONDecoder(), 0, None
    text = text.strip()
    while pos < len(text):
        last, pos = decoder.raw_decode(text, pos)
        while pos < len(text) and text[pos].isspace():
            pos += 1
    if not isinstance(last, dict):
        raise json.JSONDecodeError("no JSON object", text, 0)
    return last


def ask_gemini(prompt: str, files=(), model: str | None = None, cancel: Cancel | None = None,
               timeout_s: int = PRINT_TIMEOUT_S, repo: str | None = None, skills=(), structured: bool = False,
               proposal: bool = False, base: str | None = None) -> dict:
    """Run one prompt; return {"text", "model", "elapsed_s", "usage", "call_id", "denied", "isolated"}, plus "data"
    (the parsed JSON, or None if it did not parse) when `structured` or `proposal`. Raises WorkerError on any failure.
    `cancel` lets another thread stop the call (the MCP server uses it when the client cancels). `timeout_s` is
    agy's own limit (the command line passes --timeout, CLI_TIMEOUT_S by default). `repo` mounts a
    repository read-only for agy to explore; `base` (with `repo`) makes it a review of the change from that git ref;
    `skills` names the shared skills the caller suggests; `proposal` asks for an independent plan and implies
    `structured` (see the module docstring)."""
    structured = structured or proposal
    model = model or DEFAULT_MODEL
    if model.startswith("-") or "orchestrator" in model:
        raise WorkerError(f"model {model!r} is not allowed; pick one from the tier table")
    check_text(prompt, "prompt")
    checked = check_files(files)
    shared = _shared_skills()
    suggested = list(dict.fromkeys(skills))
    unknown = [s for s in suggested if s not in shared]
    if unknown:
        raise WorkerError(f"unknown or lead-only skill(s): {', '.join(unknown)}; "
                          f"shared skills: {', '.join(shared) or 'none'}")
    repo_path = None
    if repo:
        repo_path = Path(repo).expanduser().resolve()
        if not repo_path.is_dir():
            raise WorkerError(f"not a directory: {repo_path}")
        if is_denied_path(repo_path / "x"):
            raise WorkerError(f"refused: {repo_path} is a secret location and is never mounted")
    diff_info = None
    if base is not None:
        if repo_path is None:
            raise WorkerError("base needs repo: pass the repository (or the PR's checkout) whose change to review")
        if proposal:
            raise WorkerError("base reviews a change and cannot be combined with proposal")
        # Before the quota check: a bad ref or an oversized diff is refused without spending anything.
        diff_info = _diff_material(repo_path, base, MAX_TOTAL_BYTES - sum(len(data) for _, data in checked))
    quota_note = _check_quota(model)
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="agy_", ignore_cleanup_errors=True) as work:
        # Copies go under files/, so a reviewed file such as .agents/hooks.json or .agents/skills/x/SKILL.md can
        # neither overwrite the guard nor be loaded by agy as part of this workspace's configuration.
        inputs = Path(work) / "files"
        copies: dict[str, Path] = {}
        for name, (p, data) in _copy_names(checked).items():
            dest = inputs / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            copies[name] = p
        guard_log = _install_guard(Path(work), [Path(work)] + ([repo_path] if repo_path else []), structured)
        full = prompt
        if copies:
            full = READ_ONLY_NOTE + "Files: " + ", ".join(f"files/{n}" for n in copies) + "\n\n" + full
        extra = []
        if repo_path:
            (Path(work) / "FILES.txt").write_text("\n".join(_repo_index(repo_path)) + "\n", encoding="utf-8")
            note = REPO_NOTE.format(repo=repo_path)
            if diff_info:
                diff, merge_base, changes, excluded = diff_info
                if diff:
                    (Path(work) / "DIFF.patch").write_bytes(diff)  # the exact bytes scanned and size-checked
                listed = changes[:MAX_INDEX_FILES] + ([f"(list cut at {MAX_INDEX_FILES} files)"]
                                                      if len(changes) > MAX_INDEX_FILES else [])
                (Path(work) / "CHANGES.txt").write_text("\n".join(listed) + "\n", encoding="utf-8")
                note += DIFF_NOTE.format(
                    diff=(DIFF_PATCH_TEXT if diff else NO_PATCH_TEXT).format(base=base, sha=merge_base),
                    n=len(changes),
                    excluded=f"{excluded} secret or dependency files were left out. " if excluded else "")
            full = note + "\n" + full
            extra = ["--add-dir", str(repo_path)]
        installed = _install_skills(Path(work), shared) if shared else []
        skipped = [s for s in suggested if s not in installed]
        if skipped:
            raise WorkerError(f"suggested skill(s) failed the secret check and were not copied: {', '.join(skipped)}")
        if installed:
            # Give paths: a suggested skill may be manual-only (disable-model-invocation), which agy leaves out of
            # the skill list it shows the model; a suggestion stands in for the user invoking it.
            hint = ("The lead suggests: " + ", ".join(
                f"{s} ({Path(work) / '.agents' / 'skills' / s / 'SKILL.md'})" for s in suggested) + ". ") if suggested else ""
            full = SKILLS_NOTE.format(suggested=hint, report=SKILLS_REPORT_JSON if structured else SKILLS_REPORT_TEXT) + full
        if structured:
            full = (PROPOSAL_NOTE if proposal else STRUCTURED_NOTE) + full
            extra += ["--json-schema", str(PROPOSAL_SCHEMA if proposal else REVIEW_SCHEMA)]
        record = {"worker": "gemini", "model": model, "prompt": full, "files": file_records(checked),
                  "repo": str(repo_path) if repo_path else None, "quota_check": quota_note,
                  "skills_suggested": suggested, "structured": structured, "proposal": proposal}
        if diff_info:
            record.update({"base": base, "merge_base": diff_info[1],
                           "diff": {"bytes": len(diff_info[0]), "sha256": hashlib.sha256(diff_info[0]).hexdigest()},
                           "diff_excluded": diff_info[3]})
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
                      "tool_calls": _guard_log(guard_log), "conversation_id": cid, "agy_transcript": _transcript(cid),
                      "isolated": _isolated(cid)})
            raise
        tool_calls = _guard_log(guard_log)
    cid = result.get("conversation_id")
    text = result.get("response", "")
    data = None
    if structured:
        try:
            data = _restore_json_paths(_last_json(text), str(inputs), copies)
            text = json.dumps(data, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            data = None
    if data is None:
        text = _restore_paths(text, str(inputs), copies)
    out = {
        "text": text,
        "model": model,
        "elapsed_s": round(time.monotonic() - start, 1),
        "usage": result.get("usage", {}),
        "call_id": cid,
        "isolated": _isolated(cid),
    }
    if structured:
        out["data"] = data
    log_call({**record, "elapsed_s": out["elapsed_s"], "usage": out["usage"], "response": out["text"],
              "tool_calls": tool_calls, "conversation_id": cid, "agy_transcript": _transcript(cid),
              "isolated": out["isolated"], **({"json_valid": data is not None} if structured else {})})
    out["denied"] = [c for c in tool_calls if c.get("decision") != "allow"]
    return out


def gemini_quota() -> list[dict]:
    """Remaining subscription quota per bucket; this query itself spends no quota."""
    with tempfile.TemporaryDirectory(prefix="agy_", ignore_cleanup_errors=True) as work:
        # No lock: /quota starts no conversation, so it cannot race agy's conversation database, and a long call
        # in progress must not hold up the quota check of the call queued behind it.
        result = _run_agy(["--print=/quota", "--print-timeout", f"{QUOTA_TIMEOUT_S}s"], cwd=work,
                          wait_s=QUOTA_TIMEOUT_S + 30, lock=False)
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


def _quota_rows() -> list[dict]:
    """gemini_quota(), reused for QUOTA_CACHE_S so back-to-back calls in the MCP server query it once."""
    global _quota_cache
    with _quota_lock:
        if _quota_cache is None or time.monotonic() - _quota_cache[0] >= QUOTA_CACHE_S:
            _quota_cache = (time.monotonic(), gemini_quota())
        return _quota_cache[1]


def _check_quota(model: str) -> str:
    """Refuse a call its quota pool cannot afford; return a note for the call log. Argon has its own pool,
    every other model draws on "Gemini Models". If the quota query itself fails the call goes ahead: the real
    call will then report what is wrong with agy more clearly."""
    try:
        rows = _quota_rows()
    except WorkerError as e:
        print(f"quota check failed, calling anyway: {e}", file=sys.stderr)
        return f"check failed: {e}"
    group = "Gemini 4 Argon" if "argon" in model else "Gemini Models"
    live = [r for r in rows if r["group"] == group]
    for r in live:
        if r["remaining_pct"] < QUOTA_STOP_PCT:
            raise WorkerError(f"Gemini quota low: {group} / {r['bucket']} at {r['remaining_pct']}% (resets "
                              f"{r['reset']}); do the work yourself and tell the user")
    if model in HIGH_TIER_MODELS:
        for r in live:
            if "five hour" in r["bucket"].lower() and r["remaining_pct"] < QUOTA_HIGH_TIER_PCT:
                raise WorkerError(f"{model} needs the Gemini five-hour quota at {QUOTA_HIGH_TIER_PCT}% or more, it is "
                                  f"at {r['remaining_pct']}%; use gemini-4-argon-high (separate quota) or "
                                  "gemini-4-neon-medium instead")
    return ", ".join(f"{r['bucket']} {r['remaining_pct']}%" for r in live) or f"no {group} buckets"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Ask Gemini via the Antigravity CLI (subscription quota only).")
    ap.add_argument("--prompt")
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--repo", help="mount this repository read-only and let agy pick the files to read")
    ap.add_argument("--base", help="with --repo: review the change from this git ref (merge base) to the working tree")
    ap.add_argument("--skill", action="append", default=[], help="suggest a shared skill to Gemini (repeatable)")
    ap.add_argument("--structured", action="store_true", help="ask for the result as JSON (review_schema.json)")
    ap.add_argument("--proposal", action="store_true",
                    help="ask for Gemini's own independent plan as JSON (proposal_schema.json)")
    ap.add_argument("--timeout", type=int, default=CLI_TIMEOUT_S,
                    help=f"seconds before agy is stopped (default {CLI_TIMEOUT_S}, under a shell tool's 10-minute "
                         f"foreground cap; up to {PRINT_TIMEOUT_S} when run in the background)")
    ap.add_argument("--quota", action="store_true", help="print remaining quota and exit")
    a = ap.parse_args()
    try:
        if a.quota:
            for r in gemini_quota():
                print(f"{r['group']} | {r['bucket']}: {r['remaining_pct']}% (resets {r['reset']})")
            return 0
        if not a.prompt:
            ap.error("--prompt is required unless --quota is given")
        out = ask_gemini(a.prompt, a.file, a.model, timeout_s=a.timeout, repo=a.repo, skills=a.skill,
                         structured=a.structured, proposal=a.proposal, base=a.base)
    except WorkerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(out["text"])
    if not out["isolated"]:
        print("warning: agy did not use its own home (--gemini_dir); the user's plugins and GEMINI.md may have "
              "applied", file=sys.stderr)
    if (a.structured or a.proposal) and out["data"] is None:
        print("warning: the structured answer is not valid JSON; the raw text is above", file=sys.stderr)
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
