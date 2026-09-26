"""
saboteur.py — Saboteur subagent for Sentinel Swarm.

Given the Scout's function summary and the actual source, asks Bob to:
  1. Pick the ONE function most likely to harbour an edge-case bug.
  2. Write a single runnable pytest test that targets a specific edge case
     (empty input, None, boundary value, wrong type, etc.).
  3. Return ONLY the raw Python test code — no prose, no markdown fences.

The returned code is written to test_generated.py inside the scenario
directory and then executed with pytest.  The caller receives the test
code, the pass/fail result, and the full pytest output.
"""

import os
import textwrap

from bob_client import call_bob, extract_code_block, read_source_files, run_pytest


TARGET_FILES = ["app.py"]

_PROMPT = textwrap.dedent("""\
    You are a software tester specialising in edge-case bugs.

    ## Function summary
    {scout_summary}

    ## Source code
    {file_contents}

    ## Instructions
    1. Pick the ONE function most likely to crash on a tricky input
       (e.g. None, missing key, wrong type, boundary integer).
    2. Write a single pytest test that calls that function with the
       edge-case input.  The test MUST FAIL on the current code (proving
       a bug) and PASS after a correct fix.
    3. Import the function directly: from app import <function_name>
       (the file will be saved in the same directory as app.py).

    ## Output rules — follow exactly
    - Respond with a SINGLE markdown Python code block and nothing else.
    - The block must start with ```python on its own line.
    - The block must end with ``` on its own line.
    - Inside the block: imports first, then exactly one function named test_<something>.
    - Zero prose, zero explanation, zero comments outside the code block.

    Example of the exact format required:
    ```python
    import pytest
    from app import some_function

    def test_some_function_edge_case():
        result = some_function(None)
        assert result is not None
    ```
""")



def run_saboteur(scout_summary: str, repo_path: str) -> dict:
    """
    Ask Bob to write a pytest test targeting the most bug-prone function,
    save it to test_generated.py in that directory, run it, and return results.

    Returns a dict with keys:
      test_code   — the raw Python test code Bob produced
      test_path   — absolute path where the test was saved
      passed      — bool: True if pytest exit code is 0
      pytest_output — full combined stdout+stderr from pytest
    """
    file_contents = read_source_files(repo_path, TARGET_FILES)
    if not file_contents:
        raise RuntimeError(f"No readable source files found in {repo_path!r}")

    prompt = _PROMPT.format(
        scout_summary=scout_summary,
        file_contents=file_contents,
    )

    raw_response = call_bob(prompt, workspace=repo_path, timeout=180)
    test_code = extract_code_block(raw_response)

    # Save the generated test next to the sample app so pytest can import it
    test_path = os.path.join(repo_path, "test_generated.py")
    with open(test_path, "w", encoding="utf-8") as fh:
        fh.write(test_code + "\n")

    # Run pytest against the generated test file
    pytest_result = run_pytest(test_path, repo_path)

    return {
        "test_code": test_code,
        "test_path": test_path,
        "passed": pytest_result["passed"],
        "collection_error": pytest_result["collection_error"],
        "pytest_output": pytest_result["output"],
    }
