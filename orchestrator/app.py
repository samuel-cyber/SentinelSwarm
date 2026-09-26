"""
Sentinel Swarm — orchestrator backend.

Run locally:
    cd orchestrator
    pip install -r requirements.txt
    flask --app app run --port 5001

Routes:
    GET  /                         — judge-facing frontend
    POST /run-swarm?scenario=<id>  — full chain: Scout→Saboteur→Medic→Scribe
    POST /reset-demo?scenario=<id> — restore scenario app.py from .bak
    POST /run-snippet              — validate+run pipeline on user-pasted code

Dev-only routes (require SENTINEL_DEV_ROUTES=1, unauthenticated, spend API
credits — never enable them on a public deployment):
    GET  /test-scout               — smoke-test Scout only
    GET  /test-saboteur            — Scout→Saboteur→pytest
    GET  /test-medic               — Scout→Saboteur→Medic
"""

import functools
import os
import shutil
import tempfile
from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv

from scout import run_scout
from saboteur import run_saboteur
from medic import run_medic
from scribe import run_scribe
from sandbox_runner import (
    SnippetValidationError,
    save_snippet_to_tempdir,
    validate_snippet,
)

# ---------------------------------------------------------------------------
# Runtime tput stub
#
# Bob Shell's install chain shells out to `tput` for terminal colors, and
# Render's containers ship no terminfo tools — `tput` is simply not there. At
# build time that aborts the build (see render-build.sh); at runtime it would
# abort a live /run-swarm in front of judges, which is worse.  The build
# container and the runtime container are separate environments, so the stub
# created at build time does not exist here and has to be recreated.
#
# Prepending to os.environ["PATH"] reaches both places that matter:
#   * shutil.which() inside bob_client.find_bob_cmd(), and
#   * the subprocess env, which call_bob builds via os.environ.copy().
#
# This runs at import — before Flask serves a request and before any Bob call.
# POSIX only: on Windows (local dev) there is no tput to shadow, and the stub
# is a shell script, so we skip rather than write an unusable file.
# ---------------------------------------------------------------------------

_TPUT_STUB_SOURCE = """#!/bin/sh
# No-op tput. cols/lines are handled because their OUTPUT is used as a value;
# returning empty there can break arithmetic downstream. Every other
# subcommand (setaf, sgr0, bold, ...) is a color call whose output is
# discarded, so printing nothing and exiting 0 is the correct behavior.
case "$1" in
  cols)  echo 80 ;;
  lines) echo 24 ;;
  *)     exit 0 ;;
esac
"""


def _install_tput_stub() -> str | None:
    """
    Put a no-op ``tput`` at the front of PATH for this process and every
    subprocess it spawns.  Returns the stub directory, or None if nothing
    was installed.

    Best-effort by design: a missing stub must never be the reason the app
    fails to start, so filesystem errors are swallowed rather than raised.
    """
    if os.name != "posix":
        return None

    stub_dir  = os.path.join(tempfile.gettempdir(), "sentinel-stubbin")
    stub_path = os.path.join(stub_dir, "tput")

    try:
        os.makedirs(stub_dir, exist_ok=True)
        with open(stub_path, "w", encoding="utf-8") as fh:
            fh.write(_TPUT_STUB_SOURCE)
        os.chmod(stub_path, 0o755)
    except OSError:
        return None

    # Guard against prepending twice if this module is imported more than once
    # in the same process (and against a re-import under gunicorn's reloader).
    current = os.environ.get("PATH", "")
    if stub_dir not in current.split(os.pathsep):
        os.environ["PATH"] = stub_dir + os.pathsep + current

    return stub_dir


_TPUT_STUB_DIR = _install_tput_stub()

load_dotenv()

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Scenario registry — the ONLY permitted targets
# Paths are relative to the workspace root (one level above orchestrator/).
# ---------------------------------------------------------------------------

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

