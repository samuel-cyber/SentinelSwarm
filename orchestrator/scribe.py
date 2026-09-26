"""
scribe.py — Scribe subagent for Sentinel Swarm.

Given the full paper trail from Scout → Saboteur → Medic, asks Bob to
write a concise human-readable bug report card as a JSON object with
exactly four fields:

  title          (str)  — short headline for the bug
  explanation    (str)  — 2-3 sentences: what broke, why, what the fix was
  severity_score (int)  — 1-10 likelihood a real user hits this in production
  function_name  (str)  — name of the function that contained the bug

Bob is instructed to return ONLY a JSON code block.  run_scribe() parses
it, validates all four fields are present, and returns either the parsed
dict or a structured error dict — it never raises on bad Bob output.
"""

import json
import textwrap

from bob_client import call_bob, extract_code_block


REQUIRED_FIELDS = ("title", "explanation", "severity_score", "function_name")

_PROMPT = textwrap.dedent("""\
    You are a technical writer producing a concise bug report for a software team.

    ## What happened
    A software test was written to probe an edge case in a Flask application.
    The test FAILED on the original code, proving a real bug existed.
    The bug was then fixed and the same test PASSED on the fixed code.

    ## The test that exposed the bug
    ```python
    {test_code}
    ```

    ## Initial failure output (before fix)
    ```
    {initial_pytest_output}
    ```

    ## Fixed source: app.py (after fix)
    ```python
    {fixed_source}
    ```

    ## Confirmation output (after fix)
    ```
    {rerun_pytest_output}
    ```

    ## Your task
    Write a concise bug report card as a single JSON object with exactly
    these four fields:

    - "title": a short headline (≤10 words) describing the bug
    - "explanation": 2-3 plain-English sentences — what the bug was, why it
      crashed/misbehaved, and what the fix did to resolve it
    - "severity_score": an integer from 1 to 10 representing how easily a
      real end-user would trigger this bug in production (10 = trivially easy)
    - "function_name": the exact name of the Python function that had the bug

    ## Output rules — follow exactly
    - Respond with a SINGLE markdown JSON code block and nothing else.
    - The block must start with ```json on its own line.
    - The block must end with ``` on its own line.
    - Inside the block: only the JSON object, no extra keys.
    - Zero prose or explanation outside the code block.

    Example of the exact format required:
    ```json
    {{
      "title": "Crash when age field is missing from registration",
      "explanation": "The register_user function read the age field with dict.get(), which returns None when the key is absent. Comparing None < 18 raises a TypeError, crashing the endpoint with a 500 error. The fix adds an explicit None check before the comparison.",
      "severity_score": 8,
      "function_name": "register_user"
    }}
    ```
""")


def run_scribe(
    test_code: str,
    initial_pytest_output: str,
    fixed_source: str,
    rerun_pytest_output: str,
    repo_path: str,
) -> dict:
    """
    Ask Bob to write a bug report card for the proven-and-fixed bug.

    Parameters
    ----------
    test_code              : the pytest test that exposed the bug
    initial_pytest_output  : pytest output from the failing run (red)
    fixed_source           : the corrected app.py content
    rerun_pytest_output    : pytest output from the confirmation run (green)
    repo_path              : absolute path to sample_app/ (used as workspace)

    Returns a dict with keys:
      success       : bool — True if Bob returned parseable, complete JSON
      case_file     : dict with title/explanation/severity_score/function_name
                      (only present when success is True)
      raw_response  : Bob's raw text response (always present, for debugging)
      error         : error message string (only present when success is False)
    """
    prompt = _PROMPT.format(
        test_code=test_code,
        initial_pytest_output=initial_pytest_output,
        fixed_source=fixed_source,
        rerun_pytest_output=rerun_pytest_output,
    )

    raw_response = call_bob(prompt, workspace=repo_path, timeout=120)

    # Extract the JSON from inside the ```json ... ``` fence
    json_text = extract_code_block(raw_response, lang="json")

    # Parse and validate
    try:
        case_file = json.loads(json_text)
    except json.JSONDecodeError as exc:
        return {
            "success": False,
            "raw_response": raw_response,
            "error": f"Bob returned invalid JSON: {exc}",
        }

    missing = [f for f in REQUIRED_FIELDS if f not in case_file]
    if missing:
        return {
            "success": False,
            "raw_response": raw_response,
            "error": f"Missing required fields: {missing}",
        }

    # Coerce severity_score to int in case Bob returned a string
    try:
        case_file["severity_score"] = int(case_file["severity_score"])
    except (ValueError, TypeError):
        return {
            "success": False,
            "raw_response": raw_response,
            "error": "severity_score is not a valid integer",
        }

    return {
        "success": True,
        "case_file": case_file,
        "raw_response": raw_response,
    }
