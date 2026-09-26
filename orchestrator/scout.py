"""
scout.py — Scout subagent for Sentinel Swarm.

Reads the files in *repo_path* and asks Bob to summarise every function in
plain English, one sentence each.  The summary is later handed to the
Saboteur so it can pick a target.
"""

import textwrap

from bob_client import call_bob, read_source_files


TARGET_FILES = ["app.py"]

_PROMPT = textwrap.dedent("""\
    You are a code analyst. I will show you the source files from a small
    Python Flask application.  Produce a concise plain-English summary of
    every function and route in the code.

    Rules:
    - One sentence per function / route.
    - Format each line as:  `function_name` — what it does.
    - Do NOT suggest fixes, flag problems, or include any preamble.
    - Output only the bullet list, nothing else.

    Source files:

    {file_contents}

    Provide the summary now.
""")


def run_scout(repo_path: str) -> str:
    """
    Return a plain-English bullet-list summary of every function in the
    sample app, produced by Bob Shell.
    """
    file_contents = read_source_files(repo_path, TARGET_FILES)
    if not file_contents:
        raise RuntimeError(f"No readable source files found in {repo_path!r}")

    prompt = _PROMPT.format(file_contents=file_contents)
    return call_bob(prompt, workspace=repo_path)