SCENARIOS = {
    "user_api": {
        "path":  os.path.join(_ROOT, "scenarios", "user_api"),
        "label": "User API",
        "description": "User lookup & registration bugs (type mismatch, missing None-guard)",
    },
    "pagination": {
        "path":  os.path.join(_ROOT, "scenarios", "pagination"),
        "label": "Pagination",
        "description": "Search & pagination bugs (off-by-one, NoneType attribute error)",
    },
    "payments": {
        "path":  os.path.join(_ROOT, "scenarios", "payments"),
        "label": "Payments",
        "description": "Payment processing bugs (wrong discount formula, validation order)",
    },
}

DEFAULT_SCENARIO = "user_api"


def _resolve_scenario(scenario_id: str | None) -> tuple[str, dict]:
    """
    Validate *scenario_id* against the allow-list and return (id, meta).
    Falls back to DEFAULT_SCENARIO if None or unknown — never accepts
    arbitrary paths from user input.
    """
    sid = scenario_id if scenario_id in SCENARIOS else DEFAULT_SCENARIO
    return sid, SCENARIOS[sid]


def _reset_scenario(scenario_path: str) -> bool:
    """
    Restore app.py from app.py.bak and delete test_generated.py.
    Returns True if a backup was found and restored.
    """
    app_py    = os.path.join(scenario_path, "app.py")
    bak_py    = app_py + ".bak"
    test_py   = os.path.join(scenario_path, "test_generated.py")

    restored = False
    if os.path.isfile(bak_py):
        shutil.copy2(bak_py, app_py)
        restored = True
    if os.path.isfile(test_py):
        os.remove(test_py)
    return restored


