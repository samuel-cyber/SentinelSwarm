"""
bob_client.py — shared helpers for calling Bob Shell (v2) from Python.

All subagents (Scout, Saboteur, Medic, Scribe) import from here so the
subprocess plumbing lives in exactly one place.

Bob Shell CLI (v2) reference:
  Headless execution:  bob run [options]          (prompt on stdin)
  API key env var:     BOB_API_KEY
  Output format:       --format json  → single JSON object:
                       {"type":"result","last_message":"<text>", ...}
  Workspace:           --workspace <path>
"""

import json
import os
import re
import shutil
import subprocess
import sys


# ---------------------------------------------------------------------------
# Executable discovery
# ---------------------------------------------------------------------------

def find_bob_cmd() -> str:
    """
    Return the full path to the bob executable.

    Tries candidates in order so that the .cmd wrapper (Windows npm shim) is
    preferred — it is the only form Python's subprocess can invoke directly
    in list-form without shell=True.

    Raises RuntimeError if bob is not on PATH at all.
    """
    for candidate in ("bob.cmd", "bob.ps1", "bob"):
        path = shutil.which(candidate)
        if path:
            return path
    raise RuntimeError(
        "Bob Shell ('bob') not found on PATH.\n"
        "Install it with:\n"
        "  powershell -ep Bypass "
        "'irm -Uri https://bob.ibm.com/download/bobshell.ps1 | iex'"
    )


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------

def extract_text(raw: str) -> str:
    """
    Parse Bob's ``--format json`` output and return the assistant's reply.

    Bob emits a single JSON object:
        {"type": "result", "status": "success", "last_message": "<text>", ...}

    We parse that object and return ``last_message``.  Falls back to
    returning *raw* verbatim if parsing fails (unexpected output shape).
    """
    raw = raw.strip()
    if not raw:
        return raw
    try:
        obj = json.loads(raw)
        if "last_message" in obj:
            return obj["last_message"]
        # Older / alternate shape: top-level "result" string
        if "result" in obj:
            return obj["result"]
    except json.JSONDecodeError:
        pass
    # Final fallback: return whatever came back as-is
    return raw


# ---------------------------------------------------------------------------
# Code-block extractor (used by Saboteur, Medic, Scribe)
# ---------------------------------------------------------------------------

def extract_code_block(text: str, lang: str = "python") -> str:
    """
    Pull the content out of the first fenced code block in *text*.

    Looks for  ```<lang>\\n...```  (e.g. ```python) first; if not found,
    falls back to any plain ``` fence.  If no fence at all, returns *text*
    stripped — callers can pass through whatever Bob returned.
    """
    text = text.strip()
    # Try the language-specific fence first, then a plain fence
    for pattern in (rf"```{re.escape(lang)}\n(.*?)```",
                    r"```[a-z]*\n(.*?)```",
                    r"```(.*?)```"):
        m = re.search(pattern, text, re.DOTALL)
        if m:
            return m.group(1).strip()
    return text


# ---------------------------------------------------------------------------
# File reading helper (used by Scout and Saboteur)
# ---------------------------------------------------------------------------

def read_source_files(repo_path: str, filenames: list[str]) -> str:
    """
    Read *filenames* from *repo_path* and return them as a single formatted
    string with clear ``### File: <name>`` markers.  Files that don't exist
    are silently skipped.
    """
    parts = []
    for filename in filenames:
        full_path = os.path.join(repo_path, filename)
        if not os.path.isfile(full_path):
            continue
        with open(full_path, "r", encoding="utf-8") as fh:
            source = fh.read()
        parts.append(f"### File: {filename}\n```python\n{source}\n```")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Core call
# ---------------------------------------------------------------------------

def call_bob(prompt: str, workspace: str, timeout: int = 120) -> str:
    """
    Run ``bob run`` with *prompt* in headless mode and return the plain-text
    response.

    The prompt is passed via stdin rather than as a CLI argument, which
    avoids Windows quoting limits and shell-escaping issues with long prompts.

    Parameters
    ----------
    prompt:    The full prompt string to send to Bob.
    workspace: Path passed to --workspace (tells Bob where the code lives).
    timeout:   subprocess timeout in seconds (default 120).

    Returns the extracted assistant text on success.
    Raises RuntimeError on non-zero exit or missing API key.
    """
    api_key = os.environ.get("BOBSHELL_API_KEY") or os.environ.get("BOB_API_KEY")
    if not api_key:
        raise RuntimeError(
            "No Bob API key found.  Set BOBSHELL_API_KEY in orchestrator/.env"
        )

    env = os.environ.copy()
    # Bob Shell v2 reads BOB_API_KEY; set both names for safety
    env["BOB_API_KEY"] = api_key
    env["BOBSHELL_API_KEY"] = api_key

    bob_exe = find_bob_cmd()

    result = subprocess.run(
        [
            bob_exe,
            "run",
            "--accept-license",
            "--format", "json",
            "--workspace", workspace,
            # No prompt argument here — sent via stdin instead
        ],
        input=prompt,           # prompt delivered on stdin, no quoting issues
        capture_output=True,
        text=True,
        encoding="utf-8",       # force UTF-8; Windows defaults to cp1252
        env=env,
        timeout=timeout,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Bob Shell exited with code {result.returncode}.\n"
            f"stderr: {result.stderr.strip()}"
        )

    return extract_text(result.stdout)


# ---------------------------------------------------------------------------
# Shared pytest runner (used by Saboteur and Medic)
# ---------------------------------------------------------------------------

def run_pytest(test_path: str, cwd: str, timeout: int = 60) -> dict:
    """
    Execute pytest on *test_path* inside *cwd* using the current interpreter
    (``sys.executable -m pytest``) so it always runs in the same venv as the
    orchestrator — Flask and all other deps are guaranteed importable.

    Return keys
    -----------
    passed           : bool  — True only when all tests pass (exit code 0)
    collection_error : bool  — True when pytest couldn't collect/import the
                               test file (env problem, not a real bug)
    output           : str   — combined stdout + stderr from pytest

    Classification rule
    -------------------
    A genuine assertion failure always prints a ``FAILED`` line.  Any non-zero
    exit that lacks ``FAILED`` in the output is an environment/collection error.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", test_path, "-v", "--tb=short",
         "--no-header"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
        timeout=timeout,
    )

    output = (result.stdout + result.stderr).strip()
    has_real_failure = "FAILED" in output
    collection_error = result.returncode != 0 and not has_real_failure

    return {
        "passed": result.returncode == 0,
        "collection_error": collection_error,
        "output": output,
    }
