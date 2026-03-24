"""
AI Energy Dashboard - Flask app and entry point
Run: python dashboard.py
Serves the dashboard UI and API for running analyses and viewing report history.

Auth: set AIENERGY_USER / AIENERGY_PASSWORD (optional) and FLASK_SECRET_KEY for production.
"""

import hmac
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parent
NODES_DIR = PROJECT_ROOT / "nodes"
DATA_DIR = NODES_DIR / "data"
MODEL_DIR = PROJECT_ROOT / "model"
MODEL_ARTIFACTS_DIR = PROJECT_ROOT / "model_artifacts"
MODEL_META_PATH = MODEL_ARTIFACTS_DIR / "model_meta.json"

# Login (override with environment variables in production)
DASHBOARD_USER = os.environ.get("AIENERGY_USER", "aienergy")
DASHBOARD_PASSWORD = os.environ.get("AIENERGY_PASSWORD", "REDACTED")

# Report type config
# value_groups: 2 = regex captures (start, days), 1 = only start (days fixed at 1 for UI)
REPORT_TYPES = {
    "grid": {
        "script": "nodes/griddata.py",
        "pattern": re.compile(r"^griddata-(\d{6})-(\d+)d\.html$"),
        "value_groups": 2,
        "label_en": "Grid",
        "label_zh": "电网",
    },
    "solar": {
        "script": "nodes/solardata.py",
        "pattern": re.compile(r"^solardata-(\d{6})-(\d+)d\.html$"),
        "value_groups": 2,
        "label_en": "Solar",
        "label_zh": "太阳能",
    },
    "meters_use": {
        "script": "nodes/endmetersuse.py",
        "pattern": re.compile(r"^endmeterdata-(\d{6})-(\d+)d\.html$"),
        "value_groups": 2,
        "label_en": "Use",
        "label_zh": "用电",
    },
    "meters_ai": {
        "script": "nodes/endmetersai.py",
        "pattern": re.compile(r"^endmetersai-(\d{6})\.html$"),
        "value_groups": 1,
        "label_en": "AI",
        "label_zh": "AI预测",
    },
    "meters_matrix": {
        "script": "nodes/endmeters.py",
        "pattern": re.compile(r"^endmeters-(\d{6})\.html$"),
        "value_groups": 1,
        "label_en": "Meters",
        "label_zh": "电表矩阵",
    },
    "meters_data": {
        "script": "nodes/endmetersdata.py",
        "pattern": re.compile(r"^endmetersdata-(\d{6})-(\d+)d\.html$"),
        "value_groups": 2,
        "label_en": "Data",
        "label_zh": "数据",
    },
    "report": {
        "script": "nodes/report.py",
        "pattern": re.compile(r"^report-(\d{6})-(\d+)d\.html$"),
        "value_groups": 2,
        "label_en": "Report",
        "label_zh": "报告",
    },
}

app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "dev-aienergy-secret-change-with-FLASK_SECRET_KEY-in-production",
)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)


_task_lock = threading.Lock()
_task_state = {
    "task_id": None,
    "type": None,
    "status": "idle",
    "progress": 0,
    "logs": [],
    "updated_at": None,
}


def _set_task_state(
    *,
    task_id=None,
    task_type=None,
    status=None,
    progress=None,
    append_log=None,
):
    with _task_lock:
        if task_id is not None:
            _task_state["task_id"] = task_id
        if task_type is not None:
            _task_state["type"] = task_type
        if status is not None:
            _task_state["status"] = status
        if progress is not None:
            _task_state["progress"] = int(max(0, min(100, progress)))
        if append_log:
            _task_state["logs"].append(str(append_log))
            _task_state["logs"] = _task_state["logs"][-200:]
        _task_state["updated_at"] = int(time.time())


def _snapshot_task_state():
    with _task_lock:
        return {
            "task_id": _task_state["task_id"],
            "type": _task_state["type"],
            "status": _task_state["status"],
            "progress": _task_state["progress"],
            "logs": list(_task_state["logs"]),
            "updated_at": _task_state["updated_at"],
        }


def _credentials_ok(username: str, password: str) -> bool:
    """Constant-time compare for password when lengths match."""
    try:
        u = username.encode("utf-8")
        p = password.encode("utf-8")
        eu = DASHBOARD_USER.encode("utf-8")
        ep = DASHBOARD_PASSWORD.encode("utf-8")
    except Exception:
        return False
    if len(u) != len(eu) or len(p) != len(ep):
        return False
    return hmac.compare_digest(u, eu) and hmac.compare_digest(p, ep)


