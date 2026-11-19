"""Ask DeepSeek (pay per token) for cheap bulk drafting, rewriting and translation.

The API key is read only from the DEEPSEEK_API_KEY environment variable and is never printed.

--effort controls thinking mode; thinking tokens are billed as output:
  none  thinking off (default): translate, rewrite, format
  low   light thinking: drafting docs and comments
  high / max  deeper reasoning (rarely needed for this worker's role)

Usage:
  python ask_deepseek.py --prompt "task" [--file a.py --file b.md] [--model deepseek-flash]
                         [--effort none|low|high|max] [--system "..."] [--json]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone

import requests

from worker_common import WorkerError, check_files, check_text, file_records, log_call

# "deepseek-v4-flash" is a retired alias that the API routes to this model.
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_EFFORT = "none"
EFFORTS = ("none", "low", "high", "max")
API_URL = "https://api.deepseek.com/chat/completions"
TIMEOUT_S = 180
RETRY_WAIT_S = 30
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
# Weekday hours (UTC) billed at double price: 01:00-04:00 and 06:00-10:00.
PEAK_HOURS_UTC = frozenset({1, 2, 3, 6, 7, 8, 9})


def is_peak(now: datetime | None = None) -> bool:
    """True when DeepSeek charges its doubled peak-hour price."""
    now = now or datetime.now(timezone.utc)
    return now.weekday() < 5 and now.hour in PEAK_HOURS_UTC


def _post(payload: dict, key: str) -> requests.Response:
    """POST once, retrying a single time after RETRY_WAIT_S on 429, 5xx or a connection failure."""
    for attempt in (1, 2):
        try:
            resp = requests.post(
                API_URL,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
                timeout=TIMEOUT_S,
            )
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt == 2:
                raise WorkerError(f"DeepSeek unreachable after retry: {type(e).__name__}")
        except requests.RequestException as e:
            raise WorkerError(f"DeepSeek request failed: {type(e).__name__}")
        else:
            if resp.status_code not in RETRYABLE_STATUS or attempt == 2:
                return resp
        print(f"DeepSeek call failed, retrying once in {RETRY_WAIT_S}s", file=sys.stderr)
        time.sleep(RETRY_WAIT_S)


def ask_deepseek(prompt: str, files=(), model: str | None = None, system: str | None = None,
                 json_mode: bool = False, effort: str | None = None) -> dict:
    """Run one prompt; return {"text", "model", "elapsed_s", "usage", "call_id", "peak"}. Raises WorkerError on any
    failure."""
    model = model or DEFAULT_MODEL
    effort = effort or DEFAULT_EFFORT
    if effort not in EFFORTS:
        raise WorkerError(f"effort must be one of {', '.join(EFFORTS)}")
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise WorkerError("DEEPSEEK_API_KEY is not set (set it as a user environment variable)")
    check_text(prompt, "prompt")
    if system:
        check_text(system, "system prompt")
    checked = check_files(files)
    parts = [f"=== file: {p} ===\n{data.decode('utf-8', errors='replace')}" for p, data in checked]
    content = "\n\n".join([*parts, prompt]) if parts else prompt
    messages = [{"role": "system", "content": system}] if system else []
    messages.append({"role": "user", "content": content})
    payload = {"model": model, "messages": messages, "stream": False}
    if effort == "none":
        payload["thinking"] = {"type": "disabled"}
    else:
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = effort
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    # The user message sent is each file as "=== file: <path> ===" + its text, then the prompt; files are
    # logged by fingerprint rather than copied into the log.
    record = {"worker": "deepseek", "model": model, "effort": effort, "json_mode": json_mode,
              "system": system, "prompt": prompt, "files": file_records(checked), "peak": is_peak()}
    start = time.monotonic()
    try:
        resp = _post(payload, key)
        if resp.status_code != 200:
            raise WorkerError(f"DeepSeek HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            data = resp.json()
            message = data["choices"][0]["message"]
            text = message["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise WorkerError("malformed DeepSeek response")
    except WorkerError as e:
        log_call({**record, "elapsed_s": round(time.monotonic() - start, 1), "error": str(e)})
        raise
    out = {
        "text": text,
        "model": data.get("model", model),
        "elapsed_s": round(time.monotonic() - start, 1),
        "usage": data.get("usage", {}),
        "call_id": data.get("id"),
        "peak": record["peak"],
    }
    log_call({**record, "model": out["model"], "elapsed_s": out["elapsed_s"], "usage": out["usage"],
              "response_id": out["call_id"], "reasoning": message.get("reasoning_content"), "response": text})
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Ask DeepSeek (OpenAI-compatible chat API).")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--effort", choices=EFFORTS, default=DEFAULT_EFFORT, help="thinking level; none turns thinking off")
    ap.add_argument("--system")
    ap.add_argument("--json", action="store_true", help="ask the model for a JSON object (response_format)")
    a = ap.parse_args()
    try:
        out = ask_deepseek(a.prompt, a.file, a.model, a.system, a.json, a.effort)
    except WorkerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(out["text"])
    u = out["usage"]
    reasoning = (u.get("completion_tokens_details") or {}).get("reasoning_tokens")
    print(
        f"[{out['model']}] effort={a.effort} {out['elapsed_s']}s  in={u.get('prompt_tokens')} "
        f"out={u.get('completion_tokens')} reasoning={reasoning} cache_hit={u.get('prompt_cache_hit_tokens')} "
        f"id={out['call_id']}{' PEAK-2x-price' if out['peak'] else ''}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
