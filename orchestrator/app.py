"""
Sentinel Swarm — orchestrator backend.

Run locally:
    cd orchestrator
    pip install -r requirements.txt
    flask --app app run --port 5001

Routes:
    GET  /                         — judge-facing frontend
    GET  /run-swarm?scenario=<id>  — full chain: Scout→Saboteur→Medic→Scribe
    POST /reset-demo?scenario=<id> — restore scenario app.py from .bak
    POST /run-snippet               — validate+run pipeline on user-pasted code
    GET  /test-scout               — smoke-test Scout only
    GET  /test-saboteur            — Scout→Saboteur→pytest
    GET  /test-medic               — Scout→Saboteur→Medic
"""

import os
import shutil
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
# /run-swarm  (GET ?scenario=<id>)
# ---------------------------------------------------------------------------

@app.route("/run-swarm")
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
        "trail": {
            "scout_summary":         scout_summary,
            "test_code":             sab["test_code"],
            "test_path":             sab["test_path"],
            "initial_pytest_output": sab["pytest_output"],
            "fixed_source":          med["fixed_source"],
            "rerun_pytest_output":   med["rerun_output"],
            "backup_path":           med["backup_path"],
        },
    })


# ---------------------------------------------------------------------------
# /run-snippet  (POST, JSON body: {"code": "<python source>"})
#
# Security flow:
#   1. Validate snippet via Judge0 CE (remote sandbox — never exec'd locally)
#   2. On validation failure → return 400 with the error, no Bob calls spent
#   3. On success → save to tmp dir, run full Scout→Saboteur→Medic→Scribe
# ---------------------------------------------------------------------------

@app.route("/run-snippet", methods=["POST"])
def run_snippet():
    import shutil as _shutil  # already imported at top, but explicit here

    data = request.get_json(force=True, silent=True) or {}
    code = data.get("code", "")

    if not code or not code.strip():
        return jsonify({"status": "error", "detail": "No code provided."}), 400

    # ── Step 0: Sandbox validation via Judge0 (remote, never local exec) ──
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
            "trail": {
                "scout_summary":         scout_summary,
                "test_code":             sab["test_code"],
                "test_path":             sab["test_path"],
                "initial_pytest_output": sab["pytest_output"],
                "fixed_source":          med["fixed_source"],
                "rerun_pytest_output":   med["rerun_output"],
                "backup_path":           med["backup_path"],
            },
        })

    finally:
        # Clean up the temp dir whether the run succeeded or not
        _shutil.rmtree(snippet_path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Dev/smoke-test routes
# ---------------------------------------------------------------------------

def _get_dev_scenario():
    return _resolve_scenario(request.args.get("scenario"))[1]["path"]


@app.route("/test-scout")
def test_scout():
    path = _get_dev_scenario()
    return jsonify({"status": "ok", "scout_summary": run_scout(path)})


@app.route("/test-saboteur")
def test_saboteur():
    path    = _get_dev_scenario()
    summary = run_scout(path)
    result  = run_saboteur(summary, path)
    verdict = ("error" if result["collection_error"]
               else "no_bug_found" if result["passed"]
               else "bug_found")
    return jsonify({"status": "ok", "verdict": verdict, **result})


@app.route("/test-medic")
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