def _safe_next_url(target: str) -> str:
    if not target:
        return "/"
    if not target.startswith("/") or target.startswith("//"):
        return "/"
    return target


@app.before_request
def require_login():
    if request.endpoint in ("login", "static"):
        return None
    if session.get("logged_in"):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "Unauthorized", "login": url_for("login")}), 401
    return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(_safe_next_url(request.args.get("next") or "/"))
    if request.method == "POST":
        user = (request.form.get("username") or "").strip()
        pw = request.form.get("password") or ""
        nxt = _safe_next_url(request.form.get("next") or "")
        if _credentials_ok(user, pw):
            session["logged_in"] = True
            session.permanent = True
            return redirect(nxt or "/")
        flash("Invalid username or password / 用户名或密码错误", "error")
        return render_template("login.html", next=nxt or request.args.get("next", ""))
    return render_template("login.html", next=request.args.get("next", ""))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


def get_history(report_type: str):
    """Scan nodes/data for report files matching the type."""
    if report_type not in REPORT_TYPES:
        return []
    config = REPORT_TYPES[report_type]
    patterns = [config["pattern"]]
    # Legacy ping reports before rename (endmetersping.py → endmetersdata.py)
    if report_type == "meters_data":
        patterns.append(re.compile(r"^endmetersping-(\d{6})-(\d+)d\.html$"))
    value_groups = config.get("value_groups", 2)
    results = []
    seen_paths = set()
    if not DATA_DIR.exists():
        return results
    for f in DATA_DIR.iterdir():
        if not f.is_file() or not f.suffix == ".html":
            continue
        if f.name in seen_paths:
            continue
        for pattern in patterns:
            m = pattern.match(f.name)
            if not m:
                continue
            if value_groups == 1:
                start = m.group(1)
                days = 1
            else:
                start, days = m.group(1), int(m.group(2))
            label = f"{config['label_en']} {start} {days}d"
            if value_groups == 1:
                label = f"{config['label_en']} {start}"
            results.append({
                "label": label,
                "path": f.name,
                "start": start,
                "days": days,
            })
            seen_paths.add(f.name)
            break
    results.sort(key=lambda x: (x["start"], -x["days"]), reverse=True)
    return results


def _expected_filename(report_type: str, start: str, days: int) -> str:
    if report_type == "meters_matrix":
        return f"endmeters-{start}.html"
    if report_type == "meters_ai":
        return f"endmetersai-{start}.html"
    if report_type == "meters_data":
        return f"endmetersdata-{start}-{days}d.html"
    if report_type == "meters_use":
        return f"endmeterdata-{start}-{days}d.html"
    if report_type == "grid":
        return f"griddata-{start}-{days}d.html"
    if report_type == "solar":
        return f"solardata-{start}-{days}d.html"
    if report_type == "report":
        return f"report-{start}-{days}d.html"
    raise ValueError(f"Unknown type {report_type}")


def _build_analyze_command(report_type: str, start: str, days: int) -> list:
    script_path = PROJECT_ROOT / REPORT_TYPES[report_type]["script"]
    cmd = [sys.executable, str(script_path)]
    if report_type in {"meters_matrix", "meters_ai"}:
        cmd.extend(["--date", str(start), "--no-browser"])
    else:
        cmd.extend(["--start", str(start), "--days", str(days), "--no-browser"])
    if report_type == "report":
        cmd.extend(["--grid-id", "1"])
    return cmd


def _resolve_model_artifacts_dir(value: str | None) -> Path:
    txt = str(value or "").strip()
    if not txt:
        return MODEL_ARTIFACTS_DIR
    candidate = Path(txt)
    if not candidate.is_absolute():
        candidate = (PROJECT_ROOT / candidate).resolve()
    return candidate.resolve()


@app.route("/")
def index():
    """Serve the dashboard SPA."""
    return render_template("dashboard.html")


@app.route("/ai-imputer")
def ai_imputer():
    """Serve manual AI imputation control page."""
    return render_template("ai_imputer.html")


