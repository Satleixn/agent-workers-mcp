"""Register the "workers" MCP server in ~/.claude.json and install the two subagents into ~/.claude/agents.

Run it with any Python, while no Claude app or Claude Code session is open: they rewrite ~/.claude.json and could
overwrite this change. It makes a timestamped backup first, writes atomically, and stores no API key (the server
inherits DEEPSEEK_API_KEY from the user environment). Existing subagent files that differ are backed up before they
are replaced.

Prerequisite: a virtual environment with requirements.txt installed. By default it is .venv in this folder; pass
--python <path to that venv's python.exe> to use one elsewhere (needed when this folder carries a low-integrity
label, e.g. from another tool's sandbox: programs started from such a venv cannot read %LOCALAPPDATA%, where agy
lives).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = Path.home() / ".claude.json"
AGENTS_DIR = Path.home() / ".claude" / "agents"


def claude_running() -> bool:
    out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True).stdout.lower()
    return any(line.startswith('"claude') for line in out.splitlines())


def install_agents(stamp: str) -> None:
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    for src in sorted((ROOT / "agents").glob("*.md")):
        dest = AGENTS_DIR / src.name
        if dest.is_file() and dest.read_bytes() == src.read_bytes():
            print(f"subagent {src.stem}: up to date")
            continue
        if dest.is_file():
            shutil.copy2(dest, dest.with_name(f"{dest.name}.bak-{stamp}"))
        shutil.copy2(src, dest)
        print(f"subagent {src.stem}: installed")


def register_server(stamp: str, python: Path) -> None:
    entry = {"type": "stdio", "command": str(python), "args": [str(ROOT / "workers_mcp.py")], "env": {}}
    data = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.is_file() else {}
    servers = data.setdefault("mcpServers", {})
    if servers.get("workers") == entry:
        print("MCP server workers: already registered")
        return
    if CONFIG.is_file():
        shutil.copy2(CONFIG, CONFIG.with_name(f".claude.json.bak-{stamp}"))
    servers["workers"] = entry
    tmp = CONFIG.with_name(".claude.json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, CONFIG)
    print("MCP server workers: registered")
    print(json.dumps({"workers": entry}, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser(description="Register the workers MCP server and install the subagents.")
    ap.add_argument("--python", default=str(ROOT / ".venv" / "Scripts" / "python.exe"),
                    help="python.exe of the virtual environment that has requirements.txt installed")
    python = Path(ap.parse_args().python).resolve()
    if not python.is_file():
        print(f"No Python at {python}. Create the virtual environment first (see README).")
        return 1
    if claude_running():
        print("Claude is still running. Quit the Claude app (and any claude sessions) first, then rerun.")
        return 1
    stamp = time.strftime("%Y%m%d-%H%M%S")
    install_agents(stamp)
    register_server(stamp, python)
    print("Done. Start Claude again to load the server and the subagents.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
