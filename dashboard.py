"""
AI Energy Dashboard - Flask app and entry point
Run: python dashboard.py
Serves the dashboard UI and API for running analyses and viewing report history.

Auth: set AIENERGY_USER / AIENERGY_PASSWORD (optional) and FLASK_SECRET_KEY for production.
"""

import hmac
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import timedelta
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
    if report_type == "meters_matrix":
        cmd.extend(["--date", str(start), "--no-browser"])
    else:
        cmd.extend(["--start", str(start), "--days", str(days), "--no-browser"])
    if report_type == "report":
        cmd.extend(["--grid-id", "1"])
    return cmd


@app.route("/")
def index():
    """Serve the dashboard SPA."""
    return render_template("dashboard.html")


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
    effective_days = 1 if report_type == "meters_matrix" else max(1, min(30, days))

    cmd = _build_analyze_command(report_type, start, effective_days)

    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
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


@app.route("/reports/<path:filename>")
def serve_report(filename):
    """Serve generated report HTML files from nodes/data."""
    if ".." in filename or filename.startswith("/"):
        return "Forbidden", 403
    return send_from_directory(DATA_DIR, filename)


def _open_browser_delayed():
    """Open browser after a short delay so the server is listening."""
    time.sleep(1.2)
    webbrowser.open("http://localhost:5050")


if __name__ == "__main__":
    os.chdir(PROJECT_ROOT)
    sys.path.insert(0, str(PROJECT_ROOT))

    port = 5050
    url = f"http://localhost:{port}"
    threading.Thread(target=_open_browser_delayed, daemon=True).start()

    print("=" * 50)
    print("  AI Energy Dashboard / 智能能源")
    print("=" * 50)
    print(f"  {url}")
    print("=" * 50)
    print("  Press Ctrl+C to stop")
    print()

    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
