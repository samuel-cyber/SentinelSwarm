"""
sandbox_runner.py — lightweight safety filter for user-submitted Python snippets.

Security model (honest assessment)
------------------------------------
This is a demo-grade safeguard, not a production-grade sandbox.  It provides
two layers of protection:

  Layer 1 — Static AST check (before any execution)
    The snippet is parsed with Python's ``ast`` module.  If it references any
    blocked import, dangerous builtin call, or dunder-escape pattern, it is
    rejected immediately — no subprocess is spawned, no Bob calls are spent.

  Layer 2 — Subprocess execution with hard limits
    If the AST check passes, the snippet is run as a separate Python subprocess
    in a fresh temporary directory, with:
      • A 5-second wall-clock timeout (subprocess.run timeout=)
      • On Linux/macOS: CPU-time and memory caps via the ``resource`` module
        set in a preexec_fn before the child process starts
      • The temp directory is deleted immediately after execution

  What this does NOT provide
    • Network isolation (we rely on the import blocklist for obvious attempts)
    • Full OS-level sandboxing (no seccomp, no namespaces, no containers)
    • Protection against all possible escape techniques
    • Blocklist completeness — an AST blocklist is a heuristic, not a proof.
      The lists below are deliberately broad (they also reject getattr/vars/
      globals and any string naming a dunder), which costs a few false
      rejections on unusual-but-harmless snippets.  Blocking a legitimate
      snippet is a cheap failure; letting a hostile one through is not.

Being explicit about this in the UI copy is intentional.
"""

import ast
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import textwrap


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

MAX_SNIPPET_BYTES = 16_384   # 16 KB — generous for a demo function
CPU_LIMIT_SECS    = 3        # hard CPU-time cap on Linux/macOS
MEM_LIMIT_BYTES   = 64 * 1024 * 1024   # 64 MB RSS cap on Linux/macOS
EXEC_TIMEOUT_SECS = 5        # wall-clock timeout for the probe subprocess


# ---------------------------------------------------------------------------
# AST blocklist
# ---------------------------------------------------------------------------

# Blocked top-level module names (import X or from X import ...)
#
# Grouped by what the module hands an attacker.  A blocklist is never complete
# — the point is to raise the cost of the obvious escapes, nothing more.
_BLOCKED_IMPORTS = frozenset({
    # OS / process control
    "os", "subprocess", "sys", "shutil", "pathlib", "signal", "mmap",
    "resource", "multiprocessing", "threading", "_thread", "ctypes",
    "pty", "atexit", "gc", "fcntl", "select", "selectors", "posix", "nt",
    "_posixsubprocess", "pwd", "grp", "spwd", "termios", "tty", "getpass",
    "codecs",

    # Import / execution machinery
    "importlib", "builtins", "__builtin__", "runpy", "code", "codeop",
    "pdb", "bdb", "imp", "zipimport", "pkgutil", "site", "sitecustomize",
    "usercustomize", "sysconfig", "traceback", "linecache", "inspect",
    # Deserialisers that execute code on load
    "pickle", "pickletools", "marshal", "shelve", "dbm", "anydbm",

    # Network
    "socket", "socketserver", "ssl", "asyncio", "asyncore", "http",
    "ftplib", "smtplib", "telnetlib", "xmlrpc", "requests", "urllib",
    "webbrowser",

    # Filesystem
    "glob", "tempfile", "fileinput", "zipfile", "tarfile", "gzip", "bz2",
    "lzma", "sqlite3", "platform",
})

# Blocked builtin call names.
#
# getattr/setattr/delattr/globals/locals/vars are pure escape primitives: they
# turn a *string* into an attribute lookup, which defeats the attribute check
# below (e.g. getattr(x, "__subclasses__") or globals()["__builtins__"]).
# Blocking them outright is deliberate — the false-reject cost is one clear
# error message, whereas a false-accept is arbitrary code execution.
_BLOCKED_CALLS = frozenset({
    "eval", "exec", "__import__", "open", "compile",
    "breakpoint", "input",
    "getattr", "setattr", "delattr", "globals", "locals", "vars",
})