@app.route("/api/history")
def api_history():
    """Return history of generated reports for the given type."""
    report_type = request.args.get("type", "grid")
    if report_type not in REPORT_TYPES:
        return jsonify({"error": "Invalid type", "history": []}), 400
    history = get_history(report_type)
    return jsonify({"history": history})


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    """Run the analysis script for the given type, start date, and days."""
    data = request.get_json() or {}
    report_type = data.get("type", "grid")
    start = data.get("start", "260301")
    days = int(data.get("days", 7) or 7)

    if report_type not in REPORT_TYPES:
        return jsonify({"success": False, "error": "Invalid report type"}), 400

    script_path = PROJECT_ROOT / REPORT_TYPES[report_type]["script"]
    if not script_path.exists():
        return jsonify({"success": False, "error": f"Script not found: {script_path}"}), 500

    # Single-day matrix ignores days in script; still use start as YYMMDD
    effective_days = 1 if report_type in {"meters_matrix", "meters_ai"} else max(1, min(30, days))

    cmd = _build_analyze_command(report_type, start, effective_days)

    sub_env = os.environ.copy()
    pr = str(PROJECT_ROOT)
    sub_env["PYTHONPATH"] = (
        pr + os.pathsep + sub_env["PYTHONPATH"]
        if sub_env.get("PYTHONPATH")
        else pr
    )
    if report_type == "meters_ai":
        model_dir = _resolve_model_artifacts_dir(data.get("model_dir"))
        if not model_dir.exists() or not model_dir.is_dir():
            return jsonify({"success": False, "error": f"Model directory not found: {model_dir}"}), 400
        sub_env["MODEL_ARTIFACT_DIR"] = str(model_dir)
    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=sub_env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            return jsonify({
                "success": False,
                "error": result.stderr or result.stdout or "Unknown error",
            }), 500

        filename = _expected_filename(report_type, start, effective_days)
        report_path = DATA_DIR / filename
        if not report_path.exists():
            return jsonify({
                "success": False,
                "error": f"Report was not generated: {filename}",
            }), 500

        return jsonify({
            "success": True,
            "path": filename,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "error": "Analysis timed out"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/model_status")
def api_model_status():
    """Return latest training metrics from model metadata file."""
    if not MODEL_META_PATH.exists():
        return jsonify({
            "exists": False,
            "message": "Model has not been trained yet.",
            "last_trained": None,
            "accuracy": None,
            "model_type": None,
            "node_id": None,
        })
    try:
        with MODEL_META_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        return jsonify({"error": f"Failed reading model metadata: {exc}"}), 500

    runs = data.get("runs", [])
    latest = runs[-1] if runs else {}
    avg_accuracy = None
    valid_accuracies = [r.get("accuracy_pct") for r in runs if isinstance(r.get("accuracy_pct"), (int, float))]
    if valid_accuracies:
        avg_accuracy = round(sum(valid_accuracies) / len(valid_accuracies), 2)
    return jsonify({
        "exists": True,
        "last_trained": latest.get("last_trained_timestamp"),
        "accuracy": latest.get("accuracy_pct"),
        "avg_accuracy": avg_accuracy,
        "model_type": latest.get("model_type"),
        "node_id": latest.get("node_id"),
        "metrics_count": len(runs),
    })


def _parse_iso_date(value: str, field_name: str) -> datetime:
    txt = str(value or "").strip()
    if not txt:
        raise ValueError(f"{field_name} is required")
    try:
        return datetime.strptime(txt, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{field_name} must be in YYYY-MM-DD format") from exc


def _parse_start_end_dates(payload: dict) -> tuple[datetime, datetime]:
    start_dt = _parse_iso_date(payload.get("start_date"), "start_date")
    end_dt = _parse_iso_date(payload.get("end_date"), "end_date")
    if end_dt < start_dt:
        raise ValueError("end_date must be >= start_date")
    return start_dt, end_dt


def _train_job(task_id: str, node_ids: list[int], start_dt, end_dt):
    _set_task_state(task_id=task_id, task_type="train", status="running", progress=1, append_log="Starting model training...")
    try:
        from model.ml_predictor import NodeAwareMLPredictor
        from model.topology_engine import TopologyEngine

        topology = TopologyEngine(PROJECT_ROOT / "relation.json")
        predictor = NodeAwareMLPredictor(topology=topology)
        _set_task_state(append_log=f"Training range: {start_dt.date()} -> {end_dt.date()}", progress=3)
        predictor.train_models(
            node_ids=node_ids,
            start_date=start_dt.strftime("%Y-%m-%d"),
            end_date=end_dt.strftime("%Y-%m-%d"),
        )
        _set_task_state(progress=100, status="completed", append_log="Training workflow completed.")
    except Exception as exc:
        _set_task_state(status="failed", progress=100, append_log=f"Training task failed: {exc}")


def _impute_job(task_id: str, node_id: int, start_dt, end_dt):
    _set_task_state(task_id=task_id, task_type="impute", status="running", progress=1, append_log=f"Starting Tier 1 Deduction for Node {node_id}...")
    try:
        from model.db_handler import DBHandler
        from model.imputer import GridImputer
        from model.topology_engine import TopologyEngine

        topology = TopologyEngine(PROJECT_ROOT / "relation.json")
        db = DBHandler(interval_minutes=15)
        imputer = GridImputer(topology=topology, db_handler=db)

        _set_task_state(progress=20, append_log="Fetching source timeseries...")
        src = db.fetch_timeseries(node_id, start_dt, end_dt, metric="power")
        _set_task_state(progress=45, append_log="Executing Tier 1 -> Tier 2 -> Tier 3...")
        out = imputer.fill_missing(src, node_id=node_id, date_range=(start_dt, end_dt))
        missing_before = int(src["Pt"].isna().sum()) if "Pt" in src.columns else 0
        missing_after = int(out["Pt"].isna().sum()) if "Pt" in out.columns else 0
        recovered = max(0, missing_before - missing_after)
        _set_task_state(progress=100, status="completed", append_log=f"Imputation complete. Filled {recovered} points.")
    except Exception as exc:
        _set_task_state(status="failed", progress=100, append_log=f"Imputation task failed: {exc}")


@app.route("/api/train", methods=["POST"])
def api_train():
    """Trigger async model training."""
    payload = request.get_json(silent=True) or {}
    nodes = payload.get("node_ids") or [29, 30, 31, 56]
    if not isinstance(nodes, list) or not nodes:
        return jsonify({"success": False, "error": "node_ids must be a non-empty list"}), 400
    try:
        node_ids = [int(x) for x in nodes]
    except Exception:
        return jsonify({"success": False, "error": "node_ids must contain integers"}), 400

    try:
        start_dt, end_dt = _parse_start_end_dates(payload)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    task_id = str(uuid.uuid4())
    _set_task_state(task_id=task_id, task_type="train", status="queued", progress=0, append_log="Training task queued.")
    thread = threading.Thread(target=_train_job, args=(task_id, node_ids, start_dt, end_dt), daemon=True)
    thread.start()
    return jsonify({"success": True, "task_id": task_id})


@app.route("/api/impute", methods=["POST"])
def api_impute():
    """Trigger async imputation for one node and date range."""
    payload = request.get_json(silent=True) or {}
    node_raw = payload.get("node_id")
    if node_raw is None:
        return jsonify({"success": False, "error": "node_id is required"}), 400
    try:
        node_id = int(node_raw)
    except Exception:
        return jsonify({"success": False, "error": "node_id must be an integer"}), 400
    try:
        start_dt, end_dt = _parse_start_end_dates(payload)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    task_id = str(uuid.uuid4())
    _set_task_state(task_id=task_id, task_type="impute", status="queued", progress=0, append_log=f"Impute task queued for node {node_id}.")
    thread = threading.Thread(target=_impute_job, args=(task_id, node_id, start_dt, end_dt), daemon=True)
    thread.start()
    return jsonify({"success": True, "task_id": task_id})


@app.route("/api/progress")
def api_progress():
    """Return latest background task progress for polling clients."""
    task_id = request.args.get("task_id")
    snap = _snapshot_task_state()
    if task_id and snap.get("task_id") != task_id:
        return jsonify({
            "task_id": task_id,
            "status": "unknown",
            "progress": 0,
            "logs": [],
        })
    return jsonify(snap)


@app.route("/reports/<path:filename>")
def serve_report(filename):
    """Serve generated report HTML files from nodes/data."""
    if ".." in filename or filename.startswith("/"):
        return "Forbidden", 403
    return send_from_directory(DATA_DIR, filename)


def _open_browser_delayed():
    """Open browser after a short delay so the server is listening."""
    time.sleep(1.2)
    p = int(os.environ.get("PORT", "5050"))
    webbrowser.open(f"http://localhost:{p}")


if __name__ == "__main__":
    os.chdir(PROJECT_ROOT)
    sys.path.insert(0, str(PROJECT_ROOT))

    port = int(os.environ.get("PORT", "5050"))
    url = f"http://localhost:{port}"
    if not os.environ.get("FLASK_SKIP_BROWSER"):
        threading.Thread(target=_open_browser_delayed, daemon=True).start()

    print("=" * 50)
    print("  AI Energy Dashboard / 智能能源")
    print("=" * 50)
    print(f"  {url}")
    print("=" * 50)
    print("  Press Ctrl+C to stop")
    print()

    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
