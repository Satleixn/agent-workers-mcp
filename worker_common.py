"""Shared input checks for the worker scripts (ask_gemini.py, ask_deepseek.py).

Everything a worker sends to an outside model passes through check_files(),
so the secret-file denylist lives here in code, not only in CLAUDE.md.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

# About 200k tokens of code, a fifth of either model's ~1M-token context (user decision 2026-11-20; was 200 KB).
MAX_TOTAL_BYTES = 700_000
# One JSON line per call that actually reached a model (success or failure). Refused calls are not logged,
# since a refused prompt may contain the very secret it was refused for.
LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_KEEP_MONTHS = 3  # month files kept, the current one included; older ones are deleted when a call is logged
_log_lock = threading.Lock()

# Matched case-insensitively against the file name.
DENY_NAMES = (
    "secrets.toml", ".env", ".env.*", "*.env", ".envrc", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks",
    "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*", "credentials.json", "service_account*.json",
    ".npmrc", ".pypirc", ".netrc", "_netrc", ".git-credentials", ".claude.json",
)
# Matched case-insensitively against every directory name in the resolved path.
DENY_DIRS = (".streamlit", ".ssh", ".aws", ".kube", ".gnupg", ".azure", ".gcloud")
# agy's home for our own runs (ask_gemini passes it with --gemini_dir), apart from the user's ~/.gemini.
AGY_HOME = Path.home() / ".agent-workers" / "agy-home"
# Whole trees holding raw worker traffic or agent state: agy's data (the user's and ours) and our own call logs.
DENY_TREES = ((Path.home() / ".gemini").resolve(), AGY_HOME.resolve(), LOG_DIR)

# A variable that holds a secret. A name ending in _path/_file/_dir/_url/_name holds where a secret lives,
# not the secret (secrets_path = ".streamlit/secrets.toml"); the key-format patterns still check its value.
_SECRET_NAME = (r"[\w\-]*(?:api[_\-]?key|secret|token|password)[\w\-]*"
                r"(?<!_path)(?<!_file)(?<!_dir)(?<!_url)(?<!_name)")

# Strings that look like a real credential, wherever they appear in a prompt or file.
SECRET_PATTERNS = (
    ("OpenAI/DeepSeek-style key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack/Stripe/Hugging Face/npm token", re.compile(
        r"\b(?:xox[abposr]-[A-Za-z0-9\-]{10,}|[rs]k_live_[A-Za-z0-9]{16,}|hf_[A-Za-z0-9]{30,}|npm_[A-Za-z0-9]{36})")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("bearer token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-.=]{20,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    # user:password@host; a $VAR or ${{ }} reference in the password slot is not a literal secret.
    ("connection string with password", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^/\s:@\"']+:(?!\$)[^/\s@\"']+@[\w.\-]+")),
    ("hard-coded secret assignment", re.compile(
        r"(?i)\b" + _SECRET_NAME + r"\s*[:=]\s*[\"'][^\"'\s]{16,}[\"']")),
    # .env / YAML / INI style: the rest of the line is one bare value. Code such as get_key(), args.key or
    # $VAR contains ( . [ { $ and is not matched (0 false positives on an 80-file Python project, 2026-11-19).
    ("unquoted secret assignment", re.compile(
        r"(?im)^\s*(?:export\s+)?" + _SECRET_NAME + r"\s*[:=]\s*(?![\"'])[^\s\"'#()\[\]{}$.,]{16,}\s*$")),
)


class WorkerError(Exception):
    """A worker call failed; the message is safe to show (never contains keys)."""


def check_text(text: str, where: str) -> None:
    """Refuse text that contains something shaped like a credential. The message names only the kind and line."""
    for kind, pattern in SECRET_PATTERNS:
        m = pattern.search(text)
        if m:
            line = text.count("\n", 0, m.start()) + 1
            raise WorkerError(f"refused: {where} line {line} contains what looks like a {kind}; remove it before sending")


def is_denied_path(p: Path) -> bool:
    """True when a resolved path is a secret file by name, sits in a secret directory, or is inside a denied tree."""
    name = p.name.lower()
    return (any(fnmatch.fnmatch(name, pat) for pat in DENY_NAMES)
            or any(part.lower() in DENY_DIRS for part in p.parent.parts)
            or any(p.is_relative_to(tree) for tree in DENY_TREES))


def check_files(paths) -> list[tuple[Path, bytes]]:
    """Resolve the paths and refuse missing, secret or oversized input before anything is sent.

    Sizes are checked before any file is read. Returns each path with the exact bytes that were scanned,
    so the caller sends and logs that same content instead of reading the file again.
    """
    resolved = []
    total = 0
    for raw in paths:
        p = Path(raw).expanduser().resolve()
        if is_denied_path(p):
            raise WorkerError(f"refused: {p} looks like a secret file and is never sent to a worker")
        if not p.is_file():
            raise WorkerError(f"not a file: {p}")
        total += p.stat().st_size
        resolved.append(p)
    if total > MAX_TOTAL_BYTES:
        raise WorkerError(f"files total {total} bytes, over the {MAX_TOTAL_BYTES}-byte limit; send fewer files")
    files = []
    for p in resolved:
        try:
            data = p.read_bytes()
        except OSError as e:
            raise WorkerError(f"cannot read {p}: {e.strerror}")
        check_text(data.decode("utf-8", errors="replace"), str(p))
        files.append((p, data))
    return files


def file_records(files) -> list[dict]:
    """Path, size and sha256 of each file sent, so a log entry pins the exact version without copying it."""
    return [{"path": str(p), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for p, data in files]


def _prune_logs(now: datetime) -> None:
    """Delete month files older than the newest LOG_KEEP_MONTHS months."""
    oldest_kept = now.year * 12 + now.month - LOG_KEEP_MONTHS + 1
    for f in LOG_DIR.glob("*.jsonl"):
        try:
            year, month = (int(x) for x in f.stem.split("-"))
        except ValueError:
            continue
        if year * 12 + month < oldest_kept:
            f.unlink(missing_ok=True)


def log_call(record: dict) -> None:
    """Append one call to logs/YYYY-MM.jsonl. A logging failure is reported on stderr and never fails the call."""
    now = datetime.now().astimezone()
    line = json.dumps({"time": now.isoformat(timespec="seconds"), **record}, ensure_ascii=False)
    try:
        with _log_lock:
            LOG_DIR.mkdir(exist_ok=True)
            _prune_logs(now)
            with open(LOG_DIR / f"{now:%Y-%m}.jsonl", "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except OSError as e:
        print(f"warning: could not write worker log: {e}", file=sys.stderr)