def _json_errors(fn):
    """
    Turn an unexpected exception inside a pipeline route into a JSON body.

    Every route here is consumed by the frontend with ``await res.json()``.
    Without this, an uncaught error (Bob CLI missing, no API key, pytest
    timeout) makes Flask return an HTML traceback page, which the browser
    reports as a confusing JSON parse error instead of the real cause.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except SnippetValidationError as exc:
            return jsonify({
                "status": "error",
                "stage":  "validation",
                "detail": str(exc),
            }), 400
        except Exception as exc:                      # noqa: BLE001 — last resort
            app.logger.exception("Swarm pipeline failed")
            return jsonify({
                "status": "error",
                "stage":  "pipeline",
                "detail": f"{type(exc).__name__}: {exc}",
            }), 500
    return wrapper


def _build_trail(scout_summary: str, sab: dict, med: dict | None = None) -> dict:
    """
    Assemble the payload the frontend uses to render all four panels.

    Every verdict that has panel-worthy output includes this under ``trail``,
    so the browser never has to guess which envelope shape it received.
    Pass *med* only once the Medic step has actually run.
    """
    trail = {
        "scout_summary":         scout_summary,
        "test_code":             sab["test_code"],
        "test_path":             sab["test_path"],
        "initial_pytest_output": sab["pytest_output"],
    }
    if med is not None:
        trail.update({
            "fixed_source":        med["fixed_source"],
            "rerun_pytest_output": med["rerun_output"],
            "backup_path":         med["backup_path"],
        })
    return trail


# ---------------------------------------------------------------------------
# / — serve the frontend
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", scenarios=SCENARIOS)


# ---------------------------------------------------------------------------
# /reset-demo  (POST ?scenario=<id>)
# ---------------------------------------------------------------------------

@app.route("/reset-demo", methods=["POST"])
def reset_demo():
    sid, meta = _resolve_scenario(request.args.get("scenario"))
    restored  = _reset_scenario(meta["path"])
    return jsonify({
        "status":   "ok",
        "scenario": sid,
        "restored": restored,
        "message":  "Restored from backup." if restored else "No backup found.",
    })


# ---------------------------------------------------------------------------
# /run-swarm  (POST ?scenario=<id>)
#
# POST, not GET: this resets the scenario on disk and spends four real Bob
# API calls, so it must not be reachable by a link prefetch, a crawler, or a
# browser address-bar autocomplete against the public demo URL.
# ---------------------------------------------------------------------------

@app.route("/run-swarm", methods=["POST"])
@_json_errors
def run_swarm():
    sid, meta    = _resolve_scenario(request.args.get("scenario"))
    scenario_path = meta["path"]

    # Always start from the known-buggy state
    _reset_scenario(scenario_path)

    # ── Step 1: Scout ────────────────────────────────────────────────────────
    scout_summary = run_scout(scenario_path)

    # ── Step 2: Saboteur ─────────────────────────────────────────────────────
    sab = run_saboteur(scout_summary, scenario_path)

    if sab["collection_error"]:
        return jsonify({
            "status":   "error",
            "scenario": sid,
            "stage":    "saboteur",
            "detail":   "pytest could not collect the generated test",
            "pytest_output": sab["pytest_output"],
        }), 500

    if sab["passed"]:
        return jsonify({
            "status":   "ok",
            "scenario": sid,
            "verdict":  "no_bug_found",
            "scout_summary": scout_summary,
            "test_code":     sab["test_code"],
            "pytest_output": sab["pytest_output"],
            "trail":         _build_trail(scout_summary, sab),
        })

    # ── Step 3: Medic ─────────────────────────────────────────────────────────
    med = run_medic(sab["test_code"], sab["pytest_output"], scenario_path)

    if not med["fix_confirmed"]:
        return jsonify({
            "status":   "ok",
            "scenario": sid,
            "verdict":  "fix_failed",
            "scout_summary":        scout_summary,
            "test_code":            sab["test_code"],
            "initial_pytest_output": sab["pytest_output"],
            "fixed_source":         med["fixed_source"],
            "rerun_pytest_output":  med["rerun_output"],
            "trail":                _build_trail(scout_summary, sab, med),
        })

    # ── Step 4: Scribe ────────────────────────────────────────────────────────
    scr = run_scribe(
        test_code=sab["test_code"],
        initial_pytest_output=sab["pytest_output"],
        fixed_source=med["fixed_source"],
        rerun_pytest_output=med["rerun_output"],
        repo_path=scenario_path,
    )

    return jsonify({
        "status":   "ok",
        "scenario": sid,
        "verdict":  "fix_confirmed",

        # Scribe case file
        "case_file":      scr.get("case_file") if scr["success"] else None,
        "scribe_success": scr["success"],
        "scribe_error":   scr.get("error"),

        # Full trail for frontend panels
        "trail": _build_trail(scout_summary, sab, med),
    })


# ---------------------------------------------------------------------------
# /run-snippet  (POST, JSON body: {"code": "<python source>"})
#
# Security flow:
#   1. Validate snippet against the local safety filter in sandbox_runner
#      (static AST blocklist, then a resource-capped probe subprocess)
#   2. On validation failure → return 400 with the error, no Bob calls spent
#   3. On success → save to tmp dir, run full Scout→Saboteur→Medic→Scribe
# ---------------------------------------------------------------------------

@app.route("/run-snippet", methods=["POST"])
@_json_errors
def run_snippet():
    data = request.get_json(force=True, silent=True) or {}
    code = data.get("code", "")

    if not code or not code.strip():
        return jsonify({"status": "error", "detail": "No code provided."}), 400

    # ── Step 0: Sandbox validation (static AST blocklist + capped probe) ──
    try:
        validate_snippet(code)
    except SnippetValidationError as exc:
        return jsonify({
            "status":  "error",
            "stage":   "validation",
            "detail":  str(exc),
        }), 400

    # ── Write snippet to a clean temp dir ─────────────────────────────────
    snippet_path = save_snippet_to_tempdir(code)

    try:
        # Reuse the exact same chain as /run-swarm
        # Auto-reset is implicit — save_snippet_to_tempdir always starts fresh

        # Step 1: Scout
        scout_summary = run_scout(snippet_path)

        # Step 2: Saboteur
        sab = run_saboteur(scout_summary, snippet_path)

        if sab["collection_error"]:
            return jsonify({
                "status":  "error",
                "stage":   "saboteur",
                "detail":  "pytest could not collect the generated test",
                "pytest_output": sab["pytest_output"],
            }), 500

        if sab["passed"]:
            return jsonify({
                "status":  "ok",
                "verdict": "no_bug_found",
                "scout_summary": scout_summary,
                "test_code":     sab["test_code"],
                "pytest_output": sab["pytest_output"],
                "trail":         _build_trail(scout_summary, sab),
            })

        # Step 3: Medic
        med = run_medic(sab["test_code"], sab["pytest_output"], snippet_path)

        if not med["fix_confirmed"]:
            return jsonify({
                "status":  "ok",
                "verdict": "fix_failed",
                "scout_summary":         scout_summary,
                "test_code":             sab["test_code"],
                "initial_pytest_output": sab["pytest_output"],
                "fixed_source":          med["fixed_source"],
                "rerun_pytest_output":   med["rerun_output"],
                "trail":                 _build_trail(scout_summary, sab, med),
            })

        # Step 4: Scribe
        scr = run_scribe(
            test_code=sab["test_code"],
            initial_pytest_output=sab["pytest_output"],
            fixed_source=med["fixed_source"],
            rerun_pytest_output=med["rerun_output"],
            repo_path=snippet_path,
        )

        return jsonify({
            "status":  "ok",
            "verdict": "fix_confirmed",
            "case_file":      scr.get("case_file") if scr["success"] else None,
            "scribe_success": scr["success"],
            "scribe_error":   scr.get("error"),
            "trail":          _build_trail(scout_summary, sab, med),
        })

    finally:
        # Clean up the temp dir whether the run succeeded or not
        shutil.rmtree(snippet_path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Dev/smoke-test routes — DISABLED unless SENTINEL_DEV_ROUTES is set
#
# Each of these spends real Bob API calls and none of them is authenticated,
# so leaving them live on a public demo URL lets anyone who guesses the path
# drain the API quota.  Enable them locally only:
#
#     SENTINEL_DEV_ROUTES=1 flask --app app run --port 5001
# ---------------------------------------------------------------------------

_DEV_ROUTES_ENABLED = (
    os.environ.get("SENTINEL_DEV_ROUTES", "").strip().lower()
    in {"1", "true", "yes", "on"}
)

if _DEV_ROUTES_ENABLED:

    def _get_dev_scenario():
        return _resolve_scenario(request.args.get("scenario"))[1]["path"]

    @app.route("/test-scout")
    @_json_errors
    def test_scout():
        path = _get_dev_scenario()
        return jsonify({"status": "ok", "scout_summary": run_scout(path)})

    @app.route("/test-saboteur")
    @_json_errors
    def test_saboteur():
        path    = _get_dev_scenario()
        summary = run_scout(path)
        result  = run_saboteur(summary, path)
        verdict = ("error" if result["collection_error"]
                   else "no_bug_found" if result["passed"]
                   else "bug_found")
        return jsonify({"status": "ok", "verdict": verdict, **result})

    @app.route("/test-medic")
    @_json_errors
    def test_medic():
        path    = _get_dev_scenario()
        summary = run_scout(path)
        sab     = run_saboteur(summary, path)
        if sab["collection_error"]:
            return jsonify({"status": "error", **sab}), 500
        if sab["passed"]:
            return jsonify({"status": "ok", "verdict": "no_bug_found", **sab})
        med = run_medic(sab["test_code"], sab["pytest_output"], path)
        return jsonify({
            "status":  "ok",
            "verdict": "fix_confirmed" if med["fix_confirmed"] else "fix_failed",
            **sab, **med,
        })


if __name__ == "__main__":
    app.run(debug=True, port=5001)