# Blocked attribute names that suggest dunder/sandbox-escape attempts.
# Also checked against string constants, so the equivalent subscript form
# (obj["__class__"]) and string-argument form (getattr(x, "__class__")) are
# caught by the same list.
_BLOCKED_ATTRS = frozenset({
    "__class__", "__bases__", "__subclasses__", "__mro__",
    "__globals__", "__builtins__", "__code__", "__closure__",
    "__dict__", "__module__", "__spec__", "__loader__",
    "__reduce__", "__reduce_ex__", "__getattribute__", "__self__",
    "__func__", "__subclasshook__", "__init_subclass__",
    "f_locals", "f_globals", "f_builtins", "gi_frame", "cr_frame",
})


class SnippetValidationError(Exception):
    """Raised when a snippet fails the safety check or probe execution."""


# ---------------------------------------------------------------------------
# Layer 1: static AST analysis
# ---------------------------------------------------------------------------

class _SafetyVisitor(ast.NodeVisitor):
    """Walk the AST and raise SnippetValidationError on anything disallowed."""

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root in _BLOCKED_IMPORTS:
                raise SnippetValidationError(
                    f"Import of '{alias.name}' is not allowed."
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        root = (node.module or "").split(".")[0]
        if root in _BLOCKED_IMPORTS:
            raise SnippetValidationError(
                f"Import from '{node.module}' is not allowed."
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Direct calls: eval(...), exec(...), open(...)
        if isinstance(node.func, ast.Name):
            if node.func.id in _BLOCKED_CALLS:
                raise SnippetValidationError(
                    f"Call to '{node.func.id}()' is not allowed."
                )
        # Attribute calls: obj.__import__(...), builtins.eval(...)
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in _BLOCKED_CALLS:
                raise SnippetValidationError(
                    f"Call to '.{node.func.attr}()' is not allowed."
                )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _BLOCKED_ATTRS:
            raise SnippetValidationError(
                f"Access to '.{node.attr}' is not allowed."
            )
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # String form of the same escapes: obj["__class__"] instead of
        # obj.__class__, or the string argument to a getattr-style call.
        # Only dunder/frame names are matched (not plain words like "eval"),
        # so an innocent docstring is never tripped up.
        if isinstance(node.value, str):
            for blocked in _BLOCKED_ATTRS:
                if blocked in node.value:
                    raise SnippetValidationError(
                        f"String literal referencing '{blocked}' is not allowed."
                    )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        # Bare name form — __builtins__ is injected into every module's
        # globals, so `__builtins__["eval"]` reaches exec() without an
        # attribute access or a string literal to catch.
        if node.id in _BLOCKED_ATTRS:
            raise SnippetValidationError(
                f"Reference to '{node.id}' is not allowed."
            )
        self.generic_visit(node)


def _ast_check(code: str) -> None:
    """
    Parse *code* and walk its AST.  Raise SnippetValidationError on the first
    blocked pattern found.  Raise SnippetValidationError with a SyntaxError
    message if the code is not valid Python.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        # exc.lineno is None for some malformed inputs (e.g. null bytes)
        where = f"line {exc.lineno}" if exc.lineno else "unknown line"
        raise SnippetValidationError(
            f"Syntax error at {where}: {exc.msg}"
        )
    _SafetyVisitor().visit(tree)


# ---------------------------------------------------------------------------
# Layer 2: subprocess probe with resource limits
# ---------------------------------------------------------------------------

def _make_preexec() -> "callable | None":
    """
    Return a preexec_fn that applies CPU-time and memory limits, or None on
    platforms that don't support ``resource`` (i.e. Windows).
    """
    if platform.system() == "Windows":
        return None

    def _limit():
        try:
            import resource as _resource
            # CPU time: (soft, hard) seconds
            _resource.setrlimit(_resource.RLIMIT_CPU,
                                 (CPU_LIMIT_SECS, CPU_LIMIT_SECS))
            # Virtual memory / address space
            _resource.setrlimit(_resource.RLIMIT_AS,
                                 (MEM_LIMIT_BYTES, MEM_LIMIT_BYTES))
        except Exception:
            pass  # best-effort — don't let limit failures block the probe

    return _limit


# Minimal wrapper that imports the user module and confirms it loads cleanly
_PROBE_SCRIPT = textwrap.dedent("""\
    import importlib.util, sys, pathlib

    _path = pathlib.Path(__file__).parent / "app.py"
    _spec = importlib.util.spec_from_file_location("_snippet", _path)
    _mod  = importlib.util.module_from_spec(_spec)
    try:
        _spec.loader.exec_module(_mod)
        print("__probe_ok__")
    except Exception as exc:
        print(f"__probe_fail__: {exc}", file=sys.stderr)
        sys.exit(1)
""")


def _probe_execution(tmp_dir: str) -> None:
    """
    Run a minimal probe script in *tmp_dir* as a subprocess with resource caps
    and a hard wall-clock timeout.  Raise SnippetValidationError if the snippet
    crashes at module-load time or times out.
    """
    probe_path = os.path.join(tmp_dir, "_probe.py")
    with open(probe_path, "w", encoding="utf-8") as fh:
        fh.write(_PROBE_SCRIPT)

    try:
        result = subprocess.run(
            [sys.executable, probe_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=tmp_dir,
            timeout=EXEC_TIMEOUT_SECS,
            preexec_fn=_make_preexec(),
        )
    except subprocess.TimeoutExpired:
        raise SnippetValidationError(
            f"Snippet timed out after {EXEC_TIMEOUT_SECS} seconds."
        )

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        # Strip the internal __probe_fail__ prefix if present
        detail = detail.replace("__probe_fail__: ", "")
        raise SnippetValidationError(
            f"Snippet raised an error at load time:\n{detail}"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_snippet(code: str) -> str:
    """
    Run the two-layer safety check on *code*.

    Layer 1: static AST analysis — rejects dangerous imports/calls instantly.
    Layer 2: subprocess probe in a temp dir with CPU/memory caps and timeout.

    Returns *code* unchanged on success.
    Raises SnippetValidationError with a human-readable message on failure.
    """
    if not code or not code.strip():
        raise SnippetValidationError("Snippet is empty.")

    if len(code.encode("utf-8")) > MAX_SNIPPET_BYTES:
        raise SnippetValidationError(
            f"Snippet exceeds the {MAX_SNIPPET_BYTES // 1024} KB size limit."
        )

    # Layer 1: static check — fast, no subprocess
    _ast_check(code)

    # Layer 2: probe execution — subprocess, with limits and timeout
    tmp_dir = tempfile.mkdtemp(prefix="sentinel_probe_")
    try:
        app_path = os.path.join(tmp_dir, "app.py")
        with open(app_path, "w", encoding="utf-8") as fh:
            fh.write(code)
        _probe_execution(tmp_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return code


def save_snippet_to_tempdir(code: str) -> str:
    """
    Write validated *code* to a fresh temporary directory as ``app.py`` and
    ``app.py.bak``, and return the directory path.

    The caller owns the directory lifetime — the ``/run-snippet`` route
    deletes it in a ``finally`` block after the pipeline completes.
    """
    tmp_dir  = tempfile.mkdtemp(prefix="sentinel_snippet_")
    app_path = os.path.join(tmp_dir, "app.py")
    bak_path = app_path + ".bak"

    try:
        with open(app_path, "w", encoding="utf-8") as fh:
            fh.write(code)
        with open(bak_path, "w", encoding="utf-8") as fh:
            fh.write(code)
    except OSError:
        # Don't leak a half-written temp dir if the disk fills or perms fail
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    return tmp_dir
